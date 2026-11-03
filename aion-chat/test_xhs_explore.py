"""11-03 小猫：小红书不用盯着谁，随便逛逛——刷首页推荐或按兴趣搜，挑一篇看完回来说两句（私聊 / 群里 / 只记动态）。"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import autonomy
import xhs_lite

FEED = {"feeds": [{"id": "n1", "note_card": {"display_title": "秋天的银杏", "user": {"nickname": "小树"},
                                             "interact_info": {"liked_count": 12}}, "xsec_token": "t1"},
                  {"id": "n2", "note_card": {"display_title": "猫咪睡觉合集", "user": {"nickname": "喵"}}, "xsec_token": "t2"}]}
DETAIL = {"note": {"title": "猫咪睡觉合集", "desc": "我家猫各种睡姿", "user": {"nickname": "喵"}}, "comments": []}


class ExploreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(xhs_lite, "LOG_PATH", Path(self.tmp.name) / "runs.jsonl")]
        for p in self.patches:
            p.start()
        self.cfg = {**xhs_lite.DEFAULT_CONFIG, "enabled": True, "cookie": "a1=x; web_session=y"}

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def run_explore(self, asks, *, source_roll, share_patches=None, actor="aion", live_cfg=None, pin_error=None,
                    seat_room=None):
        calls = []

        async def worker(endpoint, body=None, *, cookie=None, timeout=90):
            calls.append((endpoint, body))
            return DETAIL if endpoint == "get-feed-detail" else FEED

        ask = AsyncMock(side_effect=asks)
        private = AsyncMock(return_value={"id": "m1"})
        group = AsyncMock(return_value={"id": "m2"})
        with patch.object(xhs_lite, "_run_worker", side_effect=worker), \
                patch.object(autonomy, "_ask_actor_json", new=ask), \
                patch.object(autonomy, "_save_private_message", new=private), \
                patch.object(autonomy, "_save_autonomy_chatroom_message", new=group), \
                patch.object(autonomy, "_latest_group_room_id", new=AsyncMock(return_value="grp")), \
                patch.object(xhs_lite, "load_config", return_value=live_cfg or self.cfg),                 patch.object(autonomy, "verify_seat_pin", side_effect=pin_error),                 patch("routes.chatroom._get_or_create_seat_room", new=AsyncMock(return_value=seat_room or "seatroom")),                 patch("random.random", return_value=source_roll):
            result = asyncio.run(xhs_lite._run_explore(actor, self.cfg, self.cfg["cookie"]))
        return result, calls, ask, private, group

    def test_home_feed_pick_read_and_tell_her_in_private(self):
        result, calls, ask, private, group = self.run_explore([
            {"pick": 1, "reason": "想看猫"},
            {"thoughts": "睡成一团好可爱", "share": "private", "message": "我刚看到一篇猫睡觉的，想起你"},
        ], source_roll=0.9)
        self.assertEqual(["list-feeds", "get-feed-detail"], [c[0] for c in calls])
        self.assertEqual("n2", calls[1][1]["feed_id"])
        self.assertEqual("pc_feed", calls[1][1]["xsec_source"])
        self.assertEqual(("browsed", "feed", "private", True), (result["status"], result["source"], result["share"], result["shared"]))
        private.assert_awaited_once()
        self.assertTrue(private.await_args.kwargs["force_private"])
        group.assert_not_awaited()
        self.assertEqual(2, ask.await_count)  # 刷推荐：挑一篇 + 看完感想，两次
        self.assertNotIn("post-comment", [c[0] for c in calls])  # 只看，不评论

    def test_interest_search_and_group_share(self):
        result, calls, ask, private, group = self.run_explore([
            {"keyword": "银杏"},
            {"pick": 0},
            {"thoughts": "金黄一片", "share": "group", "message": "大家看，银杏黄了"},
        ], source_roll=0.1)
        self.assertEqual(("search", {"keyword": "银杏", "page": 1, "sort_by": "general"}), calls[0])
        self.assertEqual("pc_search", calls[1][1]["xsec_source"])
        self.assertEqual("group", result["share"])
        group.assert_awaited_once()
        self.assertEqual(("grp", "aion", "大家看，银杏黄了"), group.await_args.args)
        self.assertIn("guard", group.await_args.kwargs)  # 核对一路传进保存函数
        private.assert_not_awaited()

    def test_nothing_interesting_or_no_share_stays_quiet(self):
        result, calls, _, private, group = self.run_explore([{"pick": -1, "reason": "都没兴趣"}], source_roll=0.9)
        self.assertEqual("skipped", result["status"])
        self.assertEqual(["list-feeds"], [c[0] for c in calls])
        result, _, _, private, group = self.run_explore(
            [{"pick": 0}, {"thoughts": "还行", "share": "none", "message": "不该发出去"}], source_roll=0.9)
        self.assertEqual(("none", ""), (result["share"], result["message"]))
        private.assert_not_awaited()
        group.assert_not_awaited()

    def test_share_is_rechecked_right_before_posting(self):
        """Codex §142 M1：生成感想期间停用了模块 / 这一位，或座位换了模型、改绑线路，旧的那句话不能再发。"""
        asks = lambda: [{"pick": 0}, {"thoughts": "好看", "share": "group", "message": "看这个"}]
        for live in ({**self.cfg, "enabled": False}, {**self.cfg, "actors": {"aion": {"enabled": False}}}):
            result, _, _, private, group = self.run_explore(asks(), source_roll=0.9, live_cfg=live)
            self.assertFalse(result["shared"])
            self.assertEqual("browsed", result["status"])  # 逛的结果保留
            group.assert_not_awaited()
        result, _, _, private, group = self.run_explore(
            [{"pick": 0}, {"thoughts": "好看", "share": "private", "message": "看这个"}], source_roll=0.9,
            actor="ai3", pin_error=RuntimeError("Jasper 刚刚换了模型，这次活动停下"))
        self.assertFalse(result["shared"])
        self.assertIn("换了模型", result["share_error"])
        group.assert_not_awaited()
        private.assert_not_awaited()

    def test_seat_private_share_goes_to_its_own_room_after_the_check(self):
        result, _, _, private, group = self.run_explore(
            [{"pick": 0}, {"thoughts": "好看", "share": "private", "message": "看这个"}], source_roll=0.9, actor="ai3")
        self.assertTrue(result["shared"])
        group.assert_awaited_once()
        self.assertEqual(("seatroom", "ai3", "看这个"), group.await_args.args)
        self.assertFalse(group.await_args.kwargs["auto_tts"])
        self.assertIn("guard", group.await_args.kwargs)
        private.assert_not_awaited()

    def test_explore_does_not_need_a_target_account(self):
        cfg = {**self.cfg, "auto_enabled": True, "target_user_id": "", "target_nickname": ""}
        with patch.object(xhs_lite, "load_config", return_value=cfg):
            self.assertTrue(xhs_lite.is_ready_for_auto("aion"))
        with patch.object(xhs_lite, "load_config", return_value={**cfg, "roam_mode": "target"}):
            self.assertFalse(xhs_lite.is_ready_for_auto("aion"))  # 看指定账号模式仍要目标


if __name__ == "__main__":
    unittest.main()
