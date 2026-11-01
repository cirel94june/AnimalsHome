"""11-01 未读小红点：哪些窗口有 AI 的新消息小猫还没看。"""

import asyncio
import sqlite3
import tempfile
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import aiosqlite
from fastapi import HTTPException

from routes import unread

STATIC = Path(__file__).resolve().parent / "static"


class UnreadApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "chat.db")
        self.now = time.time()
        db = sqlite3.connect(self.db_path)
        db.executescript("""
            CREATE TABLE messages (id TEXT, conv_id TEXT, role TEXT, content TEXT, created_at REAL);
            CREATE TABLE chatroom_messages (id TEXT, room_id TEXT, sender TEXT, content TEXT, created_at REAL);
            CREATE TABLE chatroom_rooms (id TEXT, type TEXT, actor TEXT);
        """)
        db.executemany("INSERT INTO chatroom_rooms VALUES (?,?,?)",
                       [("g1", "group", ""), ("s1", "seat_1v1", "ai3"), ("c1", "connor_1v1", "")])
        n = self.now
        db.executemany("INSERT INTO messages VALUES (?,?,?,?,?)", [
            ("old", "conv1", "assistant", "x", n - 500),       # 起点之前：不算
            ("a1", "conv1", "assistant", "x", n + 10),
            ("u1", "conv2", "user", "x", n + 10),               # 小猫自己发的不算
            ("s0", "conv3", "system", "x", n + 10),             # 系统提示不算
        ])
        db.executemany("INSERT INTO chatroom_messages VALUES (?,?,?,?,?)", [
            ("r1", "s1", "ai3", "x", n + 20), ("r2", "g1", "aion", "x", n + 30),
            ("r3", "c1", "system", "x", n + 30), ("r4", "gone", "aion", "x", n + 30),
        ])
        db.commit()
        db.close()
        self.settings = {"unread_baseline_at": self.now - 100}

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, fn, *args):
        @asynccontextmanager
        async def fake_db():
            async with aiosqlite.connect(self.db_path) as db:
                yield db
        with patch.object(unread, "get_db", fake_db), patch.object(unread, "SETTINGS", self.settings), \
                patch.object(unread, "save_settings"):
            return asyncio.run(fn(*args))

    def test_only_new_ai_messages_since_last_read(self):
        data = self.call(unread.unread_summary)
        self.assertEqual({"conv:conv1", "room:s1", "room:g1"}, {i["container"] for i in data["unread"]})
        self.assertTrue(data["private"] and data["group"])
        kinds = {i["container"]: (i["kind"], i["actor"]) for i in data["unread"]}
        self.assertEqual(("private", "ai3"), kinds["room:s1"])
        self.assertEqual(("group", ""), kinds["room:g1"])

        with patch.object(unread.time, "time", return_value=self.now + 25):
            self.call(unread.mark_read, unread.ReadBody(container="room:s1"))
            self.call(unread.mark_read, unread.ReadBody(container="conv:conv1"))
        data = self.call(unread.unread_summary)
        self.assertEqual(["room:g1"], [i["container"] for i in data["unread"]])
        self.assertFalse(data["private"])

        with patch.object(unread.time, "time", return_value=self.now - 999):  # 已读只往后挪
            self.call(unread.mark_read, unread.ReadBody(container="room:s1"))
        self.assertNotIn("room:s1", [i["container"] for i in self.call(unread.unread_summary)["unread"]])

    def test_first_use_sets_a_baseline_so_old_messages_are_not_unread(self):
        self.settings = {}
        with patch.object(unread.time, "time", return_value=self.now + 1000):  # 第一次打开是在这些消息之后
            data = self.call(unread.unread_summary)
        self.assertEqual(self.now + 1000, self.settings["unread_baseline_at"])
        self.assertEqual([], data["unread"])  # 起点之前的都不算，免得一上线满屏红点

    def test_bad_container_is_refused(self):
        for bad in ("", "x:1", "conv:", "room:" + "a" * 200):
            with self.assertRaises(HTTPException):
                self.call(unread.mark_read, unread.ReadBody(container=bad))


class UnreadPagesTests(unittest.TestCase):
    def test_pages_wire_up_the_dots(self):
        chat = (STATIC / "chat.js").read_text(encoding="utf-8")
        self.assertIn("noticeUnreadEvent(msg);", chat)
        self.assertIn("if (!currentConvId || document.hidden || currentSubPage) return;", chat)
        room = (STATIC / "chatroom.js").read_text(encoding="utf-8")
        self.assertIn("window.frameElement?.dataset.aionSubPageVisible !== '0'", room)
        home = (STATIC / "home.html").read_text(encoding="utf-8")
        self.assertIn("var chatUnread", home)  # 不能用 let：首页一开始画图标就会用到
        self.assertIn('id="chatUnreadBadge"', home)
        contacts = (STATIC / "contacts.js").read_text(encoding="utf-8")
        self.assertIn("api('GET', '/api/unread')", contacts)


if __name__ == "__main__":
    unittest.main()
