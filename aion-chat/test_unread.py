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
            CREATE TABLE conversations (id TEXT);
            INSERT INTO conversations VALUES ('conv1'), ('conv2'), ('conv3');
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
        # Codex §122 L1：空白、多余的冒号、不存在的窗口都不能写进去
        for bad in ("", "x:1", "conv:", "room:" + "a" * 200, "conv: ", "room:x:y", "room:nope", "conv:gone"):
            with self.assertRaises(HTTPException):
                self.call(unread.mark_read, unread.ReadBody(container=bad))
        db = sqlite3.connect(self.db_path)
        has_table = db.execute("SELECT name FROM sqlite_master WHERE name='unread_read_marks'").fetchone()
        count = db.execute("SELECT COUNT(*) FROM unread_read_marks").fetchone()[0] if has_table else 0
        db.close()
        self.assertEqual(0, count)

    def test_read_only_reaches_the_message_actually_shown(self):
        """Codex §122 M1：页面只显示到更早的消息时，之后才到的回复仍算未读。"""
        with patch.object(unread.time, "time", return_value=self.now + 100):
            self.call(unread.mark_read, unread.ReadBody(container="room:s1", at=self.now + 5))
            self.assertIn("room:s1", [i["container"] for i in self.call(unread.unread_summary)["unread"]])
            self.call(unread.mark_read, unread.ReadBody(container="room:s1", at=self.now + 20))
            self.assertNotIn("room:s1", [i["container"] for i in self.call(unread.unread_summary)["unread"]])
            # 记到将来也只记到现在；往回记不会倒退
            result = self.call(unread.mark_read, unread.ReadBody(container="room:g1", at=self.now + 9999))
            self.assertEqual(self.now + 100, result["read_at"])
            self.call(unread.mark_read, unread.ReadBody(container="room:s1", at=self.now - 50))
            self.assertNotIn("room:s1", [i["container"] for i in self.call(unread.unread_summary)["unread"]])


class UnreadPagesTests(unittest.TestCase):
    def test_pages_wire_up_the_dots(self):
        chat = (STATIC / "chat.js").read_text(encoding="utf-8")
        self.assertIn("noticeUnreadEvent(msg);", chat)
        self.assertIn("if (!currentConvId || document.hidden || currentSubPage) return;", chat)
        # 消息取成功、显示出来以后才记已读（Codex §122 M1）
        chat_lf = chat.replace("\r\n", "\n")
        self.assertIn("if (currentMessagesConvId !== currentConvId) return;", chat)
        self.assertNotIn("setTimeout(markCurrentConvRead, 0)", chat)
        self.assertIn("refreshCurrentConversationFromServer().then(ok => {\n      if (ok) markCurrentConvRead();", chat_lf)
        room = (STATIC / "chatroom.js").read_text(encoding="utf-8")
        self.assertIn("window.frameElement?.dataset.aionSubPageVisible !== '0'", room)
        self.assertIn("if (crRoomServerLoadedId !== roomId || crRenderedRoomId !== roomId) return;", room)
        self.assertIn("crRoomServerLoadedId = roomId;\n      crMarkRoomRead();", room.replace("\r\n", "\n"))
        home = (STATIC / "home.html").read_text(encoding="utf-8")
        self.assertIn("var badgeState", home)  # 不能用 let：首页一开始画图标就会用到
        self.assertIn("chat: 'chat-private', chatroom: 'chat-group'", home)
        # 文件夹亮红点时，点开后里面那个应用也要亮（11-01 小猫：不知道是哪个应用）
        self.assertIn('data-folder-badges="${keys}"', home)
        self.assertNotIn("!options.inFolder ? 'id=\"momentsBadge\"'", home)
        contacts = (STATIC / "contacts.js").read_text(encoding="utf-8")
        self.assertIn("api('GET', '/api/unread')", contacts)


if __name__ == "__main__":
    unittest.main()
