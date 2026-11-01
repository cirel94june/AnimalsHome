"""11-01「AI 动态」页：时间线每条带上是谁（actor_id），狗蛋显示名字不显示 ai3；页面只用 textContent。"""

import sqlite3
import tempfile
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite

from routes import activity

STATIC = Path(__file__).resolve().parent / "static"


class TimelineActorTests(unittest.IsolatedAsyncioTestCase):
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
        db.execute("INSERT INTO diary_entries VALUES ('d1','ai3','下雨天','内容',?)", (now - 60,))
        db.execute("INSERT INTO moments VALUES ('m1','aion','面馆','[]',?)", (now - 120,))
        db.execute("INSERT INTO gifts VALUES ('g1','','送你','" + str(now - 180) + "','connor')")
        db.commit()
        db.close()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_items_carry_actor_id_and_seats_show_their_names(self):
        @asynccontextmanager
        async def fake_db():
            async with aiosqlite.connect(self.db_path) as db:
                yield db

        with patch.object(activity, "get_db", fake_db), \
                patch.object(activity, "load_worldbook", return_value={"user_name": "小猫", "ai_name": "小克"}), \
                patch.object(activity, "load_chatroom_config", return_value={"connor_name": "Lucien"}), \
                patch.object(activity, "seat_names", return_value={"ai3": "狗蛋"}), \
                patch.object(activity, "list_grouped_user_events", new=AsyncMock(return_value=[])), \
                patch.object(activity, "list_compression_events", new=AsyncMock(return_value=[])), \
                patch.object(activity, "load_location_status", return_value={}):
            data = await activity.get_timeline(hours=24, limit=50)

        by_kind = {item["kind"]: item for item in data["items"]}
        self.assertEqual(("ai3", "狗蛋"), (by_kind["diary"]["actor_id"], by_kind["diary"]["actor"]))
        self.assertEqual("狗蛋 发布了日记", by_kind["diary"]["title"])  # 不再是「ai3 发布了日记」
        self.assertEqual("aion", by_kind["moment"]["actor_id"])
        self.assertEqual("connor", by_kind["gift"]["actor_id"])


class TimelineFilterAndPagingTests(unittest.IsolatedAsyncioTestCase):
    """Codex §111 M1：先截全家最新 N 条再挑 AI，会把 AI 的挤掉；改成服务器先筛人再截断，往前翻用游标。"""

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
        db.execute("INSERT INTO diary_entries VALUES ('old-ai3','ai3','狗蛋的日记','内容',?)", (now - 3600,))
        for i in range(80):  # 一小时里小猫自己发了 80 条
            db.execute("INSERT INTO moments VALUES (?,?,?,?,?)", (f"u{i}", "user", "我的", "[]", now - 60 - i))
        for i in range(3):
            db.execute("INSERT INTO diary_entries VALUES (?,?,?,?,?)", (f"aion{i}", "aion", f"小克{i}", "x", now - 7200 - i * 10))
        db.commit()
        db.close()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def timeline(self, **kwargs):
        @asynccontextmanager
        async def fake_db():
            async with aiosqlite.connect(self.db_path) as db:
                yield db

        compression = [{"timestamp": time.time() - 30, "kind": "memory_compression", "actor": "小克", "author": "aion",
                        "title": "小克整理了记忆", "detail": "", "source_id": "c1", "attachments": []}]
        with patch.object(activity, "get_db", fake_db), \
                patch.object(activity, "load_worldbook", return_value={"user_name": "小猫", "ai_name": "小克"}), \
                patch.object(activity, "load_chatroom_config", return_value={"connor_name": "Lucien"}), \
                patch.object(activity, "seat_names", return_value={"ai3": "狗蛋"}), \
                patch.object(activity, "list_grouped_user_events", new=AsyncMock(return_value=[])), \
                patch.object(activity, "list_compression_events", new=AsyncMock(return_value=compression)), \
                patch.object(activity, "load_location_status", return_value={}):
            return await activity.get_timeline(**kwargs)

    async def test_ai_items_are_not_crowded_out_by_newer_user_rows(self):
        crowded = await self.timeline(hours=24, limit=80)
        self.assertNotIn("old-ai3", [item["source_id"] for item in crowded["items"]])  # 旧做法会漏
        data = await self.timeline(hours=24, limit=5, who="ai")
        ids = [item["source_id"] for item in data["items"]]
        self.assertIn("old-ai3", ids)
        self.assertTrue(all(item["actor_id"] not in ("user", "") for item in data["items"]))
        self.assertIn("aion", [item["actor_id"] for item in data["items"] if item["kind"] == "memory_compression"])

    async def test_single_actor_and_cursor_paging(self):
        first = await self.timeline(hours=24, limit=2, who="aion")
        self.assertEqual(["c1", "aion0"], [item["source_id"] for item in first["items"]])
        self.assertIsNotNone(first["next_before"])
        second = await self.timeline(hours=24, limit=2, who="aion", before=first["next_before"])
        seen = {item["source_id"] for item in first["items"]}
        fresh = [item["source_id"] for item in second["items"] if item["source_id"] not in seen]
        self.assertEqual(["aion1"], fresh[:1])
        db = sqlite3.connect(self.db_path)
        for i in range(2):  # 狗蛋一共 3 篇
            db.execute("INSERT INTO diary_entries VALUES (?,?,?,?,?)", (f"ai3-{i}", "ai3", "又一篇", "x", time.time() - 10 - i))
        db.commit()
        db.close()
        two = await self.timeline(hours=24, limit=2, who="ai3")
        self.assertIsNotNone(two["next_before"])  # 每类只取 limit 条时会判断不出还有第 3 篇
        three = await self.timeline(hours=24, limit=3, who="ai3")
        self.assertIsNone(three["next_before"])
        only_ai3 = await self.timeline(hours=24, limit=50, who="ai3")
        self.assertEqual(["ai3-0", "ai3-1", "old-ai3"], [item["source_id"] for item in only_ai3["items"]])


class AiFeedPageTests(unittest.TestCase):
    def test_page_renders_with_text_only_and_filters_by_actor_id(self):
        page = (STATIC / "ai-feed.html").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", page)
        self.assertIn("aiIds.has(item.actor_id)", page)  # 只放 AI 的动态，小猫自己的不放
        self.assertIn("&who=${encodeURIComponent(who)}", page)
        self.assertIn("&before=", page)
        self.assertIn("/api/idle-autonomy", page)
        self.assertIn("空位 ×", page)  # 没住进来的 bot 留位置

    def test_home_has_feed_card_and_entry(self):
        home = (STATIC / "home.html").read_text(encoding="utf-8")
        self.assertIn('id="homeFeedCard"', home)
        self.assertIn("document.getElementById('homeFeedLine').textContent", home)
        self.assertIn("/api/timeline?hours=72&limit=5&who=ai", home)
        self.assertIn("{ id: 'ai-feed',", home)
        self.assertIn("apps: ['ai-feed', 'moments',", home)
        main = (Path(__file__).resolve().parent / "main.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/ai-feed")', main)


if __name__ == "__main__":
    unittest.main()
