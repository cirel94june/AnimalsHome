"""狗蛋补能力第三批：座位的主动陪伴——[NEXT_CHAT:x] 定时再来找小猫，到点 TA 自己回原房间开口。"""

import asyncio
from unittest.mock import AsyncMock

import aiosqlite
import pytest

import proactive_companionship as pc
import routes.chatroom as chatroom_routes
import schedule


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "pc.db"

    async def setup():
        async with aiosqlite.connect(path) as conn:
            await conn.execute("CREATE TABLE schedules (id TEXT PRIMARY KEY, type TEXT, trigger_at TEXT, content TEXT, "
                               "created_at REAL, status TEXT DEFAULT 'active', ended_at REAL, "
                               "origin TEXT DEFAULT 'aion', origin_room_id TEXT DEFAULT '')")
            await conn.execute("CREATE TABLE chatroom_messages (id TEXT, room_id TEXT, sender TEXT, content TEXT, "
                               "created_at REAL, attachments TEXT)")
            await conn.execute("CREATE TABLE chatroom_rooms (id TEXT PRIMARY KEY, type TEXT, actor TEXT DEFAULT '')")
            await conn.execute("INSERT INTO chatroom_rooms VALUES ('s3', 'seat_1v1', 'ai3')")
            await conn.execute("INSERT INTO chatroom_messages VALUES ('m1', 's3', 'ai3', '回头再聊[NEXT_CHAT:5]', 1000, '[]')")
            await conn.commit()
    asyncio.run(setup())
    monkeypatch.setattr(pc, "DB_PATH", path)
    monkeypatch.setattr(schedule, "DB_PATH", path)
    monkeypatch.setattr(schedule, "get_db", lambda: aiosqlite.connect(path))
    monkeypatch.setattr(schedule.manager, "broadcast", AsyncMock())
    return path


def _timers(path):
    async def go():
        async with aiosqlite.connect(path) as conn:
            return await (await conn.execute(
                "SELECT origin, origin_room_id FROM schedules WHERE type='proactive' AND status='active'")).fetchall()
    return asyncio.run(go())


def _event():
    return {"type": "chatroom_msg_created",
            "data": {"id": "m1", "room_id": "s3", "sender": "ai3", "content": "回头再聊[NEXT_CHAT:5]", "created_at": 1000}}


def test_seat_next_chat_sets_its_own_timer_in_its_room(db, monkeypatch):
    monkeypatch.setitem(pc.SETTINGS, "proactive_companionship_ai3_enabled", True)
    updated, changed = asyncio.run(pc.process_visible_message_event(_event()))
    assert changed and updated["data"]["content"] == "回头再聊"
    assert _timers(db) == [("ai3", "s3")]


def test_seat_switch_off_means_no_timer(db, monkeypatch):
    monkeypatch.setitem(pc.SETTINGS, "proactive_companionship_ai3_enabled", False)
    updated, _ = asyncio.run(pc.process_visible_message_event(_event()))
    assert updated["data"]["content"] == "回头再聊" and _timers(db) == []
    assert pc.proactive_ability_text("ai3") == ""
    monkeypatch.setitem(pc.SETTINGS, "proactive_companionship_ai3_enabled", True)
    assert "[NEXT_CHAT:x]" in pc.proactive_ability_text("ai3")


def test_status_lists_enabled_seats_and_toggle_accepts_them(db, monkeypatch):
    import actors
    monkeypatch.setattr(actors, "list_actors", lambda include_disabled=False: [{"id": "ai3", "name": "狗蛋"}])
    import chatroom
    monkeypatch.setattr(chatroom, "get_chatroom_names", lambda: ("小猫", "小克", "Lucien"))
    payload = asyncio.run(pc.proactive_status_payload())
    assert [r["actor"] for r in payload["roles"]] == ["aion", "connor", "ai3"]
    from routes import capabilities as cap_routes
    saved = {}
    monkeypatch.setattr(cap_routes, "set_proactive_enabled", lambda a, e: saved.update({a: e}))
    monkeypatch.setattr(cap_routes.manager, "broadcast", AsyncMock())
    asyncio.run(cap_routes.update_proactive_companionship("ai3", cap_routes.CapabilityToggle(enabled=True)))
    assert saved == {"ai3": True}


def test_due_seat_timer_wakes_the_seat_in_its_room(db, monkeypatch):
    async def add():
        async with aiosqlite.connect(db) as conn:
            await conn.execute("INSERT INTO schedules (id, type, trigger_at, content, created_at, origin, origin_room_id) "
                               "VALUES ('pc1', 'proactive', '2000-01-01 08:00:00', '', 0, 'ai3', 's3')")
            await conn.commit()
    asyncio.run(add())
    reply = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_generate_seat_reply", reply)
    item = {"id": "pc1", "type": "proactive", "trigger_at": "2000-01-01 08:00:00", "content": "",
            "origin": "ai3", "origin_room_id": "s3"}
    asyncio.run(schedule.ScheduleManager()._fire_monitor(item))
    args, kwargs = reply.await_args
    assert args[0] == "s3" and kwargs["actor"] == "ai3" and "主动陪伴触发" in kwargs["extra_prompt"]
    assert _timers(db) == []  # 计时器已认领
    reply.reset_mock()
    asyncio.run(schedule.ScheduleManager()._fire_monitor(item))  # 重复触发不再开口
    reply.assert_not_called()


def test_seat_prompt_includes_next_chat_when_enabled(monkeypatch):
    import seat_chat
    monkeypatch.setitem(pc.SETTINGS, "proactive_companionship_ai3_enabled", True)
    assert "[NEXT_CHAT:x]" in seat_chat.proactive_line("ai3")
    monkeypatch.setitem(pc.SETTINGS, "proactive_companionship_ai3_enabled", False)
    assert seat_chat.proactive_line("ai3") == ""
