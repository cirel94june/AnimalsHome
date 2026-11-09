"""小日历（11-08 小猫）：每天的动态、心情、聊天频率。"""
import asyncio
import sqlite3
from datetime import datetime

import pytest

import actors
import config
import database
import family_calendar as cal


def at(day, hour=12):
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00").timestamp()


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "chat.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(actors, "list_actors", lambda include_disabled=True: [{"id": "aion", "name": "小克"}, {"id": "ai3", "name": "狗蛋"}])
    monkeypatch.setattr(config, "load_worldbook", lambda: {"user_name": "小猫"})
    asyncio.run(database.init_db())
    with sqlite3.connect(path) as conn:
        conn.executescript("CREATE TABLE IF NOT EXISTS idle_events (id TEXT, actor TEXT, action TEXT, title TEXT, detail TEXT, target_type TEXT, target_id TEXT, result_type TEXT, result_id TEXT, metadata TEXT, created_at REAL);")
        conn.execute("INSERT INTO chatroom_rooms (id,title,type,actor,created_at,updated_at) VALUES ('g','家','group','',1,1)")
        for i, (sender, day) in enumerate([("user", "2026-11-08"), ("ai3", "2026-11-08"), ("ai3", "2026-11-08"), ("system", "2026-11-08"), ("user", "2026-11-07")]):
            conn.execute("INSERT INTO chatroom_messages (id,room_id,sender,content,created_at) VALUES (?,?,?,?,?)", (f"m{i}", "g", sender, "SECRET_CHAT", at(day)))
        conn.execute("INSERT INTO conversations (id,title,created_at,updated_at) VALUES ('c','x',1,1)")
        conn.execute("INSERT INTO messages (id,conv_id,role,content,created_at) VALUES ('p1','c','assistant','SECRET_CHAT',?)", (at("2026-11-08"),))
        conn.execute("INSERT INTO moments (id,author,content,attachments,created_at) VALUES ('mo','aion','今天的云','[]',?)", (at("2026-11-08", 9),))
        conn.execute("INSERT INTO diary_entries (id,author,title,content,created_at) VALUES ('d','ai3','狗蛋的日记','正文',?)", (at("2026-11-08", 22),))
        for i, (action, title) in enumerate([("travel", "狗蛋去了京都"), ("select", "内部选择")]):
            conn.execute("INSERT INTO idle_events (id,actor,action,title,created_at) VALUES (?,?,?,?,?)", (f"e{i}", "ai3", action, title, at("2026-11-08", 15)))
    return tmp_path


def test_month_counts(home):
    data = asyncio.run(cal.month(2026, 11))
    day = data["days"]["2026-11-08"]
    assert day == {"chats": 4, "events": 3, "mood": "", "festivals": []}  # 系统消息不算，select 这种内部事件不算
    assert data["days"]["2026-11-07"]["chats"] == 1 and len(data["days"]) == 30
    assert data["first_weekday"] == 6  # 2026-11-01 是周日


def test_day_detail(home):
    data = asyncio.run(cal.day_detail("2026-11-08"))
    assert {c["name"]: c["count"] for c in data["chats"]} == {"狗蛋": 2, "小猫": 1, "小克": 1}
    assert [(e["kind"], e["name"]) for e in data["events"]] == [("朋友圈", "小克"), ("旅行", "狗蛋"), ("日记", "狗蛋")]
    assert "SECRET_CHAT" not in str(data)  # 聊天只数条数
    assert data["events"][2]["text"] == "狗蛋的日记"


def test_mood_saved_and_cleared(home):
    assert cal.set_mood("2026-11-08", "😊", "很开心") == {"mood": "😊", "note": "很开心"}
    assert asyncio.run(cal.month(2026, 11))["days"]["2026-11-08"]["mood"] == "😊"
    assert asyncio.run(cal.day_detail("2026-11-08"))["note"] == "很开心"
    assert cal.set_mood("2026-11-08", "") == {"mood": "", "note": ""}
    assert "2026-11-08" not in cal._moods()


@pytest.mark.parametrize("day", ["", "2026-13-01", "../x", "2026-11-31"])
def test_bad_dates(home, day):
    with pytest.raises(cal.CalendarError):
        cal.set_mood(day, "😊")


def test_unknown_mood(home):
    with pytest.raises(cal.CalendarError, match="不认识"):
        cal.set_mood("2026-11-08", "<b>")


@pytest.mark.parametrize("year,month", [(1, 1), (2026, 13)])
def test_bad_month(home, year, month):
    with pytest.raises(cal.CalendarError):
        asyncio.run(cal.month(year, month))
