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
    import config
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "seat-model": {"provider": "custom_openai", "model": "m"}})
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


def test_wish_author_is_stored_as_the_seat(tmp_path, monkeypatch):
    """Codex M1：真实写进数据库，作者是 ai3～ai6，不是用户；不认识的身份报错。"""
    import aiosqlite
    import wish_pool
    monkeypatch.setattr(wish_pool, "get_db", lambda: aiosqlite.connect(tmp_path / "w.db"))
    monkeypatch.setattr(wish_pool.manager, "broadcast", AsyncMock())
    monkeypatch.setattr(seat_chat.actors, "list_actors",
                        lambda include_disabled=True: [{"id": "ai3", "name": "狗蛋"}, {"id": "ai4", "name": "四号"}])

    async def go():
        for seat in ("ai3", "ai4", "ai5", "ai6"):
            await wish_pool.create_wish(author=seat, content=f"{seat} 的愿望")
        with pytest.raises(ValueError):
            await wish_pool.create_wish(author="stranger", content="x")
        async with aiosqlite.connect(tmp_path / "w.db") as db:
            rows = await (await db.execute("SELECT author, author_name FROM wishes ORDER BY author")).fetchall()
        mine = await wish_pool.list_wishes(author="ai3")
        return rows, mine, await wish_pool.get_actor_names()
    rows, mine, names = asyncio.run(go())
    assert [r[0] for r in rows] == ["ai3", "ai4", "ai5", "ai6"]
    assert rows[0][1] == "狗蛋"
    assert [w["author"] for w in mine["items"]] == ["ai3"]
    assert names["ai3"] == "狗蛋"


def test_search_followup_stops_if_the_seats_line_changes_meanwhile(monkeypatch):
    """Codex M2：搜索等待期间线路被删，不换模型、不以座位名义回复，留一句说明。"""
    import config
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "seat-key": {"provider": "custom_openai", "model": "m"}})
    pinned = config.model_snapshot("seat-key")

    async def search(*a):
        config.MODELS.pop("seat-key")  # 搜索的时候线路被删了
        return ["【联网搜索结果】晴"]
    monkeypatch.setattr(chatroom_routes, "run_web_commands", search)
    monkeypatch.setattr(chatroom_routes, "_load_room_and_messages", AsyncMock(return_value=({}, [])))
    monkeypatch.setattr(chatroom_routes, "_name_for_identity", lambda a: "狗蛋")
    monkeypatch.setattr(seat_chat, "_persona_text", lambda a: "人设")
    stream = MagicMock()
    monkeypatch.setattr(chatroom_routes, "stream_ai", stream)
    saved = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_save_msg", saved)
    notes = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_chatroom_sys_msg", notes)
    asyncio.run(chatroom_routes._chatroom_web_search("room", "ai3", "seat-key", {"searches": ["天气"]}, pinned=pinned))
    stream.assert_not_called()
    saved.assert_not_called()
    assert "线路刚刚变了" in notes.await_args.args[1]


def test_only_a_song_becomes_the_seats_message_with_the_card(room, monkeypatch):
    """Codex L1：只点了歌时，歌名作为 TA 的消息保存，播放卡片挂在这条消息上。"""
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
    saved = []

    async def save(room_id, sender, content, msg_id=None, attachments=None, **kw):
        saved.append({"id": msg_id, "content": content, "attachments": attachments})
        return saved[-1]
    monkeypatch.setattr(chatroom_routes, "_save_msg", save)
    import config
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "m": {"provider": "custom_openai", "model": "m"}})
    q = asyncio.Queue()
    asyncio.run(chatroom_routes._generate_seat_reply("room", {"actor": "ai3"}, [], q, 30, actor="ai3"))
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    music = next(e for e in events if e["type"] == "music")
    assert saved[0]["content"] == "🎵 《晴天》- 周杰伦" and saved[0]["attachments"][0]["type"] == "music"
    assert music["msg_id"] == saved[0]["id"]  # 卡片挂在真正保存了的消息上
    assert events[-1]["message"] == saved[0]


def test_wish_and_picture_leave_a_visible_note(room, monkeypatch):
    """Codex M3：只许愿 / 只画图时房间里也有提示；前端收到 seat_done 就收起「回复中」。"""
    _enable(monkeypatch)
    import wish_pool
    monkeypatch.setattr(wish_pool, "create_wish", AsyncMock())
    _run("[许愿：想去海边]")
    assert "许愿池" in room.await_args.args[1]
    _run("[DRAW: a cat]")
    assert "在画图" in room.await_args.args[1]
    js = open("static/chatroom.js", encoding="utf-8").read()
    seat_done = js[js.index("case 'seat_done':"):js.index("case 'round_start':")]
    assert ".typing-indicator" in seat_done


def test_seat_picture_failure_is_visible_but_main_ais_unchanged(monkeypatch):
    import image_gen
    monkeypatch.setattr(image_gen, "generate_image", AsyncMock(return_value=None))
    monkeypatch.setattr(chatroom_routes, "_name_for_identity", lambda a: "狗蛋")
    notes = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_chatroom_sys_msg", notes)
    asyncio.run(chatroom_routes._chatroom_image_gen("room", "ai3", "a cat", False))
    assert "图没生成出来" in notes.await_args.args[1]
    notes.reset_mock()
    asyncio.run(chatroom_routes._chatroom_image_gen("room", "aion", "a cat", False))
    notes.assert_not_called()
