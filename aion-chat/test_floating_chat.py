import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aiosqlite


class FloatingChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "chat.db"
        async with self.db() as db:
            await db.executescript("""
                CREATE TABLE conversations (id TEXT, title TEXT, model TEXT, updated_at REAL);
                CREATE TABLE messages (conv_id TEXT, role TEXT, created_at REAL);
                CREATE TABLE chatroom_rooms (id TEXT, title TEXT, type TEXT, updated_at REAL);
                CREATE TABLE chatroom_messages (room_id TEXT, sender TEXT, created_at REAL);
                CREATE TABLE runtime_state (key TEXT, value TEXT, updated_at REAL);
                INSERT INTO conversations VALUES ('private-1', '私聊', 'chosen-model', 100);
                INSERT INTO chatroom_rooms VALUES ('group-1', '小家', 'group', 200);
                INSERT INTO messages VALUES ('private-1', 'user', 10);
                INSERT INTO chatroom_messages VALUES ('group-1', 'user', 20);
            """)
            await db.commit()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def db(self):
        return aiosqlite.connect(self.path)

    async def context(self):
        self.assertIsNotNone(importlib.util.find_spec("routes.floating_chat"),
                             "悬浮聊天接口尚未实现")
        from routes import floating_chat
        with patch("database.get_db", self.db), patch.object(floating_chat, "get_db", self.db), \
                patch.object(floating_chat, "get_chatroom_names", return_value=("用户", "甲", "乙")), \
                patch.object(floating_chat, "get_chatroom_config", return_value={"aion_model": "group-model"}):
            return await floating_chat.get_context()

    async def test_latest_user_send_selects_group_despite_new_private_ai_reply(self):
        async with self.db() as db:
            await db.execute("INSERT INTO messages VALUES ('private-1', 'assistant', 300)")
            await db.commit()
        payload = await self.context()
        self.assertEqual(payload["target"], {"type": "chatroom", "id": "group-1"})
        self.assertEqual(payload["send_url"], "/api/chatroom/rooms/group-1/send")
        self.assertEqual(payload["actors"]["connor"]["name"], "乙")
        self.assertEqual(payload["send_options"]["model"], "group-model")

    async def test_new_private_user_send_selects_exact_conversation_and_saved_model(self):
        async with self.db() as db:
            await db.execute("INSERT INTO messages VALUES ('private-1', 'user', 30)")
            await db.commit()
        payload = await self.context()
        self.assertEqual(payload["target"], {"type": "private", "id": "private-1"})
        self.assertEqual(payload["send_url"], "/api/conversations/private-1/send")
        self.assertEqual(payload["send_options"]["model"], "chosen-model")

    async def test_deleted_room_does_not_receive_reply(self):
        async with self.db() as db:
            await db.execute("DELETE FROM chatroom_rooms")
            await db.commit()
        self.assertEqual((await self.context())["target"], {"type": "private", "id": "private-1"})

    async def test_screen_failure_cannot_reuse_an_old_screenshot(self):
        await self.context()
        from routes import floating_chat
        from fastapi import HTTPException
        from unittest.mock import AsyncMock
        with patch.object(floating_chat.manager, "broadcast", AsyncMock()), \
                patch.object(floating_chat, "SCREEN_TIMEOUT", 0.01, create=True):
            with self.assertRaises(HTTPException) as raised:
                await floating_chat.capture_screen(floating_chat.ScreenRequest(client_id="phone"))
        self.assertEqual(raised.exception.status_code, 409)

    async def test_only_the_requested_capture_is_frozen_even_with_an_old_upload_in_flight(self):
        await self.context()
        from routes import floating_chat
        from unittest.mock import AsyncMock
        old = Path(self.tmp.name) / "old.jpg"
        fresh = Path(self.tmp.name) / "fresh.jpg"
        old.write_bytes(b"old-screen")
        fresh.write_bytes(b"fresh-screen")
        self.assertTrue(hasattr(floating_chat, "screen_uploaded"), "截图尚未与请求关联")

        async def broadcast(event):
            request_id = event["data"]["request_id"]
            floating_chat.screen_uploaded({"reason": "cam_check", "path": str(old)})
            floating_chat.screen_uploaded({"reason": "fallback_floating:" + request_id, "path": str(fresh)})

        uploads = Path(self.tmp.name) / "uploads"
        with patch.object(floating_chat.manager, "broadcast", AsyncMock(side_effect=broadcast)), \
                patch("phone_screen.UPLOADS_DIR", uploads):
            payload = await floating_chat.capture_screen(floating_chat.ScreenRequest(client_id="phone"))
        self.assertEqual((uploads / Path(payload["attachment"]).name).read_bytes(), b"fresh-screen")


if __name__ == "__main__":
    unittest.main()
