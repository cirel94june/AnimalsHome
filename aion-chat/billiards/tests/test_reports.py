"""Small real-SQL checks, always against a temporary chat database."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, patch

import aiosqlite


class ResultCardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'chat.db'
        database = ModuleType('database')
        database.get_db = lambda: aiosqlite.connect(self.path)
        ws = ModuleType('ws')
        ws.manager = object()
        sync = ModuleType('sync_events')
        sync.append_sync_event = AsyncMock(return_value=1)
        sync.broadcast_synced = AsyncMock()
        self.sync = sync
        self.modules = patch.dict(sys.modules, {'database': database, 'ws': ws, 'sync_events': sync})
        self.modules.start()
        async with aiosqlite.connect(self.path) as db:
            await db.executescript('''
              CREATE TABLE conversations(id TEXT PRIMARY KEY,title TEXT,updated_at REAL);
              CREATE TABLE messages(id TEXT PRIMARY KEY,conv_id TEXT,role TEXT,content TEXT,created_at REAL,attachments TEXT);
              CREATE TABLE chatroom_rooms(id TEXT PRIMARY KEY,title TEXT,type TEXT,updated_at REAL);
              CREATE TABLE chatroom_messages(id TEXT PRIMARY KEY,room_id TEXT,sender TEXT,content TEXT,created_at REAL,attachments TEXT);
              CREATE TABLE runtime_state(key TEXT PRIMARY KEY,value TEXT,updated_at REAL);
              INSERT INTO conversations VALUES('private-old','日常私聊',100);
              INSERT INTO conversations VALUES('billiards_legacy','台球室',999);
              INSERT INTO conversations VALUES('empty-new','新建空私聊',1500);
              INSERT INTO messages VALUES('p1','private-old','user','原私聊',100,'[]');
              INSERT INTO messages VALUES('p2','billiards_legacy','user','旧桌边对话',999,'[]');
              INSERT INTO chatroom_rooms VALUES('family','日常群聊','group',200);
              INSERT INTO chatroom_rooms VALUES('companion','另一位伴侣','connor_1v1',150);
              INSERT INTO chatroom_messages VALUES('g1','family','user','原群聊',200,'[]');
              INSERT INTO chatroom_messages VALUES('c1','companion','user','原伴侣私聊',150,'[]');
              INSERT INTO runtime_state VALUES('aion_last_active','chatroom:family',200);
              INSERT INTO runtime_state VALUES('connor_last_active','family',200);
            ''')
        self.game = {'id': 'a'*32, 'players': ['user', 'connor']}
        self.result = {'rack': 2, 'winner': 1, 'score': [1, 1], 'reason': '合法打进黑八', 'shots': 18, 'finished_at': 300}
        self.labels = {'user': '测试用户', 'aion': '测试主伴侣', 'connor': '测试另一位伴侣'}

    async def asyncTearDown(self):
        self.modules.stop()
        self.tmp.cleanup()

    async def test_group_card_is_once_per_rack_and_explicitly_model_visible(self):
        from billiards.reports import last_active_window, save_result_card
        target = await last_active_window()
        self.assertEqual(target, {'type': 'chatroom', 'id': 'family'})
        await save_result_card(self.game, self.result, target, self.labels)
        await save_result_card(self.game, self.result, target, self.labels)
        async with aiosqlite.connect(self.path) as db:
            rows = await (await db.execute("SELECT sender,content,attachments FROM chatroom_messages WHERE id LIKE 'pool_result_%'")).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], 'system')
            self.assertIn('测试另一位伴侣获胜', rows[0][1])
            self.assertIn('测试用户落败', rows[0][1])
            self.assertIn('1 : 1', rows[0][1])
            attachments = json.loads(rows[0][2])
            self.assertEqual(attachments[0]['type'], 'billiards_result')
            self.assertIn({'type': 'system_model_context'}, attachments)
            self.assertEqual((await (await db.execute('SELECT COUNT(*) FROM chatroom_rooms')).fetchone())[0], 2)
        self.sync.broadcast_synced.assert_awaited_once()
        self.result['rack'] += 1
        await save_result_card(self.game, self.result, target, self.labels)
        self.assertEqual(self.sync.broadcast_synced.await_count, 2)

    async def test_latest_user_window_wins_and_legacy_table_windows_are_ignored(self):
        from billiards.reports import last_active_window, save_result_card
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE runtime_state SET value='private',updated_at=250 WHERE key='aion_last_active'")
            await db.execute("INSERT INTO messages VALUES('user-window-p','private-old','user','回私聊说话',250,'[]')")
            await db.commit()
        target = await last_active_window()
        self.assertEqual(target, {'type': 'private', 'id': 'private-old'})
        await save_result_card(self.game, self.result, target, self.labels)
        async with aiosqlite.connect(self.path) as db:
            row = await (await db.execute("SELECT conv_id,role FROM messages WHERE id LIKE 'pool_result_%'")).fetchone()
            self.assertEqual(row, ('private-old', 'system'))
            await db.execute("UPDATE runtime_state SET value='companion',updated_at=260 WHERE key='connor_last_active'")
            await db.execute("INSERT INTO chatroom_messages VALUES('user-window-c','companion','user','回伴侣私聊说话',260,'[]')")
            await db.commit()
        self.assertEqual(await last_active_window(), {'type': 'chatroom', 'id': 'companion'})
        # Existing reports and assistant messages must not steal the user's last window.
        async with aiosqlite.connect(self.path) as db:
            await db.execute('DELETE FROM runtime_state')
            await db.execute("DELETE FROM messages WHERE id='user-window-p'")
            await db.execute("DELETE FROM chatroom_messages WHERE id='user-window-c'")
            await db.execute("UPDATE chatroom_rooms SET updated_at=1000 WHERE id='companion'")
            await db.commit()
        self.assertEqual(await last_active_window(), {'type': 'chatroom', 'id': 'family'})
        # Startup may restore a missing route with a new timestamp for old history.
        async with aiosqlite.connect(self.path) as db:
            await db.execute("INSERT INTO runtime_state VALUES('aion_last_active','private',3000)")
            await db.commit()
        self.assertEqual(await last_active_window(), {'type': 'chatroom', 'id': 'family'})

    async def test_publication_recovery_keeps_one_card_in_original_target(self):
        from billiards.service import BilliardsService
        from billiards.companionship import publish_result
        from billiards.reports import save_result_card
        service = BilliardsService(Path(self.tmp.name) / 'pool', companionship=False)
        game = dict(self.game, result_reports=[dict(self.result, published=False, chat_published=False)])
        service.store.save(game)
        autonomy = ModuleType('autonomy')
        autonomy.append_idle_event = AsyncMock()
        calls = 0
        async def interrupted(*args):
            nonlocal calls
            calls += 1
            await save_result_card(*args)
            if calls == 1:
                raise OSError('模拟发送后中断')
            return True
        with patch.dict(sys.modules, {'autonomy': autonomy}), patch('billiards.companionship.names', return_value=self.labels), patch('billiards.reports.save_result_card', side_effect=interrupted):
            with self.assertRaises(OSError):
                await publish_result(service, game['id'])
            async with aiosqlite.connect(self.path) as db:
                await db.execute("UPDATE runtime_state SET value='private',updated_at=400 WHERE key='aion_last_active'")
                await db.execute("INSERT INTO messages VALUES('later-user','private-old','user','换到私聊',400,'[]')")
                await db.commit()
            await publish_result(service, game['id'])
            await publish_result(service, game['id'])
            saved = service.get(game['id'])['result_reports'][0]
            self.assertTrue(saved['chat_published'])
            self.assertTrue(saved['published'])
            self.assertEqual(saved['chat_target']['id'], 'family')
            autonomy.append_idle_event.assert_awaited_once()
            self.sync.broadcast_synced.assert_awaited_once()
            # Previously published results from the old version do not flood chat.
            saved.pop('chat_published')
            service.store.save(dict(game, result_reports=[saved]))
            await publish_result(service, game['id'])
            self.sync.broadcast_synced.assert_awaited_once()
