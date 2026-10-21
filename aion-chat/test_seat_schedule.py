"""狗蛋补能力第三批第一段：座位的闹铃 / 日程——记在 TA 名下，到点由 TA 自己提醒，只能删自己的。"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import aiosqlite
import pytest

import routes.chatroom as chatroom_routes
import schedule
import seat_chat


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "s.db"

    async def setup():
        async with aiosqlite.connect(path) as conn:
            await conn.execute("CREATE TABLE schedules (id TEXT PRIMARY KEY, type TEXT, trigger_at TEXT, content TEXT, "
                               "created_at REAL, status TEXT DEFAULT 'active', ended_at REAL, "
                               "origin TEXT DEFAULT 'aion', origin_room_id TEXT DEFAULT '')")
            await conn.execute("CREATE TABLE chatroom_rooms (id TEXT PRIMARY KEY, type TEXT, actor TEXT DEFAULT '')")
            for rid, kind, actor in (("g1", "group", ""), ("s3", "seat_1v1", "ai3"), ("s4", "seat_1v1", "ai4")):
                await conn.execute("INSERT INTO chatroom_rooms VALUES (?,?,?)", (rid, kind, actor))
            await conn.execute("INSERT INTO schedules (id, type, trigger_at, content, created_at, origin) "
                               "VALUES ('xk', 'alarm', '2099-01-01T08:00', '小克的闹铃', 0, 'aion')")
            await conn.execute("INSERT INTO schedules (id, type, trigger_at, content, created_at, origin) "
                               "VALUES ('gd', 'reminder', '2099-01-01', '狗蛋的日程', 0, 'ai3')")
            await conn.commit()
    asyncio.run(setup())
    connect = lambda: aiosqlite.connect(path)  # noqa: E731
    monkeypatch.setattr(schedule, "get_db", connect)
    monkeypatch.setattr(chatroom_routes, "get_db", connect)
    monkeypatch.setattr(schedule, "DB_PATH", path)
    monkeypatch.setattr(schedule.manager, "broadcast", AsyncMock())
    monkeypatch.setattr(chatroom_routes, "is_capability_enabled", lambda name: True)
    return path


def _rows(path, where="1=1"):
    async def go():
        async with aiosqlite.connect(path) as conn:
            return await (await conn.execute(
                f"SELECT id, type, content, status, origin, origin_room_id FROM schedules WHERE {where} ORDER BY id")).fetchall()
    return asyncio.run(go())


def _run(text, notes):
    out = asyncio.run(chatroom_routes._process_seat_commands(text, "g1", "ai3", "m1", asyncio.Queue()))
    return out


@pytest.fixture
def notes(monkeypatch):
    sys_msgs = AsyncMock(return_value={"id": "sys"})
    monkeypatch.setattr(chatroom_routes, "_chatroom_sys_msg", sys_msgs)
    monkeypatch.setattr(chatroom_routes, "_name_for_identity", lambda a: "狗蛋")
    return sys_msgs


def test_seat_alarm_and_reminder_are_recorded_under_the_seat(db, notes):
    when = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    day = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    text, triggered = _run(f"好[ALARM:{when}|喝水][REMINDER:{day}|交作业][Monitor:{when}|看看你]", notes)
    assert text == "好" and triggered["schedule"]
    rows = _rows(db, "origin='ai3' AND status='active'")
    assert {(r[1], r[2], r[5]) for r in rows} == {("alarm", "喝水", "g1"), ("reminder", "交作业", "g1"),
                                                 ("monitor", "看看你", "g1"), ("reminder", "狗蛋的日程", "")}
    shown = [c.args[1] for c in notes.await_args_list]
    assert any("【狗蛋】设定了闹铃" in s for s in shown) and any("【狗蛋】设定了日程" in s for s in shown)
    assert any("【狗蛋】设定了监督" in s for s in shown)


def test_seat_monitor_needs_the_camera_switch(db, notes, monkeypatch):
    monkeypatch.setattr(chatroom_routes, "is_capability_enabled", lambda name: name != "cam_check")
    when = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    text, _ = _run(f"好[Monitor:{when}|看看你]", notes)
    assert text == "好" and not _rows(db, "type='monitor'")
    notes.assert_not_called()


def _due_monitor(db):
    async def add():
        async with aiosqlite.connect(db) as conn:
            await conn.execute("INSERT INTO schedules (id, type, trigger_at, content, created_at, origin, origin_room_id) "
                               "VALUES ('m1', 'monitor', '2000-01-01 08:00', '看她有没有在学习', 0, 'ai3', 's3')")
            await conn.commit()
    asyncio.run(add())
    return {"id": "m1", "type": "monitor", "trigger_at": "2000-01-01 08:00", "content": "看她有没有在学习",
            "origin": "ai3", "origin_room_id": "s3"}


def test_seat_monitor_fires_with_the_snapshot_through_the_seat(db, monkeypatch):
    item = _due_monitor(db)
    import camera
    from types import SimpleNamespace
    acquire = AsyncMock(return_value=SimpleNamespace(prompt_attachment="/screenshots/m.jpg",
                                                     image_result=SimpleNamespace(no_image_context="")))
    monkeypatch.setattr(camera, "acquire_prompt_monitor_context", acquire)
    import capabilities
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: True)
    reply = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_generate_seat_reply", reply)
    main_flow = AsyncMock()
    monkeypatch.setattr(schedule, "get_active_schedules", main_flow)  # 主 AI 流程没走到
    asyncio.run(schedule.ScheduleManager()._fire_monitor(item))
    args, kwargs = reply.await_args
    assert args[0] == "s3" and kwargs["actor"] == "ai3" and kwargs["group"] is False
    assert kwargs["extra_attachments"] == ["/screenshots/m.jpg"]
    assert "定时监控触发" in kwargs["extra_prompt"] and "看她有没有在学习" in kwargs["extra_prompt"]
    assert acquire.await_args.kwargs["alert_extra"]["origin"] == "ai3"
    assert _rows(db, "id='m1'")[0][3] == "triggered"
    main_flow.assert_not_called()
    reply.reset_mock()
    asyncio.run(schedule.ScheduleManager()._fire_monitor(item))  # 重复触发不再开口
    reply.assert_not_called()


def test_seat_monitor_without_camera_switch_says_there_is_no_picture(db, monkeypatch):
    item = _due_monitor(db)
    import camera
    acquire = AsyncMock()
    monkeypatch.setattr(camera, "acquire_prompt_monitor_context", acquire)
    import capabilities
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: name != "cam_check")
    reply = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_generate_seat_reply", reply)
    asyncio.run(schedule.ScheduleManager()._fire_monitor(item))
    acquire.assert_not_called()
    kwargs = reply.await_args.kwargs
    assert kwargs["extra_attachments"] is None and "没有拿到摄像头画面" in kwargs["extra_prompt"]
    assert "不要假装看到了画面" in kwargs["extra_prompt"]


def test_seat_reply_attaches_the_snapshot_to_the_trigger_message(monkeypatch):
    monkeypatch.setattr(seat_chat, "check_ready", lambda a: ({}, "seat-m"))
    import config
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "seat-m": {"provider": "custom_openai", "model": "m"}})
    monkeypatch.setattr(seat_chat, "merged_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(seat_chat, "build_context", AsyncMock(return_value=[{"role": "system", "content": "x"}]))
    seen = []

    async def stream(messages, *a, **k):
        seen.append(messages)
        yield "在学习呢"
    monkeypatch.setattr(chatroom_routes, "stream_ai", stream)
    monkeypatch.setattr(chatroom_routes, "_save_msg", AsyncMock(return_value={"id": "x"}))
    asyncio.run(chatroom_routes._generate_seat_reply("s3", {"actor": "ai3"}, [], asyncio.Queue(), 30, actor="ai3",
                                                     extra_prompt="[定时监控触发]",
                                                     extra_attachments=["/screenshots/m.jpg"]))
    assert seen[0][-1] == {"role": "user", "content": "[定时监控触发]", "attachments": ["/screenshots/m.jpg"]}


def test_seat_prompt_offers_monitor_only_with_camera_switch(monkeypatch):
    import capabilities
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: name in ("schedule", "cam_check"))
    assert any(t.startswith("[Monitor:") for t in capabilities.seat_ability_texts("小猫"))
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: name == "schedule")
    assert not any(t.startswith("[Monitor:") for t in capabilities.seat_ability_texts("小猫"))


def test_seat_can_only_delete_its_own_schedules(db, notes):
    text, triggered = _run("[SCHEDULE_DEL:xk][SCHEDULE_DEL:gd]取消啦", notes)
    assert text == "取消啦"
    assert dict((r[0], r[3]) for r in _rows(db)) == {"xk": "active", "gd": "cancelled"}
    assert len(notes.await_args_list) == 1 and "取消了" in notes.await_args.args[1]


def test_schedule_switch_off_only_strips(db, notes, monkeypatch):
    monkeypatch.setattr(chatroom_routes, "is_capability_enabled", lambda name: name != "schedule")
    when = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    text, triggered = _run(f"嗯[ALARM:{when}|喝水][SCHEDULE_DEL:gd]", notes)
    assert text == "嗯" and "schedule" not in triggered
    assert len(_rows(db)) == 2 and all(r[3] == "active" for r in _rows(db))
    notes.assert_not_called()


def test_seat_alarm_fires_through_the_seat_itself(db, monkeypatch):
    async def add():
        async with aiosqlite.connect(db) as conn:
            await conn.execute("INSERT INTO schedules (id, type, trigger_at, content, created_at, origin, origin_room_id) "
                               "VALUES ('a1', 'alarm', '2000-01-01T08:00', '喝水', 0, 'ai3', 'g1')")
            await conn.commit()
    asyncio.run(add())
    reply = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_generate_seat_reply", reply)
    main_flow = AsyncMock()
    monkeypatch.setattr(schedule, "get_active_schedules", main_flow)  # 主流程没走到就不会碰
    item = {"id": "a1", "type": "alarm", "trigger_at": "2000-01-01T08:00", "content": "喝水",
            "origin": "ai3", "origin_room_id": "g1"}
    asyncio.run(schedule.ScheduleManager()._fire_alarm(item))
    args, kwargs = reply.await_args
    assert args[0] == "g1" and kwargs["actor"] == "ai3" and kwargs["group"] is True
    assert "喝水" in kwargs["extra_prompt"] and "闹铃到点" in kwargs["extra_prompt"]
    assert _rows(db, "id='a1'")[0][3] == "triggered"
    popup = schedule.manager.broadcast.await_args_list[0].args[0]
    assert popup["type"] == "schedule_alarm" and popup["data"]["origin"] == "ai3"


def test_alarm_set_in_another_seats_private_room_goes_back_to_the_own_room(db, monkeypatch):
    reply = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_generate_seat_reply", reply)
    monkeypatch.setattr(chatroom_routes, "_get_or_create_seat_room", AsyncMock(return_value="s3"))
    item = {"id": "a2", "trigger_at": "2000-01-01T08:00", "content": "x", "origin": "ai3", "origin_room_id": "s4"}
    asyncio.run(schedule.ScheduleManager()._fire_seat_alarm(item))
    assert reply.await_args.args[0] == "s3" and reply.await_args.kwargs["group"] is False


def test_origin_name_and_seat_context_show_schedules(db, monkeypatch):
    monkeypatch.setattr(seat_chat.actors, "display_name", lambda a: "狗蛋")
    assert schedule.get_schedule_origin_name("ai3") == "狗蛋"
    import memory_hub_bridge
    import capabilities
    monkeypatch.setattr(memory_hub_bridge, "context_block", AsyncMock(return_value=""))
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: True)
    monkeypatch.setattr(seat_chat, "_persona_text", lambda a: "人设")
    monkeypatch.setattr(seat_chat.actors, "get_actor", lambda a: {"name": "狗蛋"})
    built = asyncio.run(seat_chat.build_context("ai3", [], group=False))
    text = "\n".join(m["content"] for m in built)
    assert "[当前日程列表]" in text and "狗蛋的日程" in text and "[ALARM:" in text and "[Monitor:" in text  # 看监控开关开着时可以设定时监督


def test_alarm_reply_is_not_captured_as_a_conversation_turn(monkeypatch):
    monkeypatch.setattr(seat_chat, "check_ready", lambda a: ({"name": "狗蛋"}, "m"))
    monkeypatch.setattr(seat_chat, "merged_messages", AsyncMock(return_value=[]))
    built = AsyncMock(return_value=[{"role": "user", "content": "x"}])
    monkeypatch.setattr(seat_chat, "build_context", built)
    sent = []

    async def stream(messages, *a, **k):
        sent.append(messages)
        yield "该喝水啦"
    monkeypatch.setattr(chatroom_routes, "stream_ai", stream)
    monkeypatch.setattr(chatroom_routes, "process_note_commands", AsyncMock(side_effect=lambda t, a: t))
    monkeypatch.setattr(chatroom_routes, "_save_msg", AsyncMock(return_value={"id": "m"}))
    import memory_hub_bridge
    capture = AsyncMock()
    monkeypatch.setattr(memory_hub_bridge, "schedule_capture", capture)
    import config
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "m": {"provider": "custom_openai", "model": "m"}})
    asyncio.run(chatroom_routes._generate_seat_reply("s3", {"actor": "ai3"}, [], asyncio.Queue(), 30,
                                                     actor="ai3", extra_prompt="[闹铃到点] 喝水"))
    assert sent[0][-1] == {"role": "user", "content": "[闹铃到点] 喝水"}
    capture.assert_not_called()


def test_reassembled_commands_cannot_delete_others_or_set_monitor(db, notes):
    """Codex M1：去掉里面那条指令后拼出来的新指令，不会再被执行。"""
    text, triggered = _run("[SCHEDULE_[SCHEDULE_DEL:missing]DEL:xk]"
                           "[Mon[SCHEDULE_DEL:missing]itor:2099-01-03T08:00|内容]好", notes)
    assert dict((r[0], r[3]) for r in _rows(db) if r[0] in ("xk", "gd")) == {"xk": "active", "gd": "active"}
    assert not _rows(db, "type='monitor'")
    assert "SCHEDULE" not in text and "itor" not in text and text.endswith("好")


def _add_rows(path, rows):
    async def go():
        async with aiosqlite.connect(path) as conn:
            for sid, origin, room in rows:
                await conn.execute("INSERT INTO schedules (id, type, trigger_at, content, created_at, origin, origin_room_id) "
                                   "VALUES (?, 'reminder', '2099-01-02', ?, 0, ?, ?)", (sid, f"{sid} 的内容", origin, room))
            await conn.commit()
    asyncio.run(go())


def test_each_ai_only_sees_its_own_and_shared_schedules(db):
    """Codex M2：别人私聊里设的日程不进你的提示词；自己的、家里群里的、小猫自己设的都看得到。"""
    _add_rows(db, [("p4", "ai4", "s4"), ("grp", "ai4", "g1"), ("usr", "user", ""), ("lc", "connor", "c1")])
    seen = lambda viewer: {s["id"] for s in asyncio.run(schedule.get_active_schedules(viewer=viewer))}  # noqa: E731
    assert seen("ai3") == {"gd", "grp", "usr"}
    assert seen("ai4") == {"p4", "grp", "usr"}
    assert seen("aion") == {"xk", "grp", "usr", "lc"}  # 小克 / Lucien 之间照旧互相看得到，座位私聊的看不到
    assert seen(None) == {"xk", "gd", "p4", "grp", "usr", "lc"}  # 日程页仍是全部
