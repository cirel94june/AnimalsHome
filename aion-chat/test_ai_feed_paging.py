"""Codex §114：同一时刻多条会卡住翻页（R1）、记忆整理先截断再筛人（R2）、认领后进静默还会醒（R4）。"""

import sqlite3
import tempfile
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite

from routes import activity


def _timeline_patches(db_path, compression=None):
    @asynccontextmanager
    async def fake_db():
        async with aiosqlite.connect(db_path) as db:
            yield db

    return [
        patch.object(activity, "get_db", fake_db),
        patch.object(activity, "load_worldbook", return_value={"user_name": "小猫", "ai_name": "小克"}),
        patch.object(activity, "load_chatroom_config", return_value={"connor_name": "Lucien"}),
        patch.object(activity, "seat_names", return_value={"ai3": "狗蛋"}),
        patch.object(activity, "list_grouped_user_events", new=AsyncMock(return_value=[])),
        patch.object(activity, "list_compression_events", new=compression or AsyncMock(return_value=[])),
        patch.object(activity, "load_location_status", return_value={}),
    ]


class SameTimestampPagingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "chat.db")
        same = time.time() - 600
        db = sqlite3.connect(self.db_path)
        db.executescript("""
            CREATE TABLE moments (id TEXT, author TEXT, content TEXT, attachments TEXT, created_at REAL);
            CREATE TABLE diary_entries (id TEXT, author TEXT, title TEXT, content TEXT, created_at REAL);
            CREATE TABLE gifts (id TEXT, image_path TEXT, message TEXT, created_at REAL, sender TEXT);
            CREATE TABLE idle_events (id TEXT, actor TEXT, action TEXT, title TEXT, detail TEXT, target_type TEXT,
                target_id TEXT, result_type TEXT, result_id TEXT, metadata TEXT, created_at REAL);
        """)
        for i in range(41):  # 41 篇日记同一时刻
            db.execute("INSERT INTO diary_entries VALUES (?,?,?,?,?)", (f"d{i:02d}", "aion", f"第{i}篇", "x", same))
        db.execute("INSERT INTO moments VALUES ('m1','aion','同一刻的朋友圈','[]',?)", (same,))
        db.commit()
        db.close()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def timeline(self, **kwargs):
        patches = _timeline_patches(self.db_path)
        for item in patches:
            item.start()
        try:
            return await activity.get_timeline(**kwargs)
        finally:
            for item in patches:
                item.stop()

    async def test_every_item_is_reached_once_even_when_all_share_one_timestamp(self):
        seen, cursor, pages = [], None, 0
        while True:
            extra = {} if cursor is None else {"before": cursor["before"], "before_kind": cursor["kind"],
                                               "before_id": cursor["id"]}
            data = await self.timeline(hours=24, limit=40, who="ai", **extra)
            seen.extend(f"{item['kind']}:{item['source_id']}" for item in data["items"])
            cursor = data["next_cursor"]
            pages += 1
            if cursor is None or pages > 5:
                break
        self.assertEqual(2, pages)  # 40 + 2，不会一直原地打转
        self.assertEqual(42, len(seen))
        self.assertEqual(len(seen), len(set(seen)))


class CompressionWiringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "chat.db")
        db = sqlite3.connect(self.db_path)
        db.executescript("""
            CREATE TABLE moments (id TEXT, author TEXT, content TEXT, attachments TEXT, created_at REAL);
            CREATE TABLE diary_entries (id TEXT, author TEXT, title TEXT, content TEXT, created_at REAL);
            CREATE TABLE gifts (id TEXT, image_path TEXT, message TEXT, created_at REAL, sender TEXT);
            CREATE TABLE idle_events (id TEXT, actor TEXT, action TEXT, title TEXT, detail TEXT, target_type TEXT,
                target_id TEXT, result_type TEXT, result_id TEXT, metadata TEXT, created_at REAL);
        """)
        db.close()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_compression_is_filtered_at_the_source(self):
        fake = AsyncMock(return_value=[])
        patches = _timeline_patches(self.db_path, compression=fake)
        for item in patches:
            item.start()
        try:
            await activity.get_timeline(hours=24, limit=5, who="connor")
            self.assertEqual("chatroom", fake.await_args.kwargs["target"])
            self.assertEqual(6, fake.await_args.kwargs["limit"])  # 多取一条
            await activity.get_timeline(hours=24, limit=5, who="aion", before=100.0, before_kind="memory_compression",
                                        before_id="b5")
            self.assertEqual((100.0, "b5"), fake.await_args.kwargs["cursor"])
            fake.reset_mock()
            await activity.get_timeline(hours=24, limit=5, who="ai3")
            fake.assert_not_awaited()  # 座位没有记忆整理，不去查
        finally:
            for item in patches:
                item.stop()

    async def test_reading_batches_filters_target_and_cursor_before_limit(self):
        import memory_compression

        path = str(Path(self.tmp.name) / "mc.db")
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE memory_compression_batches (id TEXT PRIMARY KEY, target TEXT, status TEXT, completed_at REAL)")
        now = time.time()
        for i in range(5):  # 最新 5 条都是小克的
            db.execute("INSERT INTO memory_compression_batches VALUES (?,?,?,?)", (f"m{i}", "main", "completed", now - i))
        db.execute("INSERT INTO memory_compression_batches VALUES ('c1','chatroom','completed',?)", (now - 100,))
        for i in range(3):  # 同一时刻的三条
            db.execute("INSERT INTO memory_compression_batches VALUES (?,?,?,?)", (f"t{i}", "main", "completed", now - 50))
        db.commit()
        db.close()

        @asynccontextmanager
        async def fake_db():
            async with aiosqlite.connect(path) as conn:
                yield conn

        with patch.object(memory_compression, "get_db", fake_db):
            rows = await memory_compression._read_completed_batches(since=0, target="chatroom", limit=1, text_order=True)
            self.assertEqual(["c1"], [r["id"] for r in rows])  # 不再被小克的 5 条挤掉
            rows = await memory_compression._read_completed_batches(
                since=0, target="main", limit=10, cursor=(now - 50, "t1"), text_order=True)
            self.assertEqual(["t0"], [r["id"] for r in rows])  # 同一刻里只要编号更小的


class ClaimThenQuietTests(unittest.IsolatedAsyncioTestCase):
    async def test_claimed_wake_is_put_back_when_quiet_starts_meanwhile(self):
        import autonomy

        manager = autonomy.IdleAutonomyManager()
        restore = AsyncMock(return_value=True)
        run = AsyncMock()
        with patch.object(autonomy, "get_actor_config", new=AsyncMock(return_value={"enabled": True, "next_wake_at": 1})), \
                patch.object(autonomy, "_latest_user_message_ts", new=AsyncMock(return_value=0)), \
                patch.object(autonomy, "refresh_actor_wake_for_user", new=AsyncMock(return_value=False)), \
                patch.object(autonomy, "get_wake_quiet_hours", return_value={"enabled": True, "start": "23:00", "end": "08:00"}), \
                patch.object(autonomy.quiet_hours, "is_quiet", side_effect=[False, True]), \
                patch.object(autonomy.quiet_hours, "quiet_end_after", return_value=5000.0), \
                patch.object(autonomy, "claim_due_wake", new=AsyncMock(return_value={"enabled": True})), \
                patch.object(autonomy, "restore_claimed_wake", new=restore), \
                patch.object(autonomy, "_run_actor_once", new=run):
            result = await manager.run_actor_once("aion", manual=False)
        self.assertEqual("quiet hours", result["skipped"])
        run.assert_not_awaited()
        self.assertGreaterEqual(restore.await_args.kwargs["wake_at"], 5000.0)

    async def test_restore_never_overwrites_a_new_timer(self):
        import autonomy_state

        with tempfile.TemporaryDirectory() as folder:
            async with aiosqlite.connect(str(Path(folder) / "a.db")) as db:
                db.row_factory = aiosqlite.Row
                await autonomy_state.ensure_autonomy_tables(db)
                await db.execute("UPDATE autonomy_actor_configs SET enabled=1, next_wake_at=NULL WHERE actor_id='aion'")
                await db.execute("UPDATE autonomy_actor_configs SET enabled=1, next_wake_at=777 WHERE actor_id='connor'")
                await db.commit()
                self.assertTrue(await autonomy_state.restore_claimed_wake("aion", wake_at=9000, db=db))
                self.assertFalse(await autonomy_state.restore_claimed_wake("connor", wake_at=9000, db=db))
                rows = {r["actor_id"]: r["next_wake_at"] for r in await (await db.execute(
                    "SELECT actor_id, next_wake_at FROM autonomy_actor_configs")).fetchall()}
        self.assertEqual(9000, rows["aion"])
        self.assertEqual(777, rows["connor"])  # 期间新起的计时不动


if __name__ == "__main__":
    unittest.main()


class ScanBudgetTests(unittest.IsolatedAsyncioTestCase):
    """Codex §115 R1：820 条不上时间线的内部记录后面还有一条，读满预算不能当成「到底」。"""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "chat.db")
        now = time.time()
        db = sqlite3.connect(self.db_path)
        db.executescript("""
            CREATE TABLE moments (id TEXT, author TEXT, content TEXT, attachments TEXT, created_at REAL);
            CREATE TABLE diary_entries (id TEXT, author TEXT, title TEXT, content TEXT, created_at REAL);
            CREATE TABLE gifts (id TEXT, image_path TEXT, message TEXT, created_at REAL, sender TEXT);
            CREATE TABLE idle_events (id TEXT, actor TEXT, action TEXT, title TEXT, detail TEXT, target_type TEXT,
                target_id TEXT, result_type TEXT, result_id TEXT, metadata TEXT, created_at REAL);
        """)
        for i in range(900):
            db.execute("INSERT INTO idle_events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (f"i{i:04d}", "aion", "memory_browse", "内部", "", "", "", "", "",
                        '{"autonomy_wake_internal":true}', now - 10 - i))
        db.execute("INSERT INTO diary_entries VALUES ('far','aion','很早的日记','x',?)", (now - 5000,))
        db.commit()
        db.close()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_budget_exhausted_returns_a_cursor_not_the_end(self):
        patches = _timeline_patches(self.db_path)
        for item in patches:
            item.start()
        try:
            found, cursor, calls = [], None, 0
            while calls < 10:
                extra = {} if cursor is None else {"before": cursor["before"], "before_kind": cursor["kind"],
                                                   "before_id": cursor["id"]}
                data = await activity.get_timeline(hours=24, limit=40, who="ai", **extra)
                calls += 1
                found.extend(item["source_id"] for item in data["items"])
                cursor = data["next_cursor"]
                if cursor is None:
                    break
        finally:
            for item in patches:
                item.stop()
        self.assertEqual(["far"], found)  # 第一页是空页 + 游标，接着读就找到了
        self.assertGreater(calls, 1)
        self.assertIsNone(cursor)


class QuietDuringConfigReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_quiet_switched_on_while_rereading_config_still_stops_the_wake(self):
        """Codex §115 R4：第二次检查静默之后不能再有等待；读配置那一下打开静默也要拦住。"""
        import autonomy

        state = {"quiet": False, "reads": 0}

        async def config(actor):
            state["reads"] += 1
            if state["reads"] == 2:  # 认领之后那次读配置的时候，小猫打开了静默
                state["quiet"] = True
            return {"enabled": True, "next_wake_at": 1}

        manager = autonomy.IdleAutonomyManager()
        run, capture = AsyncMock(), AsyncMock()
        with patch.object(autonomy, "get_actor_config", new=config), \
                patch.object(autonomy, "_latest_user_message_ts", new=AsyncMock(return_value=0)), \
                patch.object(autonomy, "refresh_actor_wake_for_user", new=AsyncMock(return_value=False)), \
                patch.object(autonomy, "get_wake_quiet_hours", return_value={"enabled": True, "start": "23:00", "end": "08:00"}), \
                patch.object(autonomy.quiet_hours, "is_quiet", side_effect=lambda cfg, now=None: state["quiet"]), \
                patch.object(autonomy.quiet_hours, "quiet_end_after", return_value=5000.0), \
                patch.object(autonomy, "claim_due_wake", new=AsyncMock(return_value={"enabled": True})), \
                patch.object(autonomy, "restore_claimed_wake", new=AsyncMock(return_value=True)), \
                patch.object(autonomy, "_capture_autonomy_monitor_context", new=capture), \
                patch.object(autonomy, "_run_actor_once", new=run):
            result = await manager.run_actor_once("aion", manual=False)
        self.assertEqual("quiet hours", result["skipped"])
        capture.assert_not_awaited()
        run.assert_not_awaited()
