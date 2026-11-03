"""11-03 小猫：小克总是「觉得暂时没什么想做」，其实是没叫醒（调用失败 / 回的不是 JSON）；
狗蛋逛淘宝失败也显示「旅行没有完成」；旅行失败没留下原因。"""

import unittest
from unittest.mock import AsyncMock, patch

import autonomy

CONFIG = {"enabled": True, "actions": {"private_chat": True, "home_dynamics": True}}


class WakeHonestyTests(unittest.IsolatedAsyncioTestCase):
    async def test_unreadable_answer_is_asked_again_once(self):
        ask = AsyncMock(side_effect=[{"action": "nonsense"}, {"action": "home_dynamics", "reason": "想看看大家"}])
        with patch.object(autonomy, "get_actor_config", new=AsyncMock(return_value=CONFIG)), \
                patch.object(autonomy, "_ask_actor_json", new=ask):
            selected = await autonomy._select_action("aion")
        self.assertEqual("home_dynamics", selected["action"])
        self.assertEqual(2, ask.await_count)

    async def test_failed_wake_is_not_dressed_up_as_choosing_to_rest(self):
        ask = AsyncMock(side_effect=RuntimeError("HTTP 503 暂无可用服务容量"))
        with patch.object(autonomy, "get_actor_config", new=AsyncMock(return_value=CONFIG)), \
                patch.object(autonomy, "_ask_actor_json", new=ask):
            selected = await autonomy._select_action("aion")
        self.assertEqual("rest", selected["action"])
        self.assertTrue(selected["wake_failed"])
        self.assertIn("没醒成", selected["reason"])
        self.assertIn("503", selected["reason"])
        self.assertEqual(2, ask.await_count)

    def test_empty_exception_text_still_says_what_happened(self):
        self.assertEqual("TimeoutError", autonomy._error_text(TimeoutError()))
        self.assertEqual("ValueError: 没找到", autonomy._error_text(ValueError("没找到")))

    def test_failure_titles_name_the_real_action(self):
        source = open(autonomy.__file__, encoding="utf-8").read()
        self.assertNotIn('title = f"{actor_name}的这次旅行没有完成"', source)
        self.assertIn('title = f"{actor_name}这次没做成：{ACTION_SHORT_NAMES.get(action, \'自主行动\')}"', source)
        self.assertEqual("逛淘宝", autonomy.ACTION_SHORT_NAMES["taobao_roam"])
        self.assertIn('title = f"{actor_name}这次没醒成"', source)


if __name__ == "__main__":
    unittest.main()
