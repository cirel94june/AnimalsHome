"""2026-10-29 归巢全员：狗蛋和以后在座位 3～6 接的 AI 也能进归巢、回来能合并。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import aiosqlite

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from test_homecoming_return_planner import HomecomingReturnPlannerTests, operation  # noqa: E402

NOW = 1_800_000_000.0
SEATS = [
    {"id": "ai3", "name": "狗蛋", "persona": "狗蛋的人设", "model_key": "gemini-3.8-flash"},
    {"id": "ai5", "name": "Kimi", "persona": "Kimi 的人设", "model_key": "kimi-k2"},
]


class SeatSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "seats.db"
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(
                """
                CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT, model TEXT,
                    created_at REAL, updated_at REAL);
                CREATE TABLE messages (id TEXT PRIMARY KEY, conv_id TEXT, role TEXT,
                    content TEXT, attachments TEXT, created_at REAL);
                CREATE TABLE chatroom_rooms (id TEXT PRIMARY KEY, title TEXT, type TEXT,
                    aion_persona TEXT, connor_persona TEXT, context_minutes INTEGER,
                    ai_chat_rounds INTEGER, created_at REAL, updated_at REAL, actor TEXT DEFAULT '');
                CREATE TABLE chatroom_messages (id TEXT PRIMARY KEY, room_id TEXT, sender TEXT,
                    content TEXT, attachments TEXT, created_at REAL);
                CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT, type TEXT, created_at REAL);
                CREATE TABLE chatroom_memories (id TEXT PRIMARY KEY, room_id TEXT, scope TEXT,
                    content TEXT, created_at REAL);
                CREATE TABLE schedules (id TEXT PRIMARY KEY, trigger_at TEXT, status TEXT);
                """
            )
            await db.executemany(
                "INSERT INTO chatroom_rooms VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    ("room-second", "", "connor_1v1", "", "", 30, 1, 1, 10, ""),
                    ("room-group", "", "group", "", "", 30, 3, 1, 10, ""),
                    ("room-ai3-old", "", "seat_1v1", "", "", 30, 1, 1, 5, "ai3"),
                    ("room-ai3", "", "seat_1v1", "", "", 30, 1, 1, 20, "ai3"),
                    ("room-ai4", "", "seat_1v1", "", "", 30, 1, 1, 20, "ai4"),
                ],
            )
            await db.executemany(
                "INSERT INTO chatroom_messages VALUES (?,?,?,?,?,?)",
                [
                    ("ai3-old", "room-ai3-old", "ai3", "以前的窗口", "[]", NOW - 30),
                    ("ai3-1", "room-ai3", "user", "狗蛋在吗", "[]", NOW - 20),
                    ("ai3-2", "room-ai3", "ai3", "在！", "[]", NOW - 10),
                    ("ai4-1", "room-ai4", "ai4", "没启用的座位", "[]", NOW - 10),
                    ("second-1", "room-second", "connor", "Lucien", "[]", NOW - 5),
                ],
            )
            await db.commit()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def build(self, seats=SEATS, group_seats=("ai3", "ai5")):
        from homecoming.snapshot import build_snapshot_sections

        async with aiosqlite.connect(self.db_path) as db:
            return await build_snapshot_sections(
                db, now=NOW, supervision_snapshot={}, settings={},
                worldbook={"user_name": "小猫", "ai_name": "小克", "ai_persona": "小克的人设"},
                chatroom_config={"connor_name": "Lucien", "connor_persona": "Lucien 的人设"},
                location_config={}, location_status={}, camera_config={}, digest_anchor={},
                seats=list(seats), group_seats=list(group_seats),
            )

    async def test_ready_seats_get_identity_roster_and_their_own_timeline(self):
        sections = await self.build()
        identity = sections["identity"]
        self.assertEqual("狗蛋", identity["companions"]["ai3"]["name"])
        self.assertEqual("Kimi 的人设", identity["companions"]["ai5"]["persona"])
        self.assertEqual("小克", identity["companions"]["main"]["name"])  # 旧的两位不变
        self.assertEqual(
            [("main", "main_private"), ("second", "companion_private"),
             ("ai3", "seat_private:ai3"), ("ai5", "seat_private:ai5")],
            [(item["owner"], item["timeline"]) for item in identity["roster"]],
        )
        self.assertEqual("gemini-3.8-flash", identity["roster"][2]["model_key"])
        self.assertEqual(["main", "second", "ai3", "ai5"], identity["group_members"])

        ai3 = sections["timelines"]["seat_private:ai3"]
        self.assertEqual(["ai3-old", "ai3-1", "ai3-2"], [m["id"] for m in ai3["messages"]])
        self.assertEqual(["room-ai3-old", "room-ai3"], [room["id"] for room in ai3["containers"]])
        self.assertEqual([], sections["timelines"]["seat_private:ai5"]["messages"])
        self.assertNotIn("seat_private:ai4", sections["timelines"])  # 没准备好的座位不带
        serialized = json.dumps(sections["timelines"], ensure_ascii=False)
        self.assertNotIn("没启用的座位", serialized)
        self.assertNotIn("ai3-1", json.dumps(sections["timelines"]["companion_private"]))

    async def test_group_only_lists_seats_that_are_ready(self):
        sections = await self.build(seats=SEATS[:1], group_seats=["ai3", "ai5", "ai9"])
        self.assertEqual(["main", "second", "ai3"], sections["identity"]["group_members"])

    async def test_unknown_or_unnamed_seats_are_dropped(self):
        sections = await self.build(seats=[{"id": "ai9", "name": "x"}, {"id": "ai6", "name": " "}], group_seats=[])
        self.assertEqual(2, len(sections["identity"]["roster"]))
        self.assertEqual({"main_private", "companion_private", "group"}, set(sections["timelines"]))

    async def test_timeline_mapping_points_at_the_latest_seat_room(self):
        from routes.homecoming import _timeline_mapping

        mapping = _timeline_mapping(await self.build())
        self.assertEqual("room-ai3", mapping["seat_private:ai3"])
        self.assertNotIn("seat_private:ai5", mapping)  # 还没有房间（路由会在打包前建好）
        self.assertEqual("room-second", mapping["companion_private"])


class SeatReturnPlannerTests(HomecomingReturnPlannerTests):
    """沿用原有回传测试的库，再加一个座位房间和映射。"""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO chatroom_rooms VALUES (?,?,?,?,?,?,?,?,?)",
                ("room-ai3", "", "seat_1v1", "", "", 30, 1, 1, 10),
            )
            await db.execute(
                "UPDATE homecoming_snapshot_exports SET timeline_mapping_json=?",
                (json.dumps({
                    "main_private": "conv-main", "companion_private": "room-second",
                    "group": "room-group", "seat_private:ai3": "room-ai3",
                    "seat_private:ai9": "room-evil", "other": "room-evil",
                }),),
            )
            await db.commit()

    def seat_message(self, op_id, sender, timeline="seat_private:ai3", sequence=1):
        return operation(op_id, "message", "create", {
            "id": op_id, "timeline_id": timeline, "role": "assistant" if sender != "user" else "user",
            "sender_id": sender, "text": f"{sender} 说的话", "attachment_kind": "",
            "attachment_transcript": "", "created_at": 2,
        }, sequence=sequence)

    async def test_seat_messages_go_back_to_the_seat_room(self):
        plan = await self.plan(self.seat_message("from-user", "user", sequence=1),
                               self.seat_message("from-ai3", "ai3", sequence=2))
        self.assertEqual(["apply", "apply"], [row.decision for row in plan.rows])
        self.assertEqual("room-ai3", plan.rows[1].effect_payload["container_id"])
        self.assertEqual("chatroom_messages", plan.rows[1].effect_payload["table"])

    async def test_someone_else_speaking_in_a_seat_room_is_held_back(self):
        plan = await self.plan(self.seat_message("wrong", "ai5"))
        self.assertEqual(("quarantine", "seat_sender_mismatch"), (plan.rows[0].decision, plan.rows[0].reason))

    async def test_unknown_timelines_are_not_trusted(self):
        plan = await self.plan(self.seat_message("evil", "user", timeline="seat_private:ai9"),
                               self.seat_message("other", "user", timeline="other", sequence=2))
        self.assertEqual(["quarantine", "quarantine"], [row.decision for row in plan.rows])
        self.assertTrue(all(row.reason == "timeline_mapping_unavailable" for row in plan.rows))

    async def test_group_message_from_a_seat_keeps_the_seat_as_sender(self):
        from homecoming.return_importer import _insert_message

        plan = await self.plan(self.seat_message("group-ai3", "ai3", timeline="group"))
        self.assertEqual("apply", plan.rows[0].decision)
        events = []

        async def durable(db, event):
            return event
        import homecoming.return_importer as importer
        original, importer._durable_event = importer._durable_event, durable
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await _insert_message(db, plan.rows[0].effect_payload, events)
                row = await (await db.execute(
                    "SELECT room_id, sender FROM chatroom_messages WHERE id='group-ai3'")).fetchone()
        finally:
            importer._durable_event = original
        self.assertEqual(("room-group", "ai3"), row)


# 父类的测试不在这里重复跑
for _name in [name for name in dir(HomecomingReturnPlannerTests) if name.startswith("test_")]:
    setattr(SeatReturnPlannerTests, _name, None)
del HomecomingReturnPlannerTests
