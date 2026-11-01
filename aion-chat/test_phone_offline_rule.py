"""10-30 小猫定的规矩：她的手机 App 不在线时，不去找手机，改成留言；上线时不再刷出一串「命令已过期」。"""

import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import app_supervision_ai
from ws import ConnectionManager


class PhonePresenceTests(unittest.TestCase):
    def test_android_push_registration_means_online_with_short_grace(self):
        manager = ConnectionManager()
        phone, page = MagicMock(), MagicMock()
        manager.active.extend([phone, page])
        self.assertFalse(manager.phone_online())
        manager.register_client_id(page, "web-123")
        self.assertFalse(manager.phone_online())  # 网页开着不算手机在线
        manager.register_client_id(phone, "android-push:abc")
        self.assertTrue(manager.phone_online())
        manager.disconnect(phone)
        self.assertTrue(manager.phone_online())  # 刚断开 90 秒内当作网络抖动
        self.assertFalse(manager.phone_online(now=time.time() + 120))


class OfflineAbilityTextTests(unittest.TestCase):
    def test_ai_only_gets_one_short_line_while_phone_is_offline(self):
        with patch.object(app_supervision_ai, "is_capability_enabled", return_value=True), \
                patch.object(app_supervision_ai, "_phone_online", return_value=False):
            text = app_supervision_ai.build_app_supervision_ability_text()
        self.assertEqual(app_supervision_ai.PHONE_OFFLINE_TEXT, text)
        self.assertIn("留言", text)
        self.assertNotIn("[APP_LOCK:", text)  # 不给能下命令的格式


class OfflineCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_is_not_queued_and_one_note_is_posted(self):
        snapshot = ({"groups": [{"groupId": "social"}]}, time.time())
        with patch.object(app_supervision_ai, "is_capability_enabled", return_value=True), \
                patch.object(app_supervision_ai, "_phone_online", return_value=False), \
                patch.object(app_supervision_ai.state_cache, "read", return_value=snapshot), \
                patch.object(app_supervision_ai, "enqueue_app_supervision_command",
                             new=AsyncMock(side_effect=AssertionError("不该排队"))):
            cleaned, command = await app_supervision_ai.queue_app_supervision_reply_command(
                "想你了 [COME_HOME:回来陪我一会儿]", source_message_id="m1", role_id="aion",
                source_kind="private", source_ref="conv-1")
        self.assertNotIn("COME_HOME", cleaned)
        self.assertEqual("phone_offline", command["skipped"])

        from routes import app_supervision as routes
        publish = AsyncMock()
        broadcast = AsyncMock()
        with patch.object(routes, "_publish_command_result", new=publish), \
                patch("ws.manager.broadcast", new=broadcast):
            await app_supervision_ai.broadcast_app_supervision_command(command)
        publish.assert_awaited_once()
        self.assertIn("手机不在线", publish.await_args.kwargs["reason"])
        broadcast.assert_not_awaited()  # 不再发给手机

    async def test_expired_commands_are_reported_once(self):
        from routes import app_supervision as routes

        expired = [{"commandId": f"c{i}", "createdAt": 100 + i, "sourceKind": "private", "sourceRef": "conv-1",
                    "sourceMessageId": f"m{i}", "roleId": "aion", "groupId": "", "action": "come_home"}
                   for i in range(26)]

        class FakeDb:
            async def commit(self):
                return None

        class FakeCtx:
            async def __aenter__(self):
                return FakeDb()

            async def __aexit__(self, *args):
                return False

        publish = AsyncMock()
        with patch.object(routes, "is_capability_enabled", return_value=True), \
                patch.object(routes, "get_db", return_value=FakeCtx()), \
                patch.object(routes, "expire_pending_app_supervision_commands", new=AsyncMock(return_value=expired)), \
                patch.object(routes, "list_pending_app_supervision_commands", new=AsyncMock(return_value=[])), \
                patch.object(routes, "_publish_command_result", new=publish):
            await routes.get_pending_commands()
        publish.assert_awaited_once()
        self.assertEqual("c25", publish.await_args.args[0]["commandId"])  # 最近那条
        self.assertIn("26 条", publish.await_args.kwargs["reason"])


if __name__ == "__main__":
    unittest.main()
