"""狗蛋补能力第三批：记住事情——座位回复里的 [MEMORY:内容|分类|关于谁] 按 Hub 约定写进 Memory Hub。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import capabilities
import memory_hub_bridge
import routes.chatroom as chatroom_routes


@pytest.fixture
def room(monkeypatch):
    notes = AsyncMock(return_value={"id": "sys1"})
    monkeypatch.setattr(chatroom_routes, "_chatroom_sys_msg", notes)
    monkeypatch.setattr(chatroom_routes, "_name_for_identity", lambda a: "狗蛋")
    import config
    monkeypatch.setattr(config, "load_worldbook", lambda: {"user_name": "小猫"})
    remember = MagicMock(side_effect=lambda actor, items, request_key: [i["content"] for i in items])
    monkeypatch.setattr(memory_hub_bridge, "schedule_remember", remember)
    return notes, remember


def _run(text, user_text=""):
    return asyncio.run(chatroom_routes._process_seat_commands(text, "s3", "ai3", "m1", asyncio.Queue(),
                                                              user_text=user_text))


def test_seat_memory_goes_to_hub_with_speaker_subject_and_room(room, monkeypatch):
    notes, remember = room
    monkeypatch.setattr(chatroom_routes, "is_capability_enabled", lambda name: True)
    text, _ = _run("好，记下了[MEMORY:小猫喜欢吃芒果|preferences][MEMORY:阿雅下周搬家｜people｜阿雅]",
                   user_text="记住哦，我喜欢芒果")
    assert text == "好，记下了"
    actor, items = remember.call_args.args
    assert actor == "ai3" and remember.call_args.kwargs == {"request_key": "m1"}
    assert items == [
        {"content": "小猫喜欢吃芒果", "room": "preferences", "speaker_name": "小猫", "subject_name": "小猫",
         "importance": 0.75},
        {"content": "阿雅下周搬家", "room": "people", "speaker_name": "小猫", "subject_name": "阿雅",
         "importance": 0.75},
    ]
    assert [c.args[1] for c in notes.await_args_list] == ["💾 狗蛋记住了：小猫喜欢吃芒果", "💾 狗蛋记住了：阿雅下周搬家"]


def test_plain_memory_defaults_and_no_notice_when_nothing_queued(room, monkeypatch):
    notes, remember = room
    monkeypatch.setattr(chatroom_routes, "is_capability_enabled", lambda name: True)
    remember.side_effect = lambda *a, **k: []  # 记过了 / Hub 没开
    text, _ = _run("嗯[MEMORY:小猫怕打雷]")
    assert text == "嗯"
    assert remember.call_args.args[1] == [{"content": "小猫怕打雷", "room": "", "speaker_name": "小猫",
                                           "subject_name": "小猫", "importance": 0.5}]
    notes.assert_not_called()


def test_switch_off_only_strips(room, monkeypatch):
    notes, remember = room
    monkeypatch.setattr(chatroom_routes, "is_capability_enabled", lambda name: name != "memory_write")
    text, _ = _run("嗯[MEMORY:小猫怕打雷]")
    assert text == "嗯"
    remember.assert_not_called()
    notes.assert_not_called()


def test_prompt_offers_memory_only_with_hub_identity(monkeypatch):
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: name == "memory_write")
    monkeypatch.setattr(memory_hub_bridge, "can_remember", lambda actor: actor == "ai3")
    texts = capabilities.seat_ability_texts("小猫", "ai3")
    assert any(t.startswith("[MEMORY:内容|分类]") and "第三人称" in t and "[梗]" in t for t in texts)
    assert not any("[MEMORY:" in t for t in capabilities.seat_ability_texts("小猫", "ai4"))
    assert not any("[MEMORY:" in t for t in capabilities.seat_ability_texts("小猫"))


def test_reply_passes_the_users_line_but_not_for_system_triggers(monkeypatch):
    import seat_chat
    monkeypatch.setattr(seat_chat, "check_ready", lambda a: ({}, "seat-m"))
    import config
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "seat-m": {"provider": "custom_openai", "model": "m"}})
    monkeypatch.setattr(seat_chat, "merged_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(seat_chat, "build_context", AsyncMock(return_value=[{"role": "user", "content": "x"}]))

    async def stream(*a, **k):
        yield "好"
    monkeypatch.setattr(chatroom_routes, "stream_ai", stream)
    monkeypatch.setattr(chatroom_routes, "_save_msg", AsyncMock(return_value={"id": "x"}))
    monkeypatch.setattr(memory_hub_bridge, "schedule_capture", MagicMock())
    seen = []

    async def commands(text, *a, user_text="", **k):
        seen.append(user_text)
        return text, {}
    monkeypatch.setattr(chatroom_routes, "_process_seat_commands", commands)
    msgs = [{"sender": "user", "content": "记住我怕打雷"}]
    asyncio.run(chatroom_routes._generate_seat_reply("s3", {"actor": "ai3"}, msgs, asyncio.Queue(), 30, actor="ai3"))
    asyncio.run(chatroom_routes._generate_seat_reply("s3", {"actor": "ai3"}, msgs, asyncio.Queue(), 30, actor="ai3",
                                                     extra_prompt="[闹铃到点]"))
    assert seen == ["记住我怕打雷", ""]
