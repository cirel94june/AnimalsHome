"""归巢带 Hub 记忆（Hub #123 export-lite）：打包、共用钥匙、失败不拖垮归巢、回到线上只读。"""

import json
import sys
import unittest
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from test_homecoming_return_planner import HomecomingReturnPlannerTests, operation  # noqa: E402
from test_homecoming_seats import SeatSnapshotTests  # noqa: E402

from homecoming import hub_pack  # noqa: E402

HUB_CFG = {"enabled": True, "url": "https://hub.example/mcp", "token": "shared-key"}


def _hub_item(hub_id, content, **extra):
    return {"id": hub_id, "content": content, "kind": "stable", "room": "living",
            "event_date": "2026-10-30", "importance": 0.8, "fact_ref": None, "tags": [], **extra}


class PortableItemsTests(unittest.TestCase):
    def test_ids_are_per_owner_and_tags_become_keywords(self):
        rows = hub_pack.portable_items("main", {"items": [
            _hub_item("m1", "小猫喜欢下雨天", tags=["下雨", "天气"]),
            _hub_item("m2", "补充：尤其是夜里", fact_ref="m1"),
            _hub_item("m3", "   "),
            "not-a-dict",
        ]})
        self.assertEqual(["hub:main:m1", "hub:main:m2"], [row["id"] for row in rows])
        self.assertEqual("下雨,天气", rows[0]["keywords"])
        self.assertEqual("hub:main:m1", rows[1]["fact_ref"])
        self.assertTrue(all(row["read_only"] and row["source"] == "hub" for row in rows))
        # 同一条共享记忆进两位 AI 的包，手机库主键不能撞
        other = hub_pack.portable_items("second", {"items": [_hub_item("m1", "小猫喜欢下雨天")]})
        self.assertNotEqual(rows[0]["id"], other[0]["id"])


class FetchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.requests = []
        self._orig = (hub_pack.memory_hub_bridge.load_config, hub_pack.actors.memory_hub_id,
                      hub_pack.httpx.AsyncClient)
        hub_pack.actors.memory_hub_id = {"aion": "claude", "connor": "lucien", "ai3": "jasper"}.get
        hub_pack.memory_hub_bridge.load_config = lambda: dict(HUB_CFG)

    def tearDown(self):
        (hub_pack.memory_hub_bridge.load_config, hub_pack.actors.memory_hub_id,
         hub_pack.httpx.AsyncClient) = self._orig

    def _serve(self, handler):
        real = self._orig[2]

        def client(**kwargs):
            return real(transport=httpx.MockTransport(handler), **kwargs)
        hub_pack.httpx.AsyncClient = client

    async def test_reads_each_ai_with_shared_key_and_skips_failures(self):
        import os
        os.environ["MEMORY_HUB_OWNER_SECRET"] = "owner-key"
        self.addCleanup(os.environ.pop, "MEMORY_HUB_OWNER_SECRET", None)

        def handler(request):
            self.requests.append(request)
            ai = request.url.params["ai_id"]
            if ai == "lucien":
                return httpx.Response(500)
            return httpx.Response(200, json={"items": [_hub_item(f"{ai}-1", f"{ai} 的记忆")]})
        self._serve(handler)

        result = await hub_pack.fetch_hub_memories({"main": "aion", "second": "connor", "ai3": "ai3", "ai4": "ai4"})

        self.assertEqual({"main", "ai3"}, set(result))  # Lucien 失败、ai4 没接 Hub：都不拖垮别人
        self.assertEqual(["hub:main:claude-1"], [row["id"] for row in result["main"]])
        for request in self.requests:
            self.assertEqual("https://hub.example/api/memory/export-lite", str(request.url).split("?")[0])
            self.assertEqual("Bearer shared-key", request.headers["Authorization"])  # 不用主人钥匙
            self.assertEqual("false", request.url.params["include_dreams"])
            self.assertEqual("60000", request.url.params["max_bytes"])

    async def test_hub_not_configured_means_no_hub_memories(self):
        hub_pack.memory_hub_bridge.load_config = lambda: {}
        self._serve(lambda request: self.fail("不该发请求"))
        self.assertEqual({}, await hub_pack.fetch_hub_memories({"main": "aion"}))

    async def test_unreachable_hub_returns_empty(self):
        def handler(request):
            raise httpx.ConnectError("down")
        self._serve(handler)
        self.assertEqual({}, await hub_pack.fetch_hub_memories({"main": "aion"}))


class HubSnapshotTests(SeatSnapshotTests):
    async def test_hub_memories_follow_local_ones_and_reach_ready_seats_only(self):
        from homecoming.snapshot import build_snapshot_sections
        import aiosqlite

        hub = {
            "main": hub_pack.portable_items("main", {"items": [_hub_item("a", "小克记得的")]}),
            "second": hub_pack.portable_items("second", {"items": [_hub_item("b", "Lucien 记得的")]}),
            "ai3": hub_pack.portable_items("ai3", {"items": [_hub_item("c", "狗蛋记得的")]}),
            "ai4": hub_pack.portable_items("ai4", {"items": [_hub_item("d", "没准备好的座位")]}),
        }
        async with aiosqlite.connect(self.db_path) as db:
            sections = await build_snapshot_sections(
                db, now=1_800_000_000.0, supervision_snapshot={}, settings={},
                worldbook={"ai_name": "小克"}, chatroom_config={"connor_name": "Lucien"},
                location_config={}, location_status={}, camera_config={}, digest_anchor={},
                seats=[{"id": "ai3", "name": "狗蛋"}], group_seats=["ai3"], hub_memories=hub,
            )
        memories = sections["memories"]
        self.assertEqual("hub:main:a", memories["main"][-1]["id"])
        self.assertEqual("hub:second:b", memories["second"][-1]["id"])
        self.assertEqual(["hub:ai3:c"], [row["id"] for row in memories["ai3"]])
        self.assertNotIn("ai4", memories)
        self.assertNotIn("没准备好的座位", json.dumps(sections, ensure_ascii=False))


class HubReturnPlannerTests(HomecomingReturnPlannerTests):
    async def test_phone_edits_to_hub_memories_are_never_written_back(self):
        for action in ("update", "delete"):
            plan = await self.plan(operation(
                f"hub-op-{action}", "memory", action,
                {"id": "hub:main:m1", "owner_id": "main", "content": "手机上改过", "keywords": "",
                 "tombstone": action == "delete", "updated_at": 2},
                entity_id="hub:main:m1",
            ))
            self.assertEqual("skip", plan.rows[0].decision)
            self.assertEqual("hub_memory_is_read_only", plan.rows[0].reason)
            self.assertEqual((), tuple(plan.rows[0].effects or ()))


if __name__ == "__main__":
    unittest.main()
