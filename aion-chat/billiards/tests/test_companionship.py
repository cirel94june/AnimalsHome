import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


class CompanionshipTests(unittest.IsolatedAsyncioTestCase):
    def test_latest_facts_include_groups_score_streaks_without_coordinates(self):
        from billiards.companionship import facts
        game = {'players': ['user', 'connor'], 'rack': 3, 'score': [1, 1],
                'status': 'paused', 'losses': [0, 1],
                'streaks': [{'pots': 3, 'misses': 0}, {'pots': 0, 'misses': 2}],
                'state': {'groups': ['solids', 'stripes'], 'turn': 1,
                          'phase': 'aim', 'balls': [{'id': 1, 'pocketed': True, 'x': .3}, {'id': 9, 'pocketed': False, 'x': .5}],
                          'lastShot': {'shooter': 0, 'pots': [1], 'foul': None}, 'result': None}}
        info = facts(game, {'user': '测试用户', 'connor': '测试伴侣'})
        self.assertEqual(info['players'][1]['group'], '花色')
        self.assertEqual(info['players'][1]['remaining'], [9])
        self.assertEqual(info['score'], [1, 1])
        self.assertEqual(info['players'][0]['consecutive_pots'], 3)
        self.assertNotIn('x', str(info))

    def test_cooldown_and_milestone_avoid_speaking_every_shot(self):
        from billiards.companionship import reaction_reason
        game = {'last_speech_at': 100, 'state': {'phase': 'aim', 'lastShot': {'pots': [1], 'foul': None}, 'groups': ['solids', 'stripes'], 'balls': [{'id': 1, 'pocketed': False}, {'id': 9, 'pocketed': False}]},
                'streaks': [{'pots': 1, 'misses': 0}, {'pots': 0, 'misses': 0}]}
        self.assertEqual(reaction_reason(game, 'shot', now=150), '')
        game['streaks'][0]['pots'] = 3
        self.assertTrue(reaction_reason(game, 'shot', now=150))
        self.assertFalse(reaction_reason(game, 'shot', now=105))

    async def test_queued_reply_does_not_move_into_next_rack(self):
        from billiards.service import BilliardsService
        from billiards.companionship import chat
        with tempfile.TemporaryDirectory() as directory:
            service = BilliardsService(Path(directory), companionship=False)
            game = await service.create(['user', 'aion'], seed=17)
            lock = asyncio.Lock()
            service.chat_locks[game['id']] = lock
            await lock.acquire()
            with patch('billiards.companionship.model_text', new=AsyncMock(return_value='旧回复')) as model:
                task = asyncio.create_task(chat(service, game['id'], '刚刚那杆'))
                while not service.get(game['id'])['messages']:
                    await asyncio.sleep(0)
                current = service.get(game['id'])
                current['rack'] += 1
                service.save(current)
                lock.release()
                await task
                model.assert_not_awaited()
                self.assertEqual(len(service.get(game['id'])['messages']), 1)
            await service.stop()

    async def test_table_dialogue_never_opens_or_writes_daily_chat(self):
        from billiards.service import BilliardsService
        from billiards.companionship import append_message
        import sys
        from types import ModuleType
        database = ModuleType('database')
        database.get_db = Mock(side_effect=AssertionError('桌边消息不应访问日常聊天数据库'))
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {'database': database}):
            service = BilliardsService(Path(directory), companionship=False)
            game = await service.create(['user', 'connor'], seed=17)
            await append_message(service, game['id'], 'user', '偷偷练过吧？')
            await append_message(service, game['id'], 'connor', '下一杆再试试。')
            self.assertEqual(len(service.get(game['id'])['messages']), 2)
            database.get_db.assert_not_called()
            await service.stop()

    async def test_result_publication_does_not_wait_for_speech(self):
        from billiards.service import BilliardsService
        with tempfile.TemporaryDirectory() as directory:
            service = BilliardsService(Path(directory))
            game = await service.create(['user', 'aion'], seed=17)
            game['state']['phase'] = 'over'
            game['result_reports'] = [{'rack': 1, 'published': False}]
            service.store.save(game)
            gate = asyncio.Event()
            async def slow_speech(*args):
                await gate.wait()
            with patch('billiards.companionship.react', new=slow_speech), patch('billiards.companionship.publish_result_safely', new=AsyncMock()) as publish:
                service.say_later(game['id'], 'shot')
                await asyncio.sleep(0)
                publish.assert_awaited_once()
                gate.set()
                await service.stop()

    async def test_unpublished_result_is_scheduled_after_reload(self):
        from billiards.service import BilliardsService
        with tempfile.TemporaryDirectory() as directory:
            service = BilliardsService(Path(directory))
            game = await service.create(['user', 'aion'], seed=17)
            game['status'] = 'finished'
            game['result_reports'] = [{'rack': 1, 'published': False}]
            service.store.save(game)
            restored = BilliardsService(Path(directory))
            with patch('billiards.companionship.publish_result_safely', new=AsyncMock()) as publish:
                await restored.start()
                await asyncio.sleep(0)
                publish.assert_awaited_once()
            await restored.stop()
            await service.stop()


if __name__ == '__main__':
    unittest.main()
