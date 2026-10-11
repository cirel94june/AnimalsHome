import asyncio
import tempfile
import unittest
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch


class SessionsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from billiards.service import BilliardsService
        self.tmp = tempfile.TemporaryDirectory()
        self.service = BilliardsService(Path(self.tmp.name), companionship=False)

    async def asyncTearDown(self):
        if hasattr(self, 'service'):
            await self.service.stop()
        if hasattr(self, 'tmp'):
            self.tmp.cleanup()

    async def test_saved_game_reload_and_duplicate_action(self):
        from billiards.service import BilliardsService
        game = await self.service.create(['user', 'aion'], difficulty='low', seed=17)
        sid = game['id']
        await self.service.resume(sid, 'window-a')
        game = self.service.get(sid)
        first = await self.service.act(sid, 'window-a', game['revision'], 'place-1', {'type': 'confirmPlacement'})
        again = await self.service.act(sid, 'window-a', game['revision'], 'place-1', {'type': 'confirmPlacement'})
        self.assertEqual(first['revision'], again['revision'])
        self.assertEqual(first['state']['phase'], 'aim')
        other = BilliardsService(Path(self.tmp.name), companionship=False)
        self.assertEqual(other.get(sid)['state'], first['state'])
        await other.stop()

    async def test_pause_preserves_board_and_blocks_moves_and_second_window(self):
        game = await self.service.create(['user', 'connor'], seed=8)
        sid = game['id']
        await self.service.resume(sid, 'window-a')
        game = self.service.get(sid)
        game['ai_preview'] = {'id': 'active-preview', 'action': {'type': 'shot', 'shot': {'angle': .2, 'power': 60}}}
        self.service.store.save(game)
        with self.assertRaises(ValueError):
            await self.service.resume(sid, 'window-b')
        self.assertEqual(self.service.get(sid)['ai_preview']['id'], 'active-preview')
        paused = await self.service.pause(sid, 'window-a')
        self.assertIsNone(paused.get('ai_preview'))
        before = paused['state']
        with self.assertRaises(ValueError):
            await self.service.act(sid, 'window-a', paused['revision'], 'x', {'type': 'confirmPlacement'})
        resumed = await self.service.resume(sid, 'window-a')
        self.assertEqual(resumed['state'], before)

    async def test_stale_revision_and_out_of_turn_are_rejected(self):
        game = await self.service.create(['user', 'aion'], seed=4)
        sid = game['id']
        game = await self.service.resume(sid, 'w')
        with self.assertRaises(ValueError):
            await self.service.act(sid, 'w', -1, 'x', {'type': 'confirmPlacement'})
        game = await self.service.act(sid, 'w', game['revision'], 'confirm', {'type': 'confirmPlacement'})
        game = await self.service.act(sid, 'w', game['revision'], 'shot', {'type': 'shot', 'shot': {'angle': 0, 'power': 80}})
        self.assertEqual(game['state']['shotCount'], 1)
        duplicate = await self.service.act(sid, 'w', game['revision']-1, 'shot', {'type': 'shot', 'shot': {'angle': 0, 'power': 80}})
        self.assertEqual(duplicate['state']['shotCount'], 1)

    async def test_pending_shot_is_recovered_once(self):
        from billiards.service import BilliardsService
        game = await self.service.create(['user', 'aion'], difficulty='low', seed=17)
        game['state']['phase'] = 'aim'
        game['pending'] = {'id': 'interrupted', 'action': {'type': 'shot', 'shot': {'angle': 0, 'power': 80}}, 'actor': 0}
        self.service.store.save(game)
        reloaded = BilliardsService(Path(self.tmp.name), companionship=False)
        await reloaded.recover(game['id'])
        self.assertEqual(reloaded.get(game['id'])['state']['shotCount'], 1)
        await reloaded.recover(game['id'])
        self.assertEqual(reloaded.get(game['id'])['state']['shotCount'], 1)
        await reloaded.stop()

    async def test_algorithm_can_confirm_seat_zero_placement(self):
        from billiards.engine import execute
        game = await self.service.create(['aion', 'connor'], difficulty='low', seed=17)
        action = (await execute(op='plan', state=game['state'], difficulty='low', accuracy=50, seed=17))['action']
        self.assertEqual(action['type'], 'place')
        game['pending'] = {'id': 'automatic-place', 'action': action, 'actor': 0, 'automatic': True}
        await self.service.commit_pending(game)
        self.assertEqual(game['state']['phase'], 'aim')

    async def test_expired_control_during_preparation_cannot_accept_ai_turn(self):
        game = await self.service.create(['user', 'aion'], seed=17)
        game['status'] = 'running'
        game['state']['turn'] = 1
        game['lease'] = {'client': 'gone', 'until': time.time()+20}
        self.service.store.save(game)
        async def advance(seconds):
            if seconds == 2.5:
                current = self.service.get(game['id'])
                current['lease']['until'] = time.time()-1
                self.service.store.save(current)
        with patch('billiards.service.asyncio.sleep', new=advance), patch('billiards.service.execute', new=AsyncMock(return_value={'action': {'type': 'shot', 'shot': {'angle': .4, 'power': 60}}})) as engine:
            await self.service.loop(game['id'])
            self.assertEqual([call.kwargs['op'] for call in engine.await_args_list], ['plan'])
            self.assertEqual(self.service.get(game['id'])['status'], 'paused')
            self.assertIsNone(self.service.get(game['id']).get('ai_preview'))

    async def test_ai_preparation_exposes_frozen_shot_then_commits_same_direction_once(self):
        from billiards.engine import execute
        game = await self.service.create(['user', 'aion'], seed=17)
        game['status'] = 'running'
        game['state']['turn'] = 1
        game['state']['phase'] = 'aim'
        game['lease'] = {'client': 'w', 'until': time.time()+20}
        self.service.store.save(game)
        action = {'type': 'shot', 'shot': {'angle': .07, 'power': 70}}
        preview_id = None
        async def engine(**kwargs):
            return {'action': action} if kwargs['op'] == 'plan' else await execute(**kwargs)
        async def advance(seconds):
            nonlocal preview_id
            current = self.service.get(game['id'])
            if seconds == 2.5:
                preview = current.get('ai_preview')
                self.assertIsNotNone(preview, 'AI delay must publish a cue preview before striking')
                self.assertEqual(current['state']['shotCount'], 0)
                self.assertEqual(preview['action']['shot']['angle'], .07)
                preview_id = preview['id']
                action['shot']['angle'] = 1.2
            elif current['state']['shotCount']:
                current['status'] = 'finished'
                self.service.store.save(current)
        with patch('billiards.service.asyncio.sleep', new=advance), patch('billiards.service.execute', new=engine):
            await self.service.loop(game['id'])
        settled = self.service.get(game['id'])
        self.assertEqual(settled['state']['shotCount'], 1)
        self.assertEqual(settled['last_event']['id'], preview_id)
        self.assertEqual(settled['last_event']['action']['shot']['angle'], .07)
        self.assertIsNone(settled.get('ai_preview'))

    async def test_finished_match_cannot_restart_over_another_match(self):
        first = await self.service.create(['user', 'aion'], seed=17)
        await self.service.finish(first['id'], '')
        await self.service.create(['user', 'connor'], seed=17)
        with self.assertRaises(ValueError):
            await self.service.next_rack(first['id'], 'w')
        self.assertEqual(self.service.get(first['id'])['rack'], 1)

    def test_streaks_count_legal_own_pots_and_reset_on_miss(self):
        from billiards.service import update_streaks
        game = {'streaks': [{'pots': 0, 'misses': 0}, {'pots': 0, 'misses': 0}]}
        state = {'groups': ['solids', 'stripes'], 'lastShot': {'shooter': 0, 'foul': None, 'pots': [1, 2]}}
        update_streaks(game, state)
        self.assertEqual(game['streaks'][0]['pots'], 2)
        state['lastShot'] = {'shooter': 0, 'foul': None, 'pots': [9]}
        update_streaks(game, state)
        self.assertEqual(game['streaks'][0], {'pots': 0, 'misses': 1})


if __name__ == '__main__':
    unittest.main()
