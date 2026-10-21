"""内心旁白：狗蛋等座位和小克 / Lucien 一样可以写 [心里嘀咕：…]，保留在回复里由前端特殊显示。"""

import asyncio
from unittest.mock import AsyncMock

import capabilities
import routes.chatroom as chatroom_routes
import seat_chat


def test_prompt_offers_inner_monologue_with_the_switch(monkeypatch):
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: name == "inner_monologue")
    assert capabilities.INNER_MONOLOGUE_ABILITY_TEXT in capabilities.seat_ability_texts("小猫")
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: False)
    assert capabilities.INNER_MONOLOGUE_ABILITY_TEXT not in capabilities.seat_ability_texts("小猫")


def test_seat_reply_keeps_the_monologue_for_the_page(monkeypatch):
    monkeypatch.setattr(seat_chat, "check_ready", lambda a: ({}, "seat-m"))
    import config
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "seat-m": {"provider": "custom_openai", "model": "m"}})
    monkeypatch.setattr(seat_chat, "merged_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(seat_chat, "build_context", AsyncMock(return_value=[{"role": "user", "content": "x"}]))

    async def stream(*a, **k):
        yield "快去睡[心里嘀咕：她又熬夜了，"
        yield "真拿她没办法]晚安"
    monkeypatch.setattr(chatroom_routes, "stream_ai", stream)
    saved = AsyncMock(return_value={"id": "x"})
    monkeypatch.setattr(chatroom_routes, "_save_msg", saved)
    import memory_hub_bridge
    monkeypatch.setattr(memory_hub_bridge, "schedule_capture", lambda *a, **k: None)
    chunks = []
    q = asyncio.Queue()
    asyncio.run(chatroom_routes._generate_seat_reply("s3", {"actor": "ai3"}, [], q, 30, actor="ai3"))
    while not q.empty():
        event = q.get_nowait()
        if event.get("type") == "seat_chunk":
            chunks.append(event["content"])
    assert saved.await_args.args[2] == "快去睡[心里嘀咕：她又熬夜了，真拿她没办法]晚安"
    assert "心里嘀咕" in "".join(chunks)  # 流式显示时也不吞掉
