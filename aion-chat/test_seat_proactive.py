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
    monkeypatch.setitem(pc.SETTINGS, "proactive_companionship_ai3_enabled", True)
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


def _add_timer(path, sid, origin, room=""):
    async def add():
        async with aiosqlite.connect(path) as conn:
            await conn.execute("INSERT INTO schedules (id, type, trigger_at, content, created_at, origin, origin_room_id) "
                               "VALUES (?, 'proactive', '2000-01-01 08:00:00', '', 0, ?, ?)", (sid, origin, room))
            await conn.commit()
    asyncio.run(add())


def test_switching_off_clears_only_that_actors_timers(db, monkeypatch):
    _add_timer(db, "pc1", "ai3", "s3")
    _add_timer(db, "pc2", "aion")
    from routes import capabilities as cap_routes
    monkeypatch.setattr(cap_routes, "set_proactive_enabled", lambda a, e: None)
    monkeypatch.setattr(cap_routes.manager, "broadcast", AsyncMock())
    asyncio.run(cap_routes.update_proactive_companionship("ai3", cap_routes.CapabilityToggle(enabled=False)))
    assert _timers(db) == [("aion", "")]


@pytest.mark.parametrize("origin", ["ai3", "aion", "connor"])
def test_due_timer_stays_quiet_when_switched_off(db, monkeypatch, origin):
    _add_timer(db, "pc1", origin, "s3")
    monkeypatch.setitem(pc.SETTINGS, f"proactive_companionship_{origin}_enabled", False)
    seat_reply = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_generate_seat_reply", seat_reply)
    mgr = schedule.ScheduleManager()
    item = {"id": "pc1", "type": "proactive", "trigger_at": "2000-01-01 08:00:00", "content": "",
            "origin": origin, "origin_room_id": "s3"}
    asyncio.run(mgr._fire_monitor(item))
    seat_reply.assert_not_called()
    assert _timers(db) == []  # 认领掉了，不会再响


def test_switched_off_while_waiting_seat_does_not_speak(monkeypatch):
    import seat_chat
    monkeypatch.setattr(seat_chat, "check_ready", lambda a: ({}, "seat-m"))
    import config
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "seat-m": {"provider": "custom_openai", "model": "m"}})
    monkeypatch.setattr(seat_chat, "merged_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(seat_chat, "build_context", AsyncMock(return_value=[{"role": "system", "content": "x"}]))
    called = []

    async def stream(*a, **k):
        called.append(1)
        yield "嗨"
    monkeypatch.setattr(chatroom_routes, "stream_ai", stream)
    saved = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_save_msg", saved)
    q = asyncio.Queue()
    asyncio.run(chatroom_routes._generate_seat_reply("s3", {"actor": "ai3"}, [], q, 30, actor="ai3",
                                                     extra_prompt="[主动陪伴触发]", proceed_check=lambda: False))
    assert not called
    saved.assert_not_called()


def _main_ai_monitor_setup(db, monkeypatch, origin, flip_at):
    """主 AI 主动陪伴：认领、开关检查都过了，flip_at 指定的那一步里开关被关掉。"""
    async def tables():
        async with aiosqlite.connect(db) as conn:
            await conn.execute("CREATE TABLE IF NOT EXISTS conversations (id TEXT, model TEXT, updated_at REAL, title TEXT)")
            await conn.execute("CREATE TABLE IF NOT EXISTS messages (id TEXT, conv_id TEXT, role TEXT, content TEXT, "
                               "created_at REAL, attachments TEXT DEFAULT '[]')")
            await conn.execute("INSERT INTO conversations VALUES ('c1', '', 1, 't')")
            await conn.commit()
    asyncio.run(tables())
    _add_timer(db, "pcm", origin)
    key = f"proactive_companionship_{origin}_enabled"
    monkeypatch.setitem(pc.SETTINGS, key, True)
    switch_off = lambda: pc.SETTINGS.__setitem__(key, False)  # noqa: E731
    monkeypatch.setattr(schedule.ScheduleManager, "_resolve_target", lambda self, item: {"type": "private"})
    import context_builder
    monkeypatch.setattr(context_builder, "fetch_merged_timeline", AsyncMock(return_value=[]))
    monkeypatch.setattr(context_builder, "render_merged_timeline", lambda *a, **k: [])
    import camera
    from types import SimpleNamespace

    async def acquire(*a, **k):
        if flip_at == "prepare":
            switch_off()
        return SimpleNamespace(image_result=SimpleNamespace(no_image_context="", source="none"), prompt_attachment="")
    monkeypatch.setattr(camera, "acquire_prompt_monitor_context", acquire)
    model_calls = []

    async def stream(*a, **k):
        model_calls.append(1)
        yield "嗨"
    monkeypatch.setattr(schedule, "stream_ai", stream)
    monkeypatch.setattr(schedule, "_load_connor_stream_runtime",
                        lambda: (lambda m: "cm", lambda *a, **k: stream(), lambda: {}))
    consumed = []

    async def consume(gen, *a, **k):
        consumed.append(1)
        if flip_at == "stream":
            switch_off()
        async for _ in gen:
            pass
        from types import SimpleNamespace as NS
        return NS(committed_text="", stop_reason=None, diagnostic_error=None, notice="")
    monkeypatch.setattr(schedule, "_consume_background_stream", consume)
    item = {"id": "pcm", "type": "proactive", "trigger_at": "2000-01-01 08:00:00", "content": "",
            "origin": origin, "origin_room_id": ""}
    return item, model_calls, consumed


@pytest.mark.parametrize("origin", ["aion", "connor"])
@pytest.mark.parametrize("flip_at", ["prepare", "stream"])
def test_main_ai_switched_off_after_claim_never_calls_model(db, monkeypatch, origin, flip_at):
    """§47 复审 R1：认领后截图 / 取上下文期间、或进模型前一刻关开关，主 AI 都不调模型。"""
    item, model_calls, consumed = _main_ai_monitor_setup(db, monkeypatch, origin, flip_at)
    asyncio.run(schedule.ScheduleManager()._fire_monitor(item))
    assert not model_calls
    assert consumed == ([] if flip_at == "prepare" else [1])


def test_main_ai_still_speaks_when_switch_stays_on(db, monkeypatch):
    item, model_calls, consumed = _main_ai_monitor_setup(db, monkeypatch, "aion", "never")
    asyncio.run(schedule.ScheduleManager()._fire_monitor(item))
    assert model_calls == [1]


@pytest.mark.parametrize("change", ["disabled", "reselected"])
def test_seat_reply_stops_if_the_seat_changes_while_building_context(monkeypatch, change):
    """§52 复审 M2：取记忆 / 上下文期间座位被停用或改选模型，不再用旧线路回复，房间里说明。"""
    import seat_chat
    import config
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "seat-m": {"provider": "custom_openai", "model": "m"},
                                           "other-m": {"provider": "custom_openai", "model": "o"}})
    state = {"ready": True, "model": "seat-m"}

    def check_ready(actor):
        if not state["ready"]:
            raise seat_chat.SeatNotReady("off")
        return {}, state["model"]
    monkeypatch.setattr(seat_chat, "check_ready", check_ready)
    monkeypatch.setattr(seat_chat, "merged_messages", AsyncMock(return_value=[]))

    async def build_context(*a, **k):
        if change == "disabled":
            state["ready"] = False
        else:
            state["model"] = "other-m"
        return [{"role": "system", "content": "x"}]
    monkeypatch.setattr(seat_chat, "build_context", build_context)
    monkeypatch.setattr(seat_chat.actors, "display_name", lambda a: "狗蛋")
    called = []

    async def stream(*a, **k):
        called.append(a[1] if len(a) > 1 else k.get("model_key"))
        yield "嗨"
    monkeypatch.setattr(chatroom_routes, "stream_ai", stream)
    saved = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_save_msg", saved)
    failure = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_save_chatroom_reply_failure", failure)
    asyncio.run(chatroom_routes._generate_seat_reply("s3", {"actor": "ai3"}, [], asyncio.Queue(), 30, actor="ai3",
                                                     extra_prompt="[监控唤醒]", extra_attachments=["/screenshots/x.jpg"]))
    assert not called
    saved.assert_not_called()
    assert failure.await_args.args[1] == "ai3" and "改动" in failure.await_args.args[3]  # 留下「没能回复」，不换人
