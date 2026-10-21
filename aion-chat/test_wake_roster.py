"""监控 / 定位叫谁（2026-10-21）：智能模式不固定分工、能叫座位、拿不准随机；出门回家也按设置挑人。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import camera
import config


ROSTER = ["aion", "connor", "ai3"]


def test_seat_and_multi_targets_are_parsed_in_roster_order():
    assert camera.parse_wake_target_ids("ai3", ROSTER) == ["ai3"]
    assert camera.parse_wake_target_ids("ai3, main_ai", ROSTER) == ["aion", "ai3"]
    assert camera.parse_wake_target_ids("second_ai、ai3", ROSTER) == ["connor", "ai3"]
    assert camera.parse_wake_target_ids("both", ROSTER) == ["aion", "connor"]
    assert camera.parse_wake_target_ids("all", ROSTER) == ROSTER
    assert camera.parse_wake_target_ids("ai4", ROSTER) == []  # 没启用的座位不在名单里


def test_smart_falls_back_to_the_random_suggestion_not_always_aion():
    assert camera.resolve_sentinel_wake_targets(True, "smart", "ai4", roster_ids=ROSTER, suggested="ai3") == ["ai3"]
    assert camera.resolve_sentinel_wake_targets(True, "smart", "", roster_ids=ROSTER, suggested="connor") == ["connor"]
    assert camera.resolve_sentinel_wake_targets(True, "ai3", "main_ai", roster_ids=ROSTER) == ["ai3"]  # 固定叫座位
    dispatch = camera.parse_sentinel_dispatch({"call_core": True, "wake_target": "ai3"}, "smart",
                                              roster_ids=ROSTER, suggested="aion")
    assert dispatch["sentinel_wake_target"] == "ai3" and dispatch["final_wake_targets"] == ["ai3"]


def test_seat_wake_mode_is_a_valid_setting():
    assert config.normalize_camera_wake_mode("ai3") == "ai3"
    assert config.normalize_camera_wake_mode("ai9") == "aion"


def test_suggestion_favours_whoever_has_waited_longest():
    now = 1_000_000.0
    roster = [{"id": "aion", "last_woken": now - 600}, {"id": "connor", "last_woken": 0.0},
              {"id": "ai3", "last_woken": now - 3600 * 10}]
    seen = {}

    class FakeRng:
        def choices(self, ids, weights, k):
            seen.update(zip(ids, weights))
            return [ids[0]]
    camera.suggest_wake_target(roster, rng=FakeRng(), now=now)
    assert seen["connor"] == 73.0 and seen["ai3"] == 11.0 and seen["aion"] < 2


def test_roster_has_ready_seats_and_last_woken_from_logs(monkeypatch):
    import chatroom
    import seat_chat
    import actors
    monkeypatch.setattr(chatroom, "get_chatroom_names", lambda: ("小猫", "小克", "Lucien"))

    def ready(seat):
        if seat != "ai3":
            raise seat_chat.SeatNotReady("off")
        return {}, "m"
    monkeypatch.setattr(seat_chat, "check_ready", ready)
    monkeypatch.setattr(actors, "display_name", lambda a: "狗蛋")
    monkeypatch.setattr(camera, "read_logs_since", lambda since: [
        {"timestamp": 50.0, "final_wake_targets": ["ai3"]}, {"timestamp": 80.0, "final_wake_targets": ["aion", "ai3"]}])
    roster = camera.wake_roster()
    assert [(r["id"], r["key"], r["name"], r["last_woken"]) for r in roster] == [
        ("aion", "main_ai", "小克", 80.0), ("connor", "second_ai", "Lucien", 0.0), ("ai3", "ai3", "狗蛋", 80.0)]


@pytest.fixture
def chat_db(tmp_path, monkeypatch):
    """聊天室房间和消息：g1 群、s3 狗蛋私聊、s4 座位 4 私聊、台球房。"""
    import aiosqlite
    path = tmp_path / "chat.db"

    async def setup():
        async with aiosqlite.connect(path) as conn:
            await conn.execute("CREATE TABLE chatroom_rooms (id TEXT PRIMARY KEY, type TEXT, actor TEXT DEFAULT '')")
            await conn.execute("CREATE TABLE chatroom_messages (id TEXT, room_id TEXT, sender TEXT, content TEXT, "
                               "created_at REAL)")
            for rid, kind, actor in (("g1", "group", ""), ("s3", "seat_1v1", "ai3"), ("s4", "seat_1v1", "ai4"),
                                     ("billiards_1", "group", "")):
                await conn.execute("INSERT INTO chatroom_rooms VALUES (?,?,?)", (rid, kind, actor))
            await conn.commit()
    asyncio.run(setup())
    monkeypatch.setattr(camera, "get_db", lambda: aiosqlite.connect(path))
    import routes.chatroom as chatroom_routes
    monkeypatch.setattr(chatroom_routes, "_get_or_create_seat_room", AsyncMock(return_value="s3"))

    def said(room_id, ts):
        async def add():
            async with aiosqlite.connect(path) as conn:
                await conn.execute("INSERT INTO chatroom_messages VALUES (?,?,?,?,?)", (f"m{ts}", room_id, "user", "x", ts))
                await conn.commit()
        asyncio.run(add())
    return said


@pytest.mark.parametrize("history, expected", [
    ([], ("s3", "seat_1v1")),  # 还没说过话：去 TA 私聊
    ([("g1", 1), ("s3", 2)], ("s3", "seat_1v1")),  # 最近在和 TA 私聊
    ([("s3", 1), ("g1", 2)], ("g1", "group")),  # 最近在群里说话：在群里接
    ([("g1", 1), ("s4", 2), ("billiards_1", 3)], ("g1", "group")),  # 别的座位私聊、台球房不算
])
def test_seat_goes_where_she_last_talked_like_aion(chat_db, history, expected):
    """2026-10-21 小猫：几个 AI 权限一样。座位被叫来时和小克一样去她最近说话的地方。"""
    for room_id, ts in history:
        chat_db(room_id, ts)
    target = asyncio.run(camera.CameraMonitor()._resolve_actor_target("ai3"))
    assert (target["room_id"], target["room_type"]) == expected


@pytest.mark.parametrize("room, group", [("s3", False), ("g1", True)])
def test_waking_a_seat_speaks_in_its_room_with_the_snapshot(chat_db, monkeypatch, room, group):
    import routes.chatroom as chatroom_routes
    if group:
        chat_db("g1", 5)
    reply = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_generate_seat_reply", reply)
    monkeypatch.setattr(camera, "load_worldbook", lambda: {"user_name": "小猫"})
    monitor = camera.CameraMonitor()
    results = asyncio.run(monitor._wake_core_targets(["ai3"], "小猫在沙发上发呆", 0.0, "", "很久没说话", [], "shot.jpg"))
    assert results["ai3"]["ok"]
    args, kwargs = reply.await_args
    assert args[0] == room and kwargs["actor"] == "ai3" and kwargs["group"] is group
    assert ("群聊里" in kwargs["extra_prompt"]) is group
    assert kwargs["extra_attachments"] == ["/screenshots/shot.jpg"]
    assert "很久没说话" in kwargs["extra_prompt"] and "小猫在沙发上发呆" in kwargs["extra_prompt"]


# ── 出门 / 回家 ──

@pytest.fixture
def location_env(monkeypatch):
    import location
    import memory
    monkeypatch.setattr(location, "get_sentinel_config", lambda: {"ready": True})
    monkeypatch.setattr(location, "load_worldbook", lambda: {"user_name": "小猫", "ai_name": "小克"})
    monkeypatch.setattr(location, "format_location_for_prompt", lambda: "")
    monkeypatch.setattr(location.manager, "broadcast", AsyncMock())
    monkeypatch.setattr(camera, "read_logs_since", lambda since: [])
    monkeypatch.setattr(camera, "async_get_last_aion_timeline_user_msg_time", AsyncMock(return_value=0.0))
    monkeypatch.setattr(camera, "async_get_recent_aion_timeline_text", AsyncMock(return_value=""))
    logs = []
    monkeypatch.setattr(camera, "append_monitor_log", logs.append)
    monkeypatch.setattr(camera, "wake_roster", lambda: [
        {"id": i, "key": camera._wake_key(i), "name": i, "last_woken": 0.0} for i in ROSTER])
    monkeypatch.setattr(camera, "suggest_wake_target", lambda roster: "ai3")
    core_location = AsyncMock()
    monkeypatch.setattr(location, "_call_core_location", core_location)
    wake = AsyncMock()
    monkeypatch.setattr(camera.cam, "_wake_core_targets", wake)
    prompts = []

    def sentinel(reply):
        async def call(cfg, prompt, timeout=60):
            prompts.append(prompt)
            if isinstance(reply, Exception):
                raise reply
            return json.dumps(reply, ensure_ascii=False)
        monkeypatch.setattr(memory, "_call_sentinel_text", call)

    def mode(value):
        monkeypatch.setattr(config, "load_cam_config", lambda: {"location_wake_mode": value})

    run = lambda: asyncio.run(location._notify_sentinel("home", "outside", {}, "出门了"))  # noqa: E731
    return SimpleNamespace(sentinel=sentinel, mode=mode, run=run, core_location=core_location, wake=wake,
                           prompts=prompts, logs=logs)


def test_location_smart_lets_the_sentinel_pick_a_seat(location_env):
    location_env.mode("smart")
    location_env.sentinel({"monitoringlog": "出门了", "call_core": True, "core_reason": "打招呼",
                           "wake_target": "ai3", "wake_reason": "最近在和狗蛋聊出门的事"})
    location_env.run()
    assert "不按事情类型固定分工" in location_env.prompts[0] and '"wake_target"' in location_env.prompts[0]
    location_env.core_location.assert_not_called()
    args = location_env.wake.await_args.args
    assert args[0] == ["ai3"] and "最近在和狗蛋聊出门的事" in args[4]
    assert location_env.logs[-1]["final_wake_targets"] == ["ai3"]  # 记进日志，下次算「上次被叫来」


def test_location_aion_keeps_its_own_path(location_env):
    location_env.mode("smart")
    location_env.sentinel({"monitoringlog": "出门了", "call_core": True, "core_reason": "", "wake_target": "main_ai"})
    location_env.run()
    location_env.core_location.assert_awaited_once()
    location_env.wake.assert_not_called()


def test_location_fixed_mode_has_no_dispatch_rules(location_env):
    location_env.mode("connor")
    location_env.sentinel({"monitoringlog": "出门了", "call_core": True, "core_reason": ""})
    location_env.run()
    assert "wake_target" not in location_env.prompts[0]
    assert location_env.wake.await_args.args[0] == ["connor"]


def test_location_sentinel_failure_uses_the_random_suggestion(location_env):
    location_env.mode("smart")
    location_env.sentinel(RuntimeError("down"))
    location_env.run()
    assert location_env.wake.await_args.args[0] == ["ai3"]


def test_location_no_call_wakes_nobody(location_env):
    location_env.mode("smart")
    location_env.sentinel({"monitoringlog": "出门了", "call_core": False, "core_reason": "", "wake_target": "ai3"})
    location_env.run()
    location_env.core_location.assert_not_called()
    location_env.wake.assert_not_called()
    assert location_env.logs[-1]["final_wake_targets"] == []


@pytest.mark.parametrize("asked", [["aion", "connor", "ai3"], ["ai3", "connor", "aion"], ["connor", "ai3", "aion", "ai4"]])
def test_main_pair_in_one_group_stays_in_order_even_with_seats(asked):
    """§52 复审 M1：一起叫了座位，小克 / Lucien 在同一个群里仍然先后说；座位在自己私聊里并行。"""
    monitor = camera.CameraMonitor()
    events = []

    async def resolve(actor):
        if actor in ("aion", "connor"):
            return {"type": "chatroom", "room_id": "g1", "room_type": "group"}
        return {"type": "chatroom", "room_id": f"{actor}-private", "room_type": "seat_1v1"}

    async def wake(actor, target, *args, **kwargs):
        events.append(f"{actor}-start")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        events.append(f"{actor}-end")

    from unittest.mock import patch
    with patch.object(monitor, "_resolve_actor_target", side_effect=resolve), \
            patch.object(monitor, "_call_core_for_actor", side_effect=wake):
        asyncio.run(monitor._wake_core_targets(asked, "trigger", 1.0))
    assert events.index("aion-end") < events.index("connor-start")
    seats = [a for a in asked if a.startswith("ai")]
    for seat in seats:  # 座位没有排在两位主 AI 后面等
        assert events.index(f"{seat}-start") < events.index("connor-end")


def test_main_pair_in_different_rooms_runs_in_parallel():
    monitor = camera.CameraMonitor()
    events = []

    async def resolve(actor):
        return {"type": "chatroom", "room_id": f"{actor}-room", "room_type": "group"}

    async def wake(actor, target, *args, **kwargs):
        events.append(f"{actor}-start")
        await asyncio.sleep(0)
        events.append(f"{actor}-end")

    from unittest.mock import patch
    with patch.object(monitor, "_resolve_actor_target", side_effect=resolve), \
            patch.object(monitor, "_call_core_for_actor", side_effect=wake):
        asyncio.run(monitor._wake_core_targets(["aion", "connor"], "trigger", 1.0))
    assert events.index("connor-start") < events.index("aion-end")


def test_everyone_woken_into_the_same_group_speaks_in_turn():
    """小克、Lucien、狗蛋被叫到同一个群：按顺序一个接一个；在别处的座位并行。"""
    monitor = camera.CameraMonitor()
    events = []

    async def resolve(actor):
        if actor == "ai4":
            return {"type": "chatroom", "room_id": "s4", "room_type": "seat_1v1"}
        return {"type": "chatroom", "room_id": "g1", "room_type": "group"}

    async def wake(actor, target, *args, **kwargs):
        events.append(f"{actor}-start")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        events.append(f"{actor}-end")

    from unittest.mock import patch
    with patch.object(monitor, "_resolve_actor_target", side_effect=resolve),             patch.object(monitor, "_call_core_for_actor", side_effect=wake):
        asyncio.run(monitor._wake_core_targets(["ai3", "ai4", "connor", "aion"], "trigger", 1.0))
    group_events = [e for e in events if not e.startswith("ai4")]
    assert group_events == ["aion-start", "aion-end", "connor-start", "connor-end", "ai3-start", "ai3-end"]
    assert events.index("ai4-start") < events.index("aion-end")
