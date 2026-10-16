"""狗蛋补能力第二批：座位的点歌、许愿、联网搜索、自拍 / 画画，都按座位自己的身份做。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import capabilities
import routes.chatroom as chatroom_routes
import seat_chat


@pytest.fixture
def room(monkeypatch):
    sys_msgs = AsyncMock(return_value={"id": "sys"})
    monkeypatch.setattr(chatroom_routes, "_chatroom_sys_msg", sys_msgs)
    monkeypatch.setattr(chatroom_routes, "_name_for_identity", lambda a: "狗蛋" if a == "ai3" else a)
    import ws
    monkeypatch.setattr(ws.manager, "broadcast", AsyncMock())
    return sys_msgs


def _enable(monkeypatch, *off):
    monkeypatch.setattr(chatroom_routes, "is_capability_enabled", lambda name: name not in off)


def _run(text):
    q = asyncio.Queue()
    out = asyncio.run(chatroom_routes._process_seat_commands(text, "room", "ai3", "m1", q))
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    return out, events


def test_song_request_is_played_and_recorded_under_the_seat(room, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(chatroom_routes, "search_songs", lambda k, limit=5: [{"id": 1, "name": "晴天", "artist": "周杰伦"}])
    monkeypatch.setattr(chatroom_routes, "get_audio_url", lambda i: "http://x/1.mp3")
    record = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "record_music_request", record)
    (text, triggered), events = _run("给你点一首[MUSIC:晴天 周杰伦]")
    assert text == "给你点一首" and triggered["music_cards"][0]["name"] == "晴天"
    assert "狗蛋点了一首《晴天》" in room.await_args.args[1]
    assert events[0]["type"] == "music"
    assert record.await_args.kwargs["requester_identity"] == "ai3" and record.await_args.kwargs["requester_name"] == "狗蛋"


def test_switched_off_abilities_are_only_stripped(room, monkeypatch):
    _enable(monkeypatch, "music", "wish", "web_search", "image_gen")
    search = MagicMock()
    monkeypatch.setattr(chatroom_routes, "search_songs", search)
    (text, triggered), _ = _run("嗯[MUSIC:x][许愿：y][WEB_SEARCH:z][SELFIE: a]")
    assert text == "嗯" and triggered == {}
    search.assert_not_called()
    room.assert_not_called()


def test_wish_is_the_seats_own(room, monkeypatch):
    _enable(monkeypatch)
    import wish_pool
    create = AsyncMock()
    monkeypatch.setattr(wish_pool, "create_wish", create)
    (text, triggered), _ = _run("[许愿：想去海边]好")
    assert text == "好" and triggered["wish"]
    assert create.await_args.kwargs["author"] == "ai3" and create.await_args.kwargs["author_name"] == "狗蛋"


def test_web_search_and_selfie_wait_until_the_reply_is_saved(room, monkeypatch):
    _enable(monkeypatch)
    (text, triggered), _ = _run("我查查[WEB_SEARCH:今天天气]再拍一张[SELFIE: smiling in the rain]")
    assert text == "我查查再拍一张"
    assert triggered["web_search"]["searches"] == ["今天天气"]
    assert triggered["image_gen"] == {"prompt": "smiling in the rain", "is_selfie": True}
    spawned = []
    monkeypatch.setattr(chatroom_routes, "spawn_generation_task", lambda coro: (spawned.append(coro.cr_code.co_name), coro.close()))
    chatroom_routes._spawn_seat_followups("room", "ai3", "m", triggered)
    assert spawned == ["_chatroom_image_gen", "_chatroom_web_search"]


def test_search_followup_speaks_as_the_seat_with_its_own_model(monkeypatch):
    monkeypatch.setattr(chatroom_routes, "run_web_commands", AsyncMock(return_value=["【联网搜索结果】晴"]))
    monkeypatch.setattr(chatroom_routes, "_load_room_and_messages", AsyncMock(return_value=({}, [])))
    monkeypatch.setattr(chatroom_routes, "_name_for_identity", lambda a: "狗蛋")
    monkeypatch.setattr(seat_chat, "_persona_text", lambda a: "本少爷人设")
    seen = []

    async def fake_stream(messages, model_key, *a, **k):
        seen.append((messages, model_key))
        yield "今天晴，本少爷查过了"
    monkeypatch.setattr(chatroom_routes, "stream_ai", fake_stream)
    connor = MagicMock()
    monkeypatch.setattr(chatroom_routes, "_stream_connor_model", connor)
    saved = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_save_msg", saved)
    import schedule
    background = AsyncMock()
    monkeypatch.setattr(schedule, "_process_background_reply_commands", background)
    asyncio.run(chatroom_routes._chatroom_web_search("room", "ai3", "seat-model", {"searches": ["天气"]}))
    messages, model_key = seen[0]
    assert model_key == "seat-model" and "本少爷人设" in messages[0]["content"]
    connor.assert_not_called()
    background.assert_not_called()
    assert saved.await_args.args[:3] == ("room", "ai3", "今天晴，本少爷查过了")


def test_reply_that_only_requests_a_song_ends_normally(room, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(seat_chat, "check_ready", lambda a: ({"name": "狗蛋"}, "m"))
    monkeypatch.setattr(seat_chat, "merged_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(seat_chat, "build_context", AsyncMock(return_value=[{"role": "user", "content": "x"}]))

    async def stream(*a, **k):
        yield "[MUSIC:晴天]"
    monkeypatch.setattr(chatroom_routes, "stream_ai", stream)
    monkeypatch.setattr(chatroom_routes, "process_note_commands", AsyncMock(side_effect=lambda t, a: t))
    monkeypatch.setattr(chatroom_routes, "search_songs", lambda k, limit=5: [{"id": 1, "name": "晴天", "artist": "周杰伦"}])
    monkeypatch.setattr(chatroom_routes, "get_audio_url", lambda i: "u")
    monkeypatch.setattr(chatroom_routes, "record_music_request", AsyncMock())
    failure = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_save_chatroom_reply_failure", failure)
    import config
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "m": {"provider": "custom_openai", "model": "m"}})
    q = asyncio.Queue()
    asyncio.run(chatroom_routes._generate_seat_reply("room", {"actor": "ai3"}, [], q, 30, actor="ai3"))
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    failure.assert_not_called()
    assert events[-1] == {"type": "seat_done", "actor": "ai3", "message": None}


def test_seat_and_main_ais_get_the_same_instruction_text(monkeypatch):
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: True)
    monkeypatch.setattr(capabilities, "_is_web_search_available", lambda: True)
    texts = capabilities.seat_ability_texts("小猫")
    assert texts == [capabilities.music_ability_text(), capabilities.web_search_ability_text(),
                     capabilities.image_gen_ability_text("小猫"), capabilities.wish_ability_text()]
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: name != "image_gen")
    assert capabilities.image_gen_ability_text("小猫") not in capabilities.seat_ability_texts("小猫")
    lines = seat_chat._ability_lines("小猫", group=False)
    assert any(line.startswith("[MUSIC") for line in lines) and any(line.startswith("[去群里") for line in lines)
