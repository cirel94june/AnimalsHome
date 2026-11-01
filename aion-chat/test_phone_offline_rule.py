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


class SendFailureAndStrictTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_send_clears_the_phone_registration(self):
        """Codex §118 M1：发送失败只从 active 里去掉、登记还在，会一直被当成在线。"""
        manager = ConnectionManager()
        phone = MagicMock()
        phone.send_text = AsyncMock(side_effect=RuntimeError("broken pipe"))
        manager.active.append(phone)
        manager.register_client_id(phone, "android-push:abc")
        self.assertTrue(manager.phone_online(grace=False))
        self.assertFalse(await manager.send_to_client("android-push:abc", {"type": "x"}))
        self.assertNotIn(phone, manager.client_ids)
        self.assertFalse(manager.phone_online(grace=False))
        self.assertFalse(manager.phone_online(now=time.time() + 10000))

    async def test_broadcast_failure_also_clears_it(self):
        manager = ConnectionManager()
        phone = MagicMock()
        phone.send_text = AsyncMock(side_effect=RuntimeError("gone"))
        manager.active.append(phone)
        manager.register_client_id(phone, "android-push:abc")
        with patch("ws.persist_sync_event", new=AsyncMock(return_value=0), create=True):
            try:
                await manager.broadcast({"type": "pong"})
            except Exception:
                pass
        self.assertNotIn(phone, manager.client_ids)
        self.assertFalse(manager.phone_online(grace=False))

    def test_grace_only_applies_to_the_hint_not_to_actually_queueing(self):
        manager = ConnectionManager()
        phone = MagicMock()
        manager.active.append(phone)
        manager.register_client_id(phone, "android-push:abc")
        manager.disconnect(phone)
        self.assertTrue(manager.phone_online())             # 给 AI 的说明：刚断开先别切
        self.assertFalse(manager.phone_online(grace=False))  # 真去找手机：只认现在连着


class DeliveryRaceTests(unittest.IsolatedAsyncioTestCase):
    """Codex §122 M2：排队前查过在线，之后才断线，命令不能留在「等手机来取」里、重连后补执行。"""

    def _fake_db(self, db_path):
        import aiosqlite
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_db():
            async with aiosqlite.connect(db_path) as db:
                yield db
        return fake_db

    async def _queue_one(self, fake_db):
        snapshot = ({"groups": [{"groupId": "social"}]}, time.time())
        with patch.object(app_supervision_ai, "is_capability_enabled", return_value=True), \
                patch.object(app_supervision_ai, "_phone_online", return_value=True), \
                patch.object(app_supervision_ai.state_cache, "read", return_value=snapshot), \
                patch("database.get_db", new=fake_db):
            _, command = await app_supervision_ai.queue_app_supervision_reply_command(
                "[COME_HOME:回来]", source_message_id="m1", role_id="aion", source_kind="private", source_ref="conv-1")
        return command

    async def _pending(self, fake_db):
        async with fake_db() as db:
            return await app_supervision_ai.list_pending_app_supervision_commands(db)

    async def test_undelivered_command_is_withdrawn_and_noted(self):
        import tempfile
        from pathlib import Path
        from routes import app_supervision as routes

        with tempfile.TemporaryDirectory() as folder:
            fake_db = self._fake_db(str(Path(folder) / "chat.db"))
            command = await self._queue_one(fake_db)
            self.assertTrue(command["commandId"])
            self.assertEqual(1, len(await self._pending(fake_db)))
            publish = AsyncMock()
            # 写库后手机断了：发出去时已经没有手机连接
            with patch("ws.manager.send_to_phones", new=AsyncMock(return_value=False)), \
                    patch("database.get_db", new=fake_db), \
                    patch.object(routes, "_publish_command_result", new=publish):
                await app_supervision_ai.broadcast_app_supervision_command(command)
            self.assertEqual([], await self._pending(fake_db))  # 重连也取不到
            publish.assert_awaited_once()
            self.assertIn("没能确认送到", publish.await_args.kwargs["reason"])

    async def test_cancelled_send_still_withdraws_before_cancelling(self):
        """Codex §123 R1：send_text 等待时发送任务被取消，CancelledError 越过 except Exception，撤单没做。"""
        import asyncio
        import tempfile
        from pathlib import Path
        from routes import app_supervision as routes

        with tempfile.TemporaryDirectory() as folder:
            fake_db = self._fake_db(str(Path(folder) / "chat.db"))
            command = await self._queue_one(fake_db)
            manager = ConnectionManager()
            phone = MagicMock()
            sending = asyncio.Event()

            async def stuck_send(text):
                sending.set()
                await asyncio.sleep(30)

            phone.send_text = stuck_send
            manager.active.append(phone)
            manager.register_client_id(phone, "android-push:abc")
            publish = AsyncMock()
            with patch("ws.manager", new=manager), patch("database.get_db", new=fake_db),                     patch.object(routes, "_publish_command_result", new=publish):
                task = asyncio.create_task(app_supervision_ai.broadcast_app_supervision_command(command))
                await sending.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual([], await self._pending(fake_db))  # 取消往外传之前已经撤下
                publish.assert_awaited_once()

    async def test_cancel_while_withdrawing_after_a_failed_send(self):
        """Codex §124：发送明确失败、正在等数据库撤单时任务被取消，撤单跟着中断，命令还在 pending。"""
        import asyncio
        import tempfile
        from contextlib import asynccontextmanager
        from pathlib import Path
        from routes import app_supervision as routes

        with tempfile.TemporaryDirectory() as folder:
            real_db = self._fake_db(str(Path(folder) / "chat.db"))
            command = await self._queue_one(real_db)
            manager = ConnectionManager()
            phone = MagicMock()
            phone.send_text = AsyncMock(side_effect=RuntimeError("gone"))
            manager.active.append(phone)
            manager.register_client_id(phone, "android-push:abc")
            opening, release = asyncio.Event(), asyncio.Event()

            @asynccontextmanager
            async def slow_db():
                opening.set()
                await release.wait()  # 撤单卡在等数据库
                async with real_db() as db:
                    yield db

            publish = AsyncMock()
            with patch("ws.manager", new=manager), patch("database.get_db", new=slow_db),                     patch.object(routes, "_publish_command_result", new=publish):
                task = asyncio.create_task(app_supervision_ai.broadcast_app_supervision_command(command))
                await opening.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                release.set()
                for _ in range(50):  # 后台撤单接着做完
                    if not app_supervision_ai._withdraw_tasks:
                        break
                    await asyncio.sleep(0.02)
                self.assertEqual(set(), app_supervision_ai._withdraw_tasks)
            self.assertEqual([], await self._pending(real_db))
            publish.assert_awaited_once()

    async def test_delivered_command_stays_for_the_phone_to_ack(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            fake_db = self._fake_db(str(Path(folder) / "chat.db"))
            command = await self._queue_one(fake_db)
            send = AsyncMock(return_value=True)
            with patch("ws.manager.send_to_phones", new=send), patch("database.get_db", new=fake_db):
                await app_supervision_ai.broadcast_app_supervision_command(command)
            send.assert_awaited_once()
            self.assertEqual(1, len(await self._pending(fake_db)))

    async def test_send_to_phones_only_counts_the_phone_connection(self):
        manager = ConnectionManager()
        page, phone = MagicMock(), MagicMock()
        page.send_text = AsyncMock()
        phone.send_text = AsyncMock(side_effect=RuntimeError("gone"))
        manager.active.extend([page, phone])
        manager.register_client_id(page, "web-1")
        manager.register_client_id(phone, "android-push:abc")
        self.assertFalse(await manager.send_to_phones({"type": "app_supervision_command", "data": {}}))
        page.send_text.assert_not_awaited()  # 网页收到不算送到手机
        phone.send_text = AsyncMock()
        manager.active.append(phone)
        manager.register_client_id(phone, "android-push:abc")
        self.assertTrue(await manager.send_to_phones({"type": "app_supervision_command", "data": {}}))


class PhoneCameraOfflineTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_stops_at_once_when_the_command_is_not_delivered(self):
        """Codex §118 M2：发送明确失败时还要等 15 秒再重发、总共等 45 秒。"""
        import tempfile
        from pathlib import Path
        import phone_camera

        with tempfile.TemporaryDirectory() as folder:
            store = phone_camera.PhoneCameraCoordinator(capture_dir=Path(folder), upload_dir=Path(folder))
            store.arm("android-push:abc", "back", 1.0, {})
            sends = []

            async def send(client_id, event):
                sends.append(client_id)
                return False

            started = time.monotonic()
            result = await store.request_capture("ai_cam_check", send, timeout_seconds=30, retry_after_seconds=10)
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(("unavailable", "phone_offline"), (result.status, result.error))
            self.assertEqual(1, len(sends))  # 不重发

            sync_sends = []
            started = time.monotonic()
            sync_result = store.request_capture_sync(
                "ai_cam_check", lambda client_id, event: sync_sends.append(client_id) or False,
                timeout_seconds=30, retry_after_seconds=10)
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual("phone_offline", sync_result.error)
            self.assertEqual(1, len(sync_sends))

    async def test_monitor_image_returns_offline_without_asking_the_phone(self):
        import camera

        ask = AsyncMock(side_effect=AssertionError("不该去找手机"))
        with patch.dict(camera.cam.cfg, {"active_source": "phone"}), \
                patch.object(camera.manager, "phone_online", return_value=False), \
                patch.object(camera.phone_camera, "request_capture", new=ask):
            result = await camera.acquire_monitor_image("测试")
        self.assertEqual(("unavailable", "phone_offline"), (result.status, result.error))
        self.assertIn("留言", result.no_image_context)
        with patch.dict(camera.cam.cfg, {"active_source": "phone"}), \
                patch.object(camera.manager, "phone_online", return_value=False), \
                patch.object(camera.phone_camera, "request_capture_sync", side_effect=AssertionError("不该去找手机")):
            sync_result = camera.acquire_monitor_image_sync("测试", send_command=lambda *a: True)
        self.assertEqual("phone_offline", sync_result.error)
