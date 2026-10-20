"""狗蛋补能力第三批：座位看监控（[CAM_CHECK]）、看动态（[查看动态:n]）——结果由 TA 用自己的人设和模型接着说。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import routes.chatroom as chatroom_routes
import seat_chat


@pytest.fixture
def room(monkeypatch):
    notes = AsyncMock(return_value={"id": "sys1"})
    monkeypatch.setattr(chatroom_routes, "_chatroom_sys_msg", notes)
    monkeypatch.setattr(chatroom_routes, "_name_for_identity", lambda a: "狗蛋" if a == "ai3" else a)
    import activity
    monkeypatch.setattr(activity, "is_activity_tracking_enabled", lambda: True)
    return notes


def _run(text):
    return asyncio.run(chatroom_routes._process_seat_commands(text, "s3", "ai3", "m1", asyncio.Queue()))


def test_seat_can_ask_to_look(room, monkeypatch):
    monkeypatch.setattr(chatroom_routes, "is_capability_enabled", lambda name: True)
    text, triggered = _run("我看看[CAM_CHECK]，再看看你在干嘛[查看动态:30]")
    assert text == "我看看，再看看你在干嘛"
    assert triggered["cam_check"] == "sys1" and triggered["activity"] == 12  # 最多 12（两小时）
    shown = [c.args[1] for c in room.await_args_list]
    assert "📷 狗蛋查看了监控" in shown and "📊 狗蛋查看了用户动态" in shown


def test_switched_off_only_strips(room, monkeypatch):
    monkeypatch.setattr(chatroom_routes, "is_capability_enabled", lambda name: name not in ("cam_check", "activity_check"))
    text, triggered = _run("嗯[CAM_CHECK][查看动态:2]")
    assert text == "嗯" and "cam_check" not in triggered and "activity" not in triggered
    room.assert_not_called()


def test_followups_carry_the_line_snapshot(monkeypatch):
    spawned = []
    monkeypatch.setattr(chatroom_routes, "spawn_generation_task",
                        lambda coro: (spawned.append((coro.cr_code.co_name, coro.cr_frame.f_locals.get("pinned"))), coro.close()))
    chatroom_routes._spawn_seat_followups("s3", "ai3", "m", {"cam_check": "sys1", "activity": 3}, {"model": "x"})
    assert ("_chatroom_cam_check", {"model": "x"}) in spawned and ("_chatroom_activity_check", {"model": "x"}) in spawned


def _seat_line(monkeypatch):
    import config
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "seat-m": {"provider": "custom_openai", "model": "m"}})
    monkeypatch.setattr(seat_chat, "_persona_text", lambda a: "本少爷人设")
    monkeypatch.setattr(chatroom_routes, "_load_room_and_messages", AsyncMock(return_value=({}, [])))
    monkeypatch.setattr(chatroom_routes, "_name_for_identity", lambda a: "狗蛋")
    seen = []

    async def stream(messages, model_key, *a, **k):
        seen.append((messages, model_key))
        yield "看到你在打游戏"
    monkeypatch.setattr(chatroom_routes, "stream_ai", stream)
    connor = MagicMock()
    monkeypatch.setattr(chatroom_routes, "_stream_connor_model", connor)
    saved = AsyncMock(return_value={"id": "x"})
    monkeypatch.setattr(chatroom_routes, "_save_msg", saved)
    import schedule
    background = AsyncMock()
    monkeypatch.setattr(schedule, "_process_background_reply_commands", background)
    return seen, connor, saved, background


def test_activity_followup_speaks_as_the_seat(monkeypatch):
    import activity
    monkeypatch.setattr(activity, "get_activity_summary_for_prompt", lambda n: "在玩游戏")
    seen, connor, saved, background = _seat_line(monkeypatch)
    asyncio.run(chatroom_routes._chatroom_activity_check("s3", "ai3", "seat-m", 3))
    messages, model_key = seen[0]
    assert model_key == "seat-m" and "本少爷人设" in messages[0]["content"]
    connor.assert_not_called()
    background.assert_not_called()
    assert saved.await_args.args[:3] == ("s3", "ai3", "看到你在打游戏")


def test_cam_followup_speaks_as_the_seat_and_stops_if_line_changes(monkeypatch):
    import camera
    monkeypatch.setattr(camera, "cam", SimpleNamespace(cfg={"active_source": "phone"}))
    monkeypatch.setattr(camera, "build_monitor_alert_data", lambda label: {})
    monkeypatch.setattr(camera, "acquire_monitor_image", AsyncMock(return_value=SimpleNamespace(
        camera_jpeg=None, jpeg=None, source="device", no_image_context="")))
    monkeypatch.setattr(camera, "save_monitor_camera_snapshot", lambda *a: None)
    monkeypatch.setattr(camera, "save_monitor_screenshot", lambda *a: None)
    monkeypatch.setattr(chatroom_routes.manager, "broadcast", AsyncMock())
    monkeypatch.setattr(chatroom_routes, "_broadcast_chatroom_ai_status", AsyncMock())
    seen, connor, saved, background = _seat_line(monkeypatch)
    asyncio.run(chatroom_routes._chatroom_cam_check("s3", "ai3", "seat-m", delay=0, system_msg_id="sys1"))
    assert seen and seen[0][1] == "seat-m" and "本少爷人设" in seen[0][0][0]["content"]
    connor.assert_not_called()
    background.assert_not_called()
    assert saved.await_args.args[1] == "ai3"

    import config
    pinned = config.model_snapshot("seat-m")
    config.MODELS.pop("seat-m")  # 截图期间线路被删
    notes = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_chatroom_sys_msg", notes)
    seen.clear()
    saved.reset_mock()
    asyncio.run(chatroom_routes._chatroom_cam_check("s3", "ai3", "seat-m", delay=0, system_msg_id="sys1", pinned=pinned))
    assert not seen and "线路刚刚变了" in notes.await_args.args[1]
    saved.assert_not_called()


def test_seat_prompt_offers_looking(monkeypatch):
    import capabilities
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: name in ("cam_check", "activity_check"))
    monkeypatch.setattr(capabilities, "is_activity_tracking_enabled", lambda: True)
    texts = capabilities.seat_ability_texts("小猫")
    assert any(t.startswith("[CAM_CHECK]") or "CAM_CHECK" in t for t in texts) and any(t.startswith("[查看动态:n]") for t in texts)
