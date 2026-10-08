import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def local_ts(value: str) -> float:
    return datetime.strptime(value, "%Y-%m-%d %H:%M").timestamp()


class CalendarCompressionCandidateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "chat.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _connect(self):
        return aiosqlite.connect(self.db_path)

    async def asyncSetUp(self):
        async with self._connect() as db:
            await db.execute(
                "CREATE TABLE memories ("
                "id TEXT PRIMARY KEY, content TEXT NOT NULL, type TEXT, created_at REAL, "
                "source_conv TEXT, embedding BLOB, keywords TEXT, importance REAL, "
                "source_start_ts REAL, source_end_ts REAL, source_msg_id TEXT, "
                "compression_stage INTEGER DEFAULT 0, archive_state TEXT DEFAULT 'active', "
                "period_kind TEXT DEFAULT '', period_start_ts REAL, period_end_ts REAL, "
                "compression_batch_id TEXT DEFAULT '')"
            )
            await db.execute(
                "CREATE TABLE chatroom_memories ("
                "id TEXT PRIMARY KEY, room_id TEXT, scope TEXT, content TEXT NOT NULL, "
                "keywords TEXT, importance REAL, embedding BLOB, source_start_ts REAL, "
                "source_end_ts REAL, created_at REAL, unresolved INTEGER DEFAULT 0, "
                "source_msg_id TEXT, memory_kind TEXT DEFAULT 'daily', "
                "compression_stage INTEGER DEFAULT 0, archive_state TEXT DEFAULT 'active', "
                "period_kind TEXT DEFAULT '', period_start_ts REAL, period_end_ts REAL, "
                "compression_batch_id TEXT DEFAULT '')"
            )
            await db.commit()

    async def _insert_main(self, mem_id, when, *, stage=0, archive_state="active", period_kind=""):
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO memories ("
                "id, content, type, created_at, source_start_ts, source_end_ts, source_msg_id, "
                "compression_stage, archive_state, period_kind"
                ") VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    mem_id,
                    f"memory {mem_id}",
                    "daily",
                    when,
                    when,
                    when,
                    json.dumps([f"private:{mem_id}"]),
                    stage,
                    archive_state,
                    period_kind,
                ),
            )
            await db.commit()

    async def test_memory_day_uses_five_am_boundary(self):
        import memory_compression

        before = memory_compression.memory_day_for_ts(local_ts("2026-07-09 04:59"))
        after = memory_compression.memory_day_for_ts(local_ts("2026-07-09 05:00"))

        self.assertEqual(before, "2026-07-08")
        self.assertEqual(after, "2026-07-09")

    async def test_five_am_makes_the_eighth_day_eligible_in_each_store(self):
        import memory_compression

        for day in (24, 25, 26):
            await self._insert_main(f"day-{day}", local_ts(f"2026-09-{day} 12:00"))
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO chatroom_memories (id, content, created_at, source_start_ts, source_end_ts) "
                "SELECT id, content, created_at, source_start_ts, source_end_ts FROM memories"
            )
            await db.commit()

        with patch.object(memory_compression, "get_db", self._connect):
            for target in ("main", "chatroom"):
                with self.subTest(target=target):
                    before = await memory_compression.compression_preview(
                        target, "daily", now_ts=local_ts("2026-10-03 04:59")
                    )
                    ready = await memory_compression.compression_preview(
                        target, "daily", now_ts=local_ts("2026-10-03 05:00")
                    )
                    self.assertEqual([p["label"] for p in before["periods"]], ["2026-09-24"])
                    self.assertEqual([p["label"] for p in ready["periods"]], ["2026-09-24", "2026-09-25"])

    async def test_history_keeps_old_counts_filters_store_and_pages_successful_batches(self):
        import memory_compression

        with patch.object(memory_compression, "get_db", self._connect):
            await memory_compression.ensure_calendar_compression_schema()
            async with self._connect() as db:
                for batch_id, target, status, completed in (
                    ("old", "main", "completed", 1), ("new", "main", "completed", 2),
                    ("pending", "main", "applying", 3), ("other", "chatroom", "completed", 4),
                ):
                    await db.execute(
                        "INSERT INTO memory_compression_batches "
                        "(id,target,level,model_key,status,period_start_ts,period_end_ts,input_count,output_count,created_at,completed_at) "
                        "VALUES (?,?,'daily','model',?,?,?,20,5,0,?)",
                        (batch_id, target, status, local_ts("2026-08-29 05:00"), local_ts("2026-09-01 05:00"), completed),
                    )
                await db.commit()
            first = await memory_compression.list_compression_history("main", limit=1)
            older = await memory_compression.list_compression_history("main", limit=1, offset=1)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["items"][0]["id"], "new")
        self.assertEqual(first["items"][0]["period_label"], "2026-08-29～2026-08-31")
        self.assertEqual(first["items"][0]["delta"], -15)
        self.assertEqual(first["items"][0]["reduction_percent"], 75)
        self.assertIsNone(first["items"][0]["durable_output_count"])
        self.assertEqual(older["items"][0]["id"], "old")
        self.assertFalse(older["has_more"])

    async def test_chatroom_compression_uses_its_own_persona_and_leaves_event_without_reflection(self):
        import chatroom
        import memory_compression

        async with self._connect() as db:
            await db.execute("ALTER TABLE chatroom_memories ADD COLUMN evidence_summary TEXT DEFAULT ''")
            await db.execute("ALTER TABLE chatroom_memories ADD COLUMN evidence_detail_level TEXT DEFAULT 'summary'")
            await db.execute(
                "INSERT INTO chatroom_memories (id,content,created_at,memory_kind) VALUES ('old','一起做饭',?,'daily')",
                (local_ts("2026-07-08 10:00"),),
            )
            await db.commit()
        model = AsyncMock(return_value={
            "periods": [{"period": "2026-07-08", "memories": [{
                "content": "一起做饭", "source_memory_ids": ["M1"]
            }]}]
        })
        with patch.object(memory_compression, "get_db", self._connect), patch.object(
            memory_compression, "MODELS", {"cheap": {"provider": "test"}}
        ), patch.object(memory_compression, "_call_compression_model", model), patch.object(
            memory_compression, "_embedding_for_content", AsyncMock(return_value=None)
        ), patch.object(memory_compression, "load_worldbook", return_value={
            "user_name": "小雨", "ai_persona": "不该带入的主角色设定"
        }), patch.object(chatroom, "load_chatroom_config", return_value={"connor_name": "远帆"}), patch.object(
            chatroom, "_read_connor_persona", return_value="稳重，偶尔调侃"
        ):
            result = await memory_compression.run_calendar_compression(
                "chatroom", "daily", "cheap", now_ts=local_ts("2026-07-16 05:00")
            )
            events = await memory_compression.list_compression_events(since=0)
            async with self._connect() as db:
                output = await (await db.execute(
                    "SELECT source_memory_ids FROM chatroom_memories WHERE compression_batch_id!=''"
                )).fetchone()
        self.assertTrue(result["ok"])
        self.assertEqual(json.loads(output[0]), ["old"])
        model.assert_awaited_once()
        prompt = model.await_args.args[1]
        self.assertIn("远帆", prompt)
        self.assertIn("稳重，偶尔调侃", prompt)
        self.assertNotIn("不该带入的主角色设定", prompt)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["author"], "connor")
        self.assertEqual(events[0]["reflection"], "")
        self.assertIn("远帆", events[0]["title"])

    async def test_daily_preview_only_counts_active_stage_zero_days_older_than_seven_days(self):
        import memory_compression

        now = local_ts("2026-07-16 05:00")
        await self._insert_main("eligible-a", local_ts("2026-07-08 10:00"))
        await self._insert_main("eligible-b", local_ts("2026-07-08 23:00"))
        await self._insert_main("too-new", local_ts("2026-07-09 10:00"))
        await self._insert_main("already-daily", local_ts("2026-07-01 10:00"), stage=1, period_kind="day")
        await self._insert_main("cold", local_ts("2026-07-01 10:00"), archive_state="cold")

        with patch.object(memory_compression, "get_db", self._connect):
            preview = await memory_compression.compression_preview("main", "daily", now_ts=now)

        self.assertEqual(preview["memory_count"], 2)
        self.assertEqual(preview["period_count"], 1)
        self.assertEqual(preview["periods"][0]["label"], "2026-07-08")
        self.assertEqual(preview["periods"][0]["memory_count"], 2)

    async def test_weekly_preview_waits_until_the_whole_week_is_ninety_days_old(self):
        import memory_compression

        # July 6-12 ends July 13 at 05:00; it reaches 90 days on October 11.
        await self._insert_main("eligible-week", local_ts("2026-07-08 10:00"), stage=1, period_kind="day")
        await self._insert_main("newer-week", local_ts("2026-07-14 10:00"), stage=1, period_kind="day")

        with patch.object(memory_compression, "get_db", self._connect):
            before = await memory_compression.compression_preview(
                "main", "weekly", now_ts=local_ts("2026-10-11 04:59")
            )
            ready = await memory_compression.compression_preview(
                "main", "weekly", now_ts=local_ts("2026-10-11 05:00")
            )

        self.assertEqual(before["memory_count"], 0)
        self.assertEqual(ready["memory_count"], 1)
        self.assertEqual(ready["periods"][0]["label"], "2026-07-06 ~ 2026-07-12")

    async def test_weekly_preview_requires_no_uncompressed_daily_rows_in_the_week(self):
        import memory_compression

        now = local_ts("2026-10-11 05:00")
        await self._insert_main("compressed-day", local_ts("2026-07-08 10:00"), stage=1, period_kind="day")
        await self._insert_main("raw-day", local_ts("2026-07-09 10:00"), stage=0)

        with patch.object(memory_compression, "get_db", self._connect):
            blocked = await memory_compression.compression_preview("main", "weekly", now_ts=now)

        self.assertEqual(blocked["memory_count"], 0)
        self.assertEqual(blocked["period_count"], 0)

        async with self._connect() as db:
            await db.execute(
                "UPDATE memories SET archive_state='cold' WHERE id='raw-day'"
            )
            await db.commit()

        with patch.object(memory_compression, "get_db", self._connect):
            preview = await memory_compression.compression_preview("main", "weekly", now_ts=now)

        self.assertEqual(preview["memory_count"], 1)
        self.assertEqual(preview["period_count"], 1)
        self.assertEqual(preview["periods"][0]["label"], "2026-07-06 ~ 2026-07-12")

    async def test_monthly_preview_waits_until_the_whole_month_is_365_days_old(self):
        import memory_compression

        # June 2025 ends July 1 at 05:00; its age is measured from that boundary.
        await self._insert_main("june-week", local_ts("2025-06-18 10:00"), stage=2, period_kind="week")
        await self._insert_main("july-week", local_ts("2026-07-09 10:00"), stage=2, period_kind="week")

        with patch.object(memory_compression, "get_db", self._connect):
            before = await memory_compression.compression_preview(
                "main", "monthly", now_ts=local_ts("2026-07-01 04:59")
            )
            ready = await memory_compression.compression_preview(
                "main", "monthly", now_ts=local_ts("2026-07-01 05:00")
            )

        self.assertEqual(before["memory_count"], 0)
        self.assertEqual(ready["memory_count"], 1)
        self.assertEqual(ready["periods"][0]["label"], "2025-06")

    async def test_monthly_preview_waits_until_no_daily_capsules_remain(self):
        import memory_compression

        now = local_ts("2026-07-16 12:00")
        await self._insert_main("june-week", local_ts("2025-06-18 10:00"), stage=2, period_kind="week")
        await self._insert_main("june-day", local_ts("2025-06-20 10:00"), stage=1, period_kind="day")

        with patch.object(memory_compression, "get_db", self._connect):
            preview = await memory_compression.compression_preview("main", "monthly", now_ts=now)

        self.assertEqual(preview["memory_count"], 0)
        self.assertEqual(preview["period_count"], 0)

    async def test_legacy_compressed_daily_memories_become_stage_one_day_capsules_only(self):
        import memory_compression

        old_day = local_ts("2026-06-20 00:00")
        await self._insert_main("legacy-stage-one", old_day, stage=1)
        await self._insert_main("legacy-stage-two", old_day, stage=2)
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO memories ("
                "id, content, type, created_at, source_start_ts, source_end_ts, "
                "compression_stage, archive_state, period_kind"
                ") VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "long-term",
                    "长期重要",
                    "important",
                    old_day,
                    old_day,
                    old_day,
                    1,
                    "active",
                    "",
                ),
            )
            await db.commit()

        with patch.object(memory_compression, "get_db", self._connect):
            result = await memory_compression.migrate_legacy_daily_capsules()

        self.assertEqual(result["main"], 2)
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            migrated = await (
                await db.execute(
                    "SELECT id, compression_stage, period_kind, period_start_ts, period_end_ts "
                    "FROM memories WHERE id LIKE 'legacy-%' ORDER BY id"
                )
            ).fetchall()
            important = await (
                await db.execute(
                    "SELECT compression_stage, period_kind FROM memories WHERE id='long-term'"
                )
            ).fetchone()

        self.assertEqual([row["compression_stage"] for row in migrated], [1, 1])
        self.assertEqual([row["period_kind"] for row in migrated], ["day", "day"])
        self.assertEqual(
            datetime.fromtimestamp(migrated[0]["period_start_ts"]).strftime("%Y-%m-%d %H:%M"),
            "2026-06-20 05:00",
        )
        self.assertEqual(
            datetime.fromtimestamp(migrated[0]["period_end_ts"]).strftime("%Y-%m-%d %H:%M"),
            "2026-06-21 05:00",
        )
        self.assertEqual(important["compression_stage"], 1)
        self.assertEqual(important["period_kind"], "")


class CalendarCompressionRunTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "chat.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _connect(self):
        return aiosqlite.connect(self.db_path)

    async def asyncSetUp(self):
        async with self._connect() as db:
            await db.execute(
                "CREATE TABLE memories ("
                "id TEXT PRIMARY KEY, content TEXT NOT NULL, type TEXT, created_at REAL, "
                "source_conv TEXT, embedding BLOB, keywords TEXT, importance REAL, "
                "source_start_ts REAL, source_end_ts REAL, unresolved INTEGER DEFAULT 0, "
                "source_msg_id TEXT, compression_stage INTEGER DEFAULT 0, "
                "evidence_summary TEXT DEFAULT '', evidence_detail_level TEXT DEFAULT 'summary', "
                "archive_state TEXT DEFAULT 'active', archived_at REAL, period_kind TEXT DEFAULT '', "
                "period_start_ts REAL, period_end_ts REAL, compression_batch_id TEXT DEFAULT '')"
            )
            await db.commit()

    async def _insert(self, mem_id, when):
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO memories ("
                "id, content, type, created_at, source_start_ts, source_end_ts, source_msg_id"
                ") VALUES (?,?,?,?,?,?,?)",
                (
                    mem_id,
                    "一条不包含数据库标识的摘要内容",
                    "daily",
                    when,
                    when,
                    when,
                    json.dumps([f"private:{mem_id}"]),
                ),
            )
            await db.commit()

    async def test_zero_candidates_do_not_call_model(self):
        import memory_compression

        model_call = AsyncMock()
        with patch.object(memory_compression, "get_db", self._connect), patch.object(
            memory_compression, "_call_compression_model", model_call
        ), patch.object(memory_compression, "MODELS", {"cheap": {"provider": "test"}}):
            result = await memory_compression.run_calendar_compression(
                "main", "daily", "cheap", now_ts=local_ts("2026-07-16 05:00")
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_candidates")
        model_call.assert_not_awaited()

    async def test_invalid_reflection_and_failed_refresh_do_not_fail_compression(self):
        import memory_compression
        from ws import manager

        for index, reflection in enumerate((None, {"bad": "shape"}, ["wrong"], 42)):
            with self.subTest(reflection=reflection):
                await self._insert(f"input-{index}", local_ts("2026-07-08 10:00"))
                model = AsyncMock(return_value={
                    "periods": [{"period": "2026-07-08", "memories": [{"content": "保留的重要经历"}]}],
                    "reflection": reflection,
                })
                with patch.object(memory_compression, "get_db", self._connect), patch.object(
                    memory_compression, "MODELS", {"cheap": {"provider": "test"}}
                ), patch.object(memory_compression, "_call_compression_model", model), patch.object(
                    memory_compression, "_embedding_for_content", AsyncMock(return_value=None)
                ), patch.object(manager, "broadcast", AsyncMock(side_effect=RuntimeError("offline"))):
                    result = await memory_compression.run_calendar_compression(
                        "main", "daily", "cheap", now_ts=local_ts("2026-07-16 05:00")
                    )
                    events = await memory_compression.list_compression_events(since=0)
                self.assertTrue(result["ok"])
                model.assert_awaited_once()
                self.assertEqual(len(events), index + 1)
                self.assertEqual(events[0]["reflection"], "")
                self.assertIn("2026-07-08", events[0]["title"])
                self.assertTrue(events[0]["detail"])

    async def test_concurrent_runs_recheck_candidates_before_calling_model(self):
        import asyncio
        import memory_compression

        await self._insert("shared-input", local_ts("2026-07-08 10:00"))
        entered = asyncio.Event()
        release = asyncio.Event()

        async def generate(*args):
            entered.set()
            await release.wait()
            return {"periods": [{"period": "2026-07-08", "memories": [{"content": "保留的经历"}]}]}

        model_call = AsyncMock(side_effect=generate)
        with patch.object(memory_compression, "get_db", self._connect), patch.object(
            memory_compression, "_RUN_LOCKS", {}
        ), patch.object(memory_compression, "MODELS", {"cheap": {"provider": "test"}}), patch.object(
            memory_compression, "_call_compression_model", model_call
        ), patch.object(memory_compression, "_embedding_for_content", AsyncMock(return_value=None)):
            first = asyncio.create_task(memory_compression.run_calendar_compression(
                "main", "daily", "cheap", now_ts=local_ts("2026-07-16 05:00")
            ))
            await asyncio.wait_for(entered.wait(), timeout=5)
            second = asyncio.create_task(memory_compression.run_calendar_compression(
                "main", "daily", "cheap", now_ts=local_ts("2026-07-16 05:00")
            ))
            await asyncio.sleep(0)
            release.set()
            first_result, second_result = await asyncio.gather(first, second)
        self.assertTrue(first_result["ok"])
        self.assertEqual(second_result["reason"], "no_candidates")
        model_call.assert_awaited_once()

    async def test_background_job_persists_progress_and_blocks_duplicate_start(self):
        import memory_compression

        await self._insert("job-input", local_ts("2026-07-08 10:00"))
        release = __import__("asyncio").Event()

        async def fake_run(target, level, model_key, *, now_ts=None, progress_callback=None):
            if progress_callback:
                await progress_callback({
                    "completed_calls": 0,
                    "total_calls": 1,
                    "processed_inputs": 0,
                    "created_outputs": 0,
                    "message": "正在调用模型",
                })
            await release.wait()
            return {
                "ok": True,
                "input_count": 1,
                "output_count": 1,
                "model_calls": 1,
                "message": "完成",
            }

        with patch.object(memory_compression, "get_db", self._connect), patch.object(
            memory_compression, "run_calendar_compression", fake_run
        ), patch.object(memory_compression, "MODELS", {"cheap": {"provider": "test"}}):
            first = await memory_compression.create_calendar_compression_job(
                "main", "daily", "cheap"
            )
            await __import__("asyncio").sleep(0.05)
            duplicate = await memory_compression.create_calendar_compression_job(
                "main", "daily", "cheap"
            )
            running = await memory_compression.get_calendar_compression_job(first["job"]["id"])
            release.set()
            await __import__("asyncio").sleep(0.05)
            completed = await memory_compression.get_calendar_compression_job(first["job"]["id"])

        self.assertTrue(first["ok"])
        self.assertEqual(first["job"]["status"], "queued")
        self.assertFalse(duplicate["ok"])
        self.assertEqual(duplicate["reason"], "already_running")
        self.assertEqual(running["status"], "running")
        self.assertEqual(running["progress"]["message"], "正在调用模型")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["result"]["output_count"], 1)

    async def test_valid_run_archives_inputs_and_records_precise_lineage(self):
        import memory_compression

        when = local_ts("2026-07-08 10:00")
        await self._insert("old-1", when)
        await self._insert("old-2", when + 60)
        model_call = AsyncMock(
            return_value={
                "periods": [
                    {
                        "period": "2026-07-08",
                        "memories": [
                            {
                                "content": "2026-07-08，完成了值得保留的一件事。",
                                "keywords": ["项目"],
                                "importance": 0.55,
                                "source_memory_ids": ["M1", "M1"],
                            }
                        ],
                    }
                ]
            }
        )
        with patch.object(memory_compression, "get_db", self._connect), patch.object(
            memory_compression, "_call_compression_model", model_call
        ), patch.object(memory_compression, "MODELS", {"cheap": {"provider": "test"}}), patch.object(
            memory_compression, "_embedding_for_content", AsyncMock(return_value=None)
        ):
            result = await memory_compression.run_calendar_compression(
                "main", "daily", "cheap", now_ts=local_ts("2026-07-16 05:00")
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["input_count"], 2)
        self.assertEqual(result["output_count"], 1)
        sent_prompt = model_call.await_args.args[1]
        self.assertNotIn('"id"', sent_prompt)
        self.assertNotIn("private:", sent_prompt)
        self.assertNotIn("old-1", sent_prompt)
        self.assertNotIn("old-2", sent_prompt)

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            old_rows = await (
                await db.execute("SELECT id, archive_state, source_msg_id FROM memories WHERE id LIKE 'old-%'")
            ).fetchall()
            output = await (
                await db.execute(
                    "SELECT id, archive_state, compression_stage, period_kind, compression_batch_id "
                    "FROM memories WHERE id NOT LIKE 'old-%'"
                )
            ).fetchone()
            input_links = await (
                await db.execute(
                    "SELECT memory_id FROM memory_compression_batch_inputs ORDER BY memory_id"
                )
            ).fetchall()

        self.assertEqual([row["archive_state"] for row in old_rows], ["cold", "cold"])
        self.assertEqual(json.loads(old_rows[0]["source_msg_id"]), ["private:old-1"])
        self.assertEqual(output["archive_state"], "active")
        self.assertEqual(output["compression_stage"], 1)
        self.assertEqual(output["period_kind"], "day")
        self.assertTrue(output["compression_batch_id"])
        self.assertEqual([row["memory_id"] for row in input_links], ["old-1", "old-2"])

        with patch.object(memory_compression, "get_db", self._connect):
            source_ids = await memory_compression.resolve_source_message_ids("main", output["id"])
        self.assertEqual(source_ids, ["private:old-1"])

    async def test_invalid_or_missing_lineage_keeps_original_memories_active(self):
        import memory_compression

        await self._insert('input-a', local_ts('2026-07-08 10:00'))
        await self._insert('input-b', local_ts('2026-07-08 11:00'))
        for parents in (None, [], ['M99'], ['input-a'], 'M1', [1]):
            model = AsyncMock(return_value={'periods': [{'period': '2026-07-08', 'memories': [
                {'content': '一起做饭', 'source_memory_ids': parents}
            ]}]})
            with patch.object(memory_compression, 'get_db', self._connect), patch.object(
                memory_compression, 'MODELS', {'cheap': {'provider': 'test'}}
            ), patch.object(memory_compression, '_call_compression_model', model):
                result = await memory_compression.run_calendar_compression(
                    'main', 'daily', 'cheap', now_ts=local_ts('2026-07-16 05:00'))
            self.assertEqual(result['reason'], 'invalid_source_lineage')
        async with self._connect() as db:
            rows = await (await db.execute('SELECT archive_state FROM memories')).fetchall()
        self.assertEqual(rows, [('active',), ('active',)])

    async def test_short_refs_cannot_link_memories_from_another_period(self):
        import memory_compression

        await self._insert('day-one', local_ts('2026-07-07 10:00'))
        await self._insert('day-two', local_ts('2026-07-08 10:00'))
        model = AsyncMock(return_value={'periods': [
            {'period': '2026-07-07', 'memories': [
                {'content': '保留的经历', 'source_memory_ids': ['M2']}
            ]},
            {'period': '2026-07-08', 'memories': []},
        ]})
        with patch.object(memory_compression, 'get_db', self._connect), patch.object(
            memory_compression, 'MODELS', {'cheap': {'provider': 'test'}}
        ), patch.object(memory_compression, '_call_compression_model', model):
            result = await memory_compression.run_calendar_compression(
                'main', 'daily', 'cheap', now_ts=local_ts('2026-07-16 05:00'))
        self.assertEqual(result['reason'], 'invalid_source_lineage')
        async with self._connect() as db:
            rows = await (await db.execute('SELECT archive_state FROM memories')).fetchall()
        self.assertEqual(rows, [('active',), ('active',)])

    async def test_reflection_uses_same_call_and_event_is_not_duplicated(self):
        import memory_compression

        await self._insert("reflect-input", local_ts("2026-07-08 10:00"))
        await self._insert("reflect-input-2", local_ts("2026-07-08 11:00"))
        reflection = "那天的努力，我记住了。"
        model = AsyncMock(return_value={
            "periods": [{"period": "2026-07-08", "memories": [{"content": "完成一个项目", "source_memory_ids": ["M1"]}]}],
            "durable_facts": [{"content": "喜欢一起做项目", "source_memory_ids": ["M2"]}],
            "reflection": reflection,
        })
        with patch.object(memory_compression, "get_db", self._connect), patch.object(
            memory_compression, "MODELS", {"cheap": {"provider": "test"}}
        ), patch.object(memory_compression, "_call_compression_model", model), patch.object(
            memory_compression, "_embedding_for_content", AsyncMock(return_value=None)
        ), patch.object(memory_compression, "load_worldbook", return_value={
            "ai_name": "星舟", "user_name": "小雨", "ai_persona": "沉静温柔，喜欢园艺"
        }):
            result = await memory_compression.run_calendar_compression(
                "main", "daily", "cheap", now_ts=local_ts("2026-07-16 05:00")
            )
            first = await memory_compression.list_compression_events(since=0)
            again = await memory_compression.list_compression_events(since=0)
            history = await memory_compression.list_compression_history("main")
        self.assertTrue(result["ok"])
        model.assert_awaited_once()
        prompt = model.await_args.args[1]
        for value in ("星舟", "小雨", "沉静温柔，喜欢园艺"):
            self.assertIn(value, prompt)
        self.assertEqual(first, again)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["reflection"], reflection)
        self.assertEqual(first[0]["author"], "aion")
        self.assertIn("星舟", first[0]["title"])
        record = history["items"][0]
        self.assertEqual((record["input_count"], record["output_count"]), (2, 2))
        self.assertEqual((record["memory_output_count"], record["durable_output_count"]), (1, 1))
        self.assertEqual(record["delta"], 0)
        async with self._connect() as db:
            rows = await (await db.execute("SELECT content FROM memories")).fetchall()
            outputs = await (await db.execute(
                "SELECT content,source_memory_ids FROM memories WHERE compression_batch_id!=''"
            )).fetchall()
        self.assertNotIn(reflection, [row[0] for row in rows])
        self.assertEqual({content: json.loads(parents) for content, parents in outputs}, {
            '完成一个项目': ['reflect-input'], '喜欢一起做项目': ['reflect-input-2'],
        })

    async def test_expanded_output_keeps_inputs_active_without_extra_calls_or_events(self):
        import memory_compression

        await self._insert("keep-original", local_ts("2026-07-08 10:00"))
        for response in (
            {"memories": [{"content": "经历"}], "durable_facts": [{"content": "承诺"}]},
            {"memories": [{"content": "很冗长的情感扩写" * 20}]},
        ):
            with self.subTest(response=response):
                model = AsyncMock(return_value={
                    "periods": [{"period": "2026-07-08", "memories": response["memories"]}],
                    "durable_facts": response.get("durable_facts", []),
                })
                embedding = AsyncMock()
                with patch.object(memory_compression, "get_db", self._connect), patch.object(
                    memory_compression, "MODELS", {"cheap": {"provider": "test"}}
                ), patch.object(memory_compression, "_call_compression_model", model), patch.object(
                    memory_compression, "_embedding_for_content", embedding
                ):
                    result = await memory_compression.run_calendar_compression(
                        "main", "daily", "cheap", now_ts=local_ts("2026-07-16 05:00")
                    )
                    self.assertEqual(await memory_compression.list_compression_events(since=0), [])
                self.assertEqual(result["reason"], "compression_expanded")
                self.assertFalse(result["ok"])
                model.assert_awaited_once()
                embedding.assert_not_awaited()
                async with self._connect() as db:
                    rows = await (await db.execute("SELECT id, archive_state FROM memories")).fetchall()
                self.assertEqual(rows, [("keep-original", "active")])

    async def test_missing_period_in_model_output_keeps_every_input_active(self):
        import memory_compression

        await self._insert("day-one", local_ts("2026-07-07 10:00"))
        await self._insert("day-two", local_ts("2026-07-08 10:00"))
        model_call = AsyncMock(
            return_value={
                "periods": [
                    {
                        "period": "2026-07-07",
                        "memories": [{"content": "只返回了一天。", "keywords": [], "importance": 0.4}],
                    }
                ]
            }
        )
        with patch.object(memory_compression, "get_db", self._connect), patch.object(
            memory_compression, "_call_compression_model", model_call
        ), patch.object(memory_compression, "MODELS", {"cheap": {"provider": "test"}}):
            result = await memory_compression.run_calendar_compression(
                "main", "daily", "cheap", now_ts=local_ts("2026-07-16 05:00")
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid_model_output")
        async with self._connect() as db:
            states = await (
                await db.execute("SELECT archive_state FROM memories ORDER BY id")
            ).fetchall()
        self.assertEqual([row[0] for row in states], ["active", "active"])
        with patch.object(memory_compression, "get_db", self._connect):
            self.assertEqual(await memory_compression.list_compression_events(since=0), [])


class CompressionPromptTests(unittest.TestCase):
    def test_levels_include_task_source_dates_budget_and_memory_ids(self):
        import memory_compression

        rows = {"private-row": {"content": "一起做饭，虽然烧糊了却笑得很开心。", "source_start_ts": local_ts("2025-06-18 12:00")}}
        for level, label, start, end, task, source_period in (
            ("daily", "2025-06-18", "2025-06-18", "2025-06-19", "日记忆压缩", "2025-06-18"),
            ("weekly", "2025-06-16 ~ 2025-06-22", "2025-06-16", "2025-06-23", "周记忆压缩", "2025-06-18"),
            ("monthly", "2025-06", "2025-06-01", "2025-07-01", "月记忆压缩", "2025-06-16 ~ 2025-06-22"),
        ):
            with self.subTest(level=level):
                periods = [{"label": label, "period_start_ts": local_ts(start + " 05:00"),
                            "period_end_ts": local_ts(end + " 05:00"), "memory_ids": ["private-row"]}]
                prompt = memory_compression._period_prompt(level, periods, rows, {"name": "星舟", "persona": "喜欢调侃"})
                payload = json.loads(prompt.rsplit("输入：", 1)[1])[0]
                self.assertIn(task, prompt)
                self.assertIn("喜欢调侃", prompt)
                self.assertEqual(payload["memories"][0]["memory_id"], "M1")
                self.assertNotIn("private-row", prompt)
                self.assertEqual(payload["period"], label)
                self.assertEqual(payload["start"], start + "T05:00")
                self.assertEqual(payload["end_exclusive"], end + "T05:00")
                self.assertEqual(payload["max_memories"], 1)
                self.assertEqual(payload["memories"][0]["source_period"], source_period)
                self.assertEqual(payload["memories"][0]["content"], rows["private-row"]["content"])
                self.assertEqual(memory_compression._compression_budget(periods, rows),
                                 {"max_output_count": 1, "max_content_chars": len(rows["private-row"]["content"])})

    def test_short_refs_are_unique_across_periods_and_reset_for_each_call(self):
        import memory_compression

        rows = {mem_id: {'content': '保留的经历', 'created_at': local_ts(day + ' 10:00')}
                for mem_id, day in [('a', '2026-07-07'), ('b', '2026-07-07'), ('c', '2026-07-08')]}
        periods = [{'label': day, 'period_start_ts': local_ts(day + ' 05:00'),
                    'period_end_ts': local_ts(end + ' 05:00'), 'memory_ids': ids}
                   for day, end, ids in [('2026-07-07', '2026-07-08', ['a', 'b']),
                                         ('2026-07-08', '2026-07-09', ['c'])]]
        prompt = memory_compression._period_prompt('daily', periods, rows)
        payload = json.loads(prompt.rsplit('输入：', 1)[1])
        self.assertEqual([[m['memory_id'] for m in p['memories']] for p in payload],
                         [['M1', 'M2'], ['M3']])
        next_prompt = memory_compression._period_prompt('daily', periods[1:], rows)
        next_payload = json.loads(next_prompt.rsplit('输入：', 1)[1])
        self.assertEqual(next_payload[0]['memories'][0]['memory_id'], 'M1')


class ColdArchiveRecallSafetyTests(unittest.TestCase):
    def test_main_recall_queries_filter_cold_archive(self):
        source = (ROOT / "memory.py").read_text(encoding="utf-8")

        self.assertGreaterEqual(
            source.count("COALESCE(archive_state,'active')='active'"),
            4,
        )

    def test_chatroom_recall_queries_filter_cold_archive(self):
        source = (ROOT / "chatroom.py").read_text(encoding="utf-8")

        self.assertGreaterEqual(
            source.count("COALESCE(archive_state,'active')='active'"),
            4,
        )


if __name__ == "__main__":
    unittest.main()
