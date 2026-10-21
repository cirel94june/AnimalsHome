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


def test_waking_a_seat_speaks_in_its_private_room_with_the_snapshot(monkeypatch):
    import routes.chatroom as chatroom_routes
    monkeypatch.setattr(chatroom_routes, "_get_or_create_seat_room", AsyncMock(return_value="s3"))
    reply = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_generate_seat_reply", reply)
    monkeypatch.setattr(camera, "load_worldbook", lambda: {"user_name": "小猫"})
    monitor = camera.CameraMonitor()
    results = asyncio.run(monitor._wake_core_targets(["ai3"], "小猫在沙发上发呆", 0.0, "", "很久没说话", [], "shot.jpg"))
    assert results["ai3"]["ok"]
    args, kwargs = reply.await_args
    assert args[0] == "s3" and kwargs["actor"] == "ai3" and kwargs["group"] is False
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
