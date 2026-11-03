"""Codex §143 余项：核对通过以后，保存函数里还要等（查窗口、链接预览），这期间关掉开关或停用座位，消息照样写进去。
现在保存函数在真正写库前最后再调一次 guard。"""

import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite

import autonomy
from routes import chatroom as chatroom_routes


class SaveGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "chat.db")
        db = sqlite3.connect(self.db_path)
        db.executescript("""
            CREATE TABLE chatroom_messages (id TEXT, room_id TEXT, sender TEXT, content TEXT, attachments TEXT,
                                            reasoning_content TEXT, created_at REAL);
            CREATE TABLE chatroom_rooms (id TEXT, type TEXT, updated_at REAL);
            INSERT INTO chatroom_rooms VALUES ('r1', 'group', 0);
            CREATE TABLE conversations (id TEXT, title TEXT, model TEXT, created_at REAL, updated_at REAL);
            INSERT INTO conversations VALUES ('c1', 't', 'm', 0, 0);
            CREATE TABLE messages (id TEXT, conv_id TEXT, role TEXT, content TEXT, created_at REAL, attachments TEXT);
        """)
        db.commit()
        db.close()
        self.state = {"allowed": True}

        @asynccontextmanager
        async def fake_db():
            async with aiosqlite.connect(self.db_path) as conn:
                yield conn
        self.fake_db = fake_db

    def tearDown(self):
        self.tmp.cleanup()

    def guard(self):
        return "" if self.state["allowed"] else "小红书 Lite 已关闭，这次不说了"

    def count(self, table):
        db = sqlite3.connect(self.db_path)
        n = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        db.close()
        return n

    def switch_off_while_previewing(self):
        async def preview(content, attachments):
            await asyncio.sleep(0)
            self.state["allowed"] = False  # 链接预览期间她关掉了开关
            return list(attachments or [])
        return preview

    def test_chatroom_save_rechecks_after_link_preview(self):
        with patch.object(chatroom_routes, "get_db", self.fake_db), \
                patch.object(chatroom_routes, "_with_link_previews", self.switch_off_while_previewing()), \
                patch.object(chatroom_routes, "with_band_vibration_attachment", AsyncMock(side_effect=lambda mid, a: a)), \
                patch.object(chatroom_routes, "broadcast_synced", AsyncMock()):
            with self.assertRaises(chatroom_routes.MessageBlocked):
                asyncio.run(chatroom_routes._save_msg("r1", "ai3", "看这个", guard=self.guard, auto_tts=False))
        self.assertEqual(0, self.count("chatroom_messages"))

    def test_xiaoke_private_save_rechecks_after_window_lookup_and_preview(self):
        with patch.object(autonomy, "get_db", self.fake_db), \
                patch.object(autonomy, "_latest_conversation", AsyncMock(return_value=("c1", "m"))), \
                patch.object(autonomy, "_with_link_previews", self.switch_off_while_previewing()), \
                patch.object(autonomy.manager, "broadcast", AsyncMock()):
            with self.assertRaises(chatroom_routes.MessageBlocked):
                asyncio.run(autonomy._save_aion_private_message("看这个", guard=self.guard, auto_tts=False))
        self.assertEqual(0, self.count("messages"))

    def test_blocked_message_does_not_leave_an_empty_new_window(self):
        """Codex §144 L：没有私聊窗口时原来先建窗口并提交，再核对，拦下后留下一扇空窗口。"""
        db = sqlite3.connect(self.db_path)
        db.execute("DELETE FROM conversations")
        db.commit()
        db.close()
        with patch.object(autonomy, "get_db", self.fake_db),                 patch.object(autonomy, "_latest_conversation", AsyncMock(return_value=(None, "m"))),                 patch.object(autonomy, "_with_link_previews", self.switch_off_while_previewing()),                 patch.object(autonomy.manager, "broadcast", AsyncMock()):
            with self.assertRaises(chatroom_routes.MessageBlocked):
                asyncio.run(autonomy._save_aion_private_message("看这个", guard=self.guard, auto_tts=False))
        self.assertEqual((0, 0), (self.count("conversations"), self.count("messages")))

    def test_without_a_guard_or_when_still_allowed_it_saves(self):
        with patch.object(autonomy, "get_db", self.fake_db), \
                patch.object(autonomy, "_latest_conversation", AsyncMock(return_value=("c1", "m"))), \
                patch.object(autonomy, "_with_link_previews", AsyncMock(side_effect=lambda c, a: list(a or []))), \
                patch.object(autonomy.manager, "broadcast", AsyncMock()):
            asyncio.run(autonomy._save_aion_private_message("看这个", guard=self.guard, auto_tts=False))
        self.assertEqual(1, self.count("messages"))

    def test_guard_reaches_every_branch(self):
        import inspect
        source = inspect.getsource(autonomy._save_private_message)
        self.assertEqual(3, source.count("**extra"))  # 小克在群、座位、Lucien
        self.assertIn("guard=guard", source)          # 小克私聊


if __name__ == "__main__":
    unittest.main()
