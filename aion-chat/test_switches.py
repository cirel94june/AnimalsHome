"""11-01「开关总览」：自己醒来的静默时间、通知总开关和「不让 AI 看的 App」。"""

import asyncio
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import quiet_hours

STATIC = Path(__file__).resolve().parent / "static"


def _at(hour: int, minute: int = 0) -> float:
    local = time.localtime()
    return time.mktime((local.tm_year, local.tm_mon, local.tm_mday, hour, minute, 0, 0, 0, -1))


class QuietHoursTests(unittest.TestCase):
    def test_same_day_and_cross_midnight_windows(self):
        day = {"enabled": True, "start": "13:00", "end": "15:00"}
        night = {"enabled": True, "start": "23:00", "end": "08:00"}
        self.assertTrue(quiet_hours.is_quiet(day, _at(14)))
        self.assertFalse(quiet_hours.is_quiet(day, _at(15)))
        self.assertTrue(quiet_hours.is_quiet(night, _at(23, 30)))
        self.assertTrue(quiet_hours.is_quiet(night, _at(7, 59)))
        self.assertFalse(quiet_hours.is_quiet(night, _at(8)))
        self.assertFalse(quiet_hours.is_quiet({**night, "enabled": False}, _at(23, 30)))
        self.assertFalse(quiet_hours.is_quiet({"enabled": True, "start": "25:00", "end": "08:00"}, _at(1)))

    def test_end_after_lands_on_the_end_of_the_window(self):
        night = {"enabled": True, "start": "23:00", "end": "08:00"}
        end = quiet_hours.quiet_end_after(night, _at(7, 30))
        self.assertEqual(_at(8), end)
        late = quiet_hours.quiet_end_after(night, _at(23, 30))
        self.assertAlmostEqual(8.5 * 3600, late - _at(23, 30), delta=60)
        self.assertEqual(_at(12), quiet_hours.quiet_end_after(night, _at(12)))  # 不在静默里：原样返回


class WakeQuietTests(unittest.IsolatedAsyncioTestCase):
    async def test_due_wake_in_quiet_hours_is_postponed_not_run(self):
        import autonomy

        manager = autonomy.IdleAutonomyManager()
        postponed = AsyncMock(return_value=True)
        run = AsyncMock()
        with patch.object(autonomy, "get_actor_config", new=AsyncMock(return_value={"enabled": True, "next_wake_at": 1})), \
                patch.object(autonomy, "_latest_user_message_ts", new=AsyncMock(return_value=0)), \
                patch.object(autonomy, "refresh_actor_wake_for_user", new=AsyncMock(return_value=False)), \
                patch.object(autonomy, "get_wake_quiet_hours", return_value={"enabled": True, "start": "00:00", "end": "23:59"}), \
                patch.object(autonomy.quiet_hours, "is_quiet", return_value=True), \
                patch.object(autonomy, "postpone_due_wake", new=postponed), \
                patch.object(autonomy, "claim_due_wake", new=AsyncMock(side_effect=AssertionError("不该醒"))), \
                patch.object(autonomy, "_run_actor_once", new=run):
            result = await manager.run_actor_once("aion", manual=False)
        self.assertEqual("quiet hours", result["skipped"])
        postponed.assert_awaited_once()
        run.assert_not_awaited()

    def test_quiet_hours_setting_rejects_bad_times(self):
        import autonomy

        store = {}
        with patch.object(autonomy, "SETTINGS", store), patch.object(autonomy, "save_settings"):
            saved = autonomy.save_wake_quiet_hours(enabled=True, start="23:00", end="08:00")
            self.assertEqual({"enabled": True, "start": "23:00", "end": "08:00"}, saved)
            with self.assertRaises(ValueError):
                autonomy.save_wake_quiet_hours(enabled=True, start="9点", end="08:00")


class NotificationSettingsTests(unittest.TestCase):
    def setUp(self):
        import device_context
        self.dc = device_context
        self.store = {}
        self.patches = [patch("config.SETTINGS", self.store), patch("config.save_settings")]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()

    def test_blocked_app_is_filtered_and_not_recorded(self):
        import activity

        self.dc.save_notification_settings(enabled=True, blocked_apps=[
            {"package": "com.tencent.mm", "name": "微信"}, {"package": "com.tencent.mm", "name": "重复"}, {"name": "没包名"}])
        self.assertEqual([{"package": "com.tencent.mm", "name": "微信"}], self.dc.notification_settings()["blocked_apps"])

        store = self.dc.DeviceContextStore()
        now = time.time()
        store.upsert_notification({"key": "a", "package_name": "com.tencent.mm", "app_name": "微信", "title": "在吗", "text": "x"}, now)
        store.upsert_notification({"key": "b", "package_name": "com.taobao.taobao", "app_name": "淘宝", "title": "到货", "text": "y"}, now)
        active, filtered = store._fresh_notifications(now)
        self.assertEqual(["淘宝"], [item["app_name"] for item in active])
        self.assertEqual("小猫设了不让 AI 看这个 App", filtered[0]["filter_reason"])
        self.assertEqual({"淘宝", "微信"}, {app["name"] for app in store.recent_apps()})

        with patch.object(activity, "append_activity_log") as log:
            self.assertEqual({}, activity.record_notification({"key": "c", "package_name": "com.tencent.mm", "title": "t"}))
            log.assert_not_called()

    def test_master_switch_off_hides_everything(self):
        self.dc.save_notification_settings(enabled=False, blocked_apps=[])
        store = self.dc.DeviceContextStore()
        now = time.time()
        store.upsert_notification({"key": "b", "package_name": "com.taobao.taobao", "title": "到货", "text": "y"}, now)
        active, filtered = store._fresh_notifications(now)
        self.assertEqual([], active)
        self.assertEqual("小猫关了「把通知告诉 AI」", filtered[0]["filter_reason"])


class SwitchesPageTests(unittest.TestCase):
    def test_page_uses_text_only_and_real_endpoints(self):
        page = (STATIC / "switches.html").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", page)
        for url in ("/api/idle-autonomy/quiet-hours", "/api/cam/config", "/api/cam/monitor/start",
                    "/api/device-context/notification-settings", "/api/idle-autonomy/${encodeURIComponent(actor.id)}/config"):
            self.assertIn(url, page)
        home = (STATIC / "home.html").read_text(encoding="utf-8")
        self.assertIn("apps: ['switches', 'settings',", home)


if __name__ == "__main__":
    unittest.main()
