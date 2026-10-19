"""狗蛋补能力第三批第二段：座位的朋友圈——发动态、评论、回复，都按座位自己的身份。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

import routes.chatroom as chatroom_routes
import routes.moments as moments
import seat_chat


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "m.db"

    async def setup():
        async with aiosqlite.connect(path) as conn:
            await conn.execute("CREATE TABLE moments (id TEXT PRIMARY KEY, author TEXT, content TEXT, attachments TEXT DEFAULT '[]', "
                               "source_conv TEXT, source_msg_id TEXT, expect_reply INTEGER DEFAULT 0, created_at REAL)")
            await conn.execute("CREATE TABLE moment_comments (id TEXT PRIMARY KEY, moment_id TEXT, author TEXT, content TEXT, "
                               "reply_to_id TEXT, created_at REAL)")
            await conn.execute("CREATE TABLE moment_reactions (id TEXT PRIMARY KEY, moment_id TEXT, author TEXT, type TEXT, "
                               "created_at REAL, UNIQUE(moment_id, author))")
            await conn.execute("INSERT INTO moments (id, author, content, created_at) VALUES ('mu', 'user', '今天下雨了', 1)")
            await conn.commit()
    asyncio.run(setup())
    connect = lambda: aiosqlite.connect(path)  # noqa: E731
    monkeypatch.setattr(moments, "get_db", connect)
    monkeypatch.setattr(chatroom_routes, "get_db", connect)
    monkeypatch.setattr(moments, "broadcast_synced", AsyncMock())
    monkeypatch.setattr(chatroom_routes, "broadcast_synced", AsyncMock())
    monkeypatch.setattr(seat_chat.actors, "display_name", lambda a: {"ai3": "狗蛋"}.get(a, a))
    return path


def _rows(path, sql):
    async def go():
        async with aiosqlite.connect(path) as conn:
            return await (await conn.execute(sql)).fetchall()
    return asyncio.run(go())


def test_seat_posts_a_moment_as_itself(db, monkeypatch):
    monkeypatch.setattr(chatroom_routes, "is_capability_enabled", lambda name: True)
    notes = AsyncMock(return_value={"id": "sys"})
    monkeypatch.setattr(chatroom_routes, "_chatroom_sys_msg", notes)
    monkeypatch.setattr(chatroom_routes, "_name_for_identity", lambda a: "狗蛋")
    spawned = []
    monkeypatch.setattr(chatroom_routes, "spawn_generation_task", lambda coro: (spawned.append(coro.cr_frame.f_locals), coro.close()))
    import ws
    monkeypatch.setattr(ws.manager, "broadcast", AsyncMock())
    q = asyncio.Queue()
    text, triggered = asyncio.run(chatroom_routes._process_seat_commands("嘿嘿[MOMENT:本少爷今天超帅|true]", "g1", "ai3", "m1", q))
    assert text == "嘿嘿" and triggered["moment"]
    assert _rows(db, "SELECT author, content, expect_reply FROM moments WHERE id!='mu'") == [("ai3", "本少爷今天超帅", 1)]
    assert "狗蛋发了一条朋友圈" in notes.await_args.args[1]
    assert spawned and spawned[0]["exclude_author"] == "ai3"


def test_moment_switch_off_only_strips(db, monkeypatch):
    monkeypatch.setattr(chatroom_routes, "is_capability_enabled", lambda name: name != "moment")
    text, triggered = asyncio.run(chatroom_routes._process_seat_commands("嗯[MOMENT:x|false]", "g1", "ai3", "m1", asyncio.Queue()))
    assert text == "嗯" and "moment" not in triggered
    assert _rows(db, "SELECT COUNT(*) FROM moments") == [(1,)]


def test_ready_seats_join_the_comment_round(monkeypatch):
    def ready(actor):
        if actor != "ai3":
            raise seat_chat.SeatNotReady("no")
        return {}, "m"
    monkeypatch.setattr(seat_chat, "check_ready", ready)
    assert moments._ai_authors() == ("aion", "connor", "ai3")
    called = []
    monkeypatch.setattr(moments, "_ai_reply_to_moment", AsyncMock(side_effect=lambda who, mid: called.append(who)))
    monkeypatch.setattr(moments.asyncio, "sleep", AsyncMock())
    asyncio.run(moments._trigger_ai_replies("mu", exclude_author="connor"))
    assert sorted(called) == ["ai3", "aion"]


def _seat_reply_setup(monkeypatch, model="seat-m"):
    import config
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "seat-m": {"provider": "custom_openai", "model": "m"}})
    monkeypatch.setattr(seat_chat, "seat_model", lambda a: model)
    monkeypatch.setattr(seat_chat, "_persona_text", lambda a: "本少爷人设")
    monkeypatch.setattr(seat_chat, "merged_messages", AsyncMock(return_value=[
        {"sender": "user", "content": "私聊里的心事", "created_at": 1, "source": "private"}]))
    import memory_hub_bridge
    hub = AsyncMock(return_value="【记忆】狗蛋喜欢雨天")
    monkeypatch.setattr(memory_hub_bridge, "context_block", hub)
    monkeypatch.setattr(moments.random, "random", lambda: 1.0)  # 不点赞，简化
    return hub


def test_seat_comments_with_its_own_persona_model_and_hub(db, monkeypatch):
    hub = _seat_reply_setup(monkeypatch)
    seen = []

    async def stream(messages, model_key, *a, **k):
        seen.append((messages, model_key))
        yield "下雨天最适合本少爷睡懒觉"
    monkeypatch.setattr(moments, "stream_ai", stream)
    monkeypatch.setattr(moments, "_parse_moment_reply_result",
                        lambda raw, expect_chat_decision: (raw, False, ""))
    asyncio.run(moments._ai_reply_to_moment("ai3", "mu"))
    messages, model_key = seen[0]
    text = "\n".join(str(m["content"]) for m in messages)
    assert model_key == "seat-m" and "本少爷人设" in text and "狗蛋喜欢雨天" in text
    assert "私聊里单独跟你说的私事" in text  # 朋友圈全家可见的提醒
    assert hub.await_args.args[3] == "private_group"
    assert _rows(db, "SELECT author, content FROM moment_comments") == [("ai3", "下雨天最适合本少爷睡懒觉")]


def test_seat_without_model_or_with_changed_line_does_not_comment(db, monkeypatch):
    _seat_reply_setup(monkeypatch, model="")
    stream = MagicMock()
    monkeypatch.setattr(moments, "stream_ai", stream)
    asyncio.run(moments._ai_reply_to_moment("ai3", "mu"))
    stream.assert_not_called()
    _seat_reply_setup(monkeypatch)
    import config

    async def ctx(who, limit=30):
        config.MODELS.pop("seat-m")  # 取上下文的时候线路被删了
        return []
    monkeypatch.setattr(moments, "_get_recent_context_messages", ctx)
    asyncio.run(moments._ai_reply_to_moment("ai3", "mu"))
    stream.assert_not_called()
    assert _rows(db, "SELECT COUNT(*) FROM moment_comments") == [(0,)]


def test_replying_to_a_seats_comment_wakes_that_seat(db, monkeypatch):
    async def add():
        async with aiosqlite.connect(db) as conn:
            await conn.execute("INSERT INTO moment_comments VALUES ('c3', 'mu', 'ai3', '睡懒觉', NULL, 2)")
            await conn.commit()
    asyncio.run(add())
    spawned = []
    monkeypatch.setattr(moments, "spawn_generation_task", lambda coro: (spawned.append(coro.cr_frame.f_locals), coro.close()))
    asyncio.run(moments.add_comment("mu", moments.CommentCreate(content="懒鬼", reply_to_id="c3")))
    assert spawned and spawned[0]["who"] == "ai3"


def test_seat_names_and_avatars_show_on_the_page(monkeypatch):
    monkeypatch.setattr(seat_chat.actors, "display_name", lambda a: {"ai3": "狗蛋"}.get(a, a))
    names = moments._name_map()
    assert names["ai3"] == "狗蛋" and {"user", "aion", "connor"} <= set(names)
    html = open("static/moments.html", encoding="utf-8").read()
    assert "/api/actors" in html and "avatarMap[author]" in html
