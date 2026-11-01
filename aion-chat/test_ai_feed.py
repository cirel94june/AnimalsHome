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


class AiFeedPageTests(unittest.TestCase):
    def test_page_renders_with_text_only_and_filters_by_actor_id(self):
        page = (STATIC / "ai-feed.html").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", page)
        self.assertIn("aiIds.has(item.actor_id)", page)  # 只放 AI 的动态，小猫自己的不放
        self.assertIn("/api/timeline?hours=", page)
        self.assertIn("/api/idle-autonomy", page)
        self.assertIn("空位 ×", page)  # 没住进来的 bot 留位置

    def test_home_has_feed_card_and_entry(self):
        home = (STATIC / "home.html").read_text(encoding="utf-8")
        self.assertIn('id="homeFeedCard"', home)
        self.assertIn("document.getElementById('homeFeedLine').textContent", home)
        self.assertIn("{ id: 'ai-feed',", home)
        self.assertIn("apps: ['ai-feed', 'moments',", home)
        main = (Path(__file__).resolve().parent / "main.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/ai-feed")', main)


if __name__ == "__main__":
    unittest.main()
