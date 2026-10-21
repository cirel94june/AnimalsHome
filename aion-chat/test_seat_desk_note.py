"""桌面便笺：狗蛋等座位和小克 / Lucien 一样可以写 [NOTE:…]，以 TA 的名义贴在桌面上。"""

import asyncio
from unittest.mock import AsyncMock

import capabilities
import desk_notes
import routes.chatroom as chatroom_routes
import seat_chat


def test_prompt_offers_desk_note_with_the_switch(monkeypatch):
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: name == "desk_note")
    assert capabilities.desk_note_ability_text("小猫") in capabilities.seat_ability_texts("小猫")
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: False)
    assert not any(t.startswith("[NOTE:") for t in capabilities.seat_ability_texts("小猫"))


def test_seat_note_is_posted_under_its_own_name(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_notes, "NOTES_PATH", tmp_path / "notes.json")
    monkeypatch.setattr(seat_chat, "check_ready", lambda a: ({}, "seat-m"))
    import config
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "seat-m": {"provider": "custom_openai", "model": "m"}})
    monkeypatch.setattr(seat_chat, "merged_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(seat_chat, "build_context", AsyncMock(return_value=[{"role": "user", "content": "x"}]))

    async def stream(*a, **k):
        yield "晚安[NOTE:明早记得吃早饭]"
    monkeypatch.setattr(chatroom_routes, "stream_ai", stream)
    saved = AsyncMock(return_value={"id": "x"})
    monkeypatch.setattr(chatroom_routes, "_save_msg", saved)
    import memory_hub_bridge
    monkeypatch.setattr(memory_hub_bridge, "schedule_capture", lambda *a, **k: None)
    asyncio.run(chatroom_routes._generate_seat_reply("s3", {"actor": "ai3"}, [], asyncio.Queue(), 30, actor="ai3"))
    assert saved.await_args.args[2] == "晚安"
    assert [(n["author"], n["text"]) for n in desk_notes.load_notes()] == [("ai3", "明早记得吃早饭")]
