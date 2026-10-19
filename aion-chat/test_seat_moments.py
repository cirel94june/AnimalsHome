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
    state = {"ready": bool(model), "model": model}

    def check_ready(actor):
        if not state["ready"]:
            raise seat_chat.SeatNotReady("停用了")
        return {"name": "狗蛋"}, state["model"]
    monkeypatch.setattr(seat_chat, "check_ready", check_ready)
    monkeypatch.setattr(seat_chat, "_persona_text", lambda a: "本少爷人设")
    monkeypatch.setattr(seat_chat, "merged_messages", AsyncMock(return_value=[
        {"sender": "user", "content": "私聊里的心事", "created_at": 1, "source": "private"}]))
    import memory_hub_bridge
    hub = AsyncMock(return_value="【记忆】狗蛋喜欢雨天")
    monkeypatch.setattr(memory_hub_bridge, "context_block", hub)
    monkeypatch.setattr(moments.random, "random", lambda: 1.0)  # 不点赞，简化
    hub.state = state
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
    assert model_key == "seat-m" and "本少爷人设" in text
    assert "亲密关系" in text and "自己判断" in text  # 朋友圈全家可见的提醒（IB 口径）
    assert hub.await_args.args[3] == "private_group" and "狗蛋喜欢雨天" in text  # 小猫定：可以拿得到
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


def test_disabled_seat_is_not_woken_by_an_old_comment(db, monkeypatch):
    """Codex M1：停用 / 没人设的座位，回复它以前的评论也不会让它说话；等待期间被停用同样不回。"""
    hub = _seat_reply_setup(monkeypatch)
    stream = MagicMock()
    monkeypatch.setattr(moments, "stream_ai", stream)
    hub.state["ready"] = False
    asyncio.run(moments._ai_reply_to_moment("ai3", "mu", None))
    stream.assert_not_called()
    hub.state["ready"] = True

    async def ctx(who, limit=30):
        hub.state["ready"] = False  # 取上下文的时候被停用了
        return []
    monkeypatch.setattr(moments, "_get_recent_context_messages", ctx)
    asyncio.run(moments._ai_reply_to_moment("ai3", "mu"))
    stream.assert_not_called()

    async def ctx2(who, limit=30):
        hub.state.update(ready=True, model="other-m")  # 等待期间换了模型
        return []
    hub.state["ready"] = True
    monkeypatch.setattr(moments, "_get_recent_context_messages", ctx2)
    asyncio.run(moments._ai_reply_to_moment("ai3", "mu"))
    stream.assert_not_called()
    assert _rows(db, "SELECT COUNT(*) FROM moment_comments") == [(0,)]


def test_ib_style_limits(db, monkeypatch):
    """参考 IB 机的限额：AI 评论最多 300 字；一条动态满 20 条评论后 AI 不再评论。"""
    _seat_reply_setup(monkeypatch)

    async def stream(messages, model_key, *a, **k):
        yield "长" * 500
    monkeypatch.setattr(moments, "stream_ai", stream)
    monkeypatch.setattr(moments, "_parse_moment_reply_result", lambda raw, expect_chat_decision: (raw, False, ""))
    asyncio.run(moments._ai_reply_to_moment("ai3", "mu"))
    assert [len(r[0]) for r in _rows(db, "SELECT content FROM moment_comments")] == [moments.MAX_AI_COMMENT_CHARS]

    async def fill():
        async with aiosqlite.connect(db) as conn:
            for i in range(moments.MAX_COMMENTS_PER_MOMENT):
                await conn.execute("INSERT INTO moment_comments VALUES (?, 'mu', 'user', 'x', NULL, ?)", (f"f{i}", 10 + i))
            await conn.commit()
    asyncio.run(fill())
    stream2 = MagicMock()
    monkeypatch.setattr(moments, "stream_ai", stream2)
    asyncio.run(moments._ai_reply_to_moment("ai3", "mu"))
    stream2.assert_not_called()


def test_one_moment_per_reply_and_800_chars(db, monkeypatch):
    monkeypatch.setattr(chatroom_routes, "is_capability_enabled", lambda name: True)
    monkeypatch.setattr(chatroom_routes, "_chatroom_sys_msg", AsyncMock(return_value={"id": "sys"}))
    monkeypatch.setattr(chatroom_routes, "_name_for_identity", lambda a: "狗蛋")
    import ws
    monkeypatch.setattr(ws.manager, "broadcast", AsyncMock())
    text = "[MOMENT:" + "长" * 900 + "|false][MOMENT:第二条|false]"
    asyncio.run(chatroom_routes._process_seat_commands(text, "g1", "ai3", "m1", asyncio.Queue()))
    rows = _rows(db, "SELECT content FROM moments WHERE author='ai3'")
    assert len(rows) == 1 and len(rows[0][0]) == 800
    import inspect
    import routes.chat as chat_routes
    src = inspect.getsource(chat_routes)
    assert src.count("moment_matches[:1]") == 3 and src.count("mt_content.strip()[:800]") == 3


def test_comment_cap_holds_when_slots_fill_up_during_generation(db, monkeypatch):
    """Codex R1：生成期间评论被补到 20 条，或两个 AI 同时抢最后一个名额，都不会出现第 21 条。"""
    _seat_reply_setup(monkeypatch)
    monkeypatch.setattr(moments, "_parse_moment_reply_result", lambda raw, expect_chat_decision: (raw, False, ""))

    async def fill(n):
        async with aiosqlite.connect(db) as conn:
            for i in range(n):
                await conn.execute("INSERT INTO moment_comments VALUES (?, 'mu', 'user', 'x', NULL, ?)", (f"u{i}_{n}", 10 + i))
            await conn.commit()
    asyncio.run(fill(19))

    async def stream_fills_last_slot(messages, model_key, *a, **k):
        async with aiosqlite.connect(db) as conn:  # 生成期间用户补上第 20 条
            await conn.execute("INSERT INTO moment_comments VALUES ('late', 'mu', 'user', 'y', NULL, 99)")
            await conn.commit()
        yield "我也来"
    monkeypatch.setattr(moments, "stream_ai", stream_fills_last_slot)
    asyncio.run(moments._ai_reply_to_moment("ai3", "mu"))
    assert _rows(db, "SELECT COUNT(*) FROM moment_comments WHERE moment_id='mu'") == [(20,)]

    async def clear_one():
        async with aiosqlite.connect(db) as conn:
            await conn.execute("DELETE FROM moment_comments WHERE id='late'")
            await conn.commit()
    asyncio.run(clear_one())  # 回到 19 条，两个 AI 同时抢最后一个

    async def stream(messages, model_key, *a, **k):
        await asyncio.sleep(0)
        yield "抢一个"
    monkeypatch.setattr(moments, "stream_ai", stream)

    async def both():
        await asyncio.gather(moments._ai_reply_to_moment("ai3", "mu"), moments._ai_reply_to_moment("ai3", "mu"))
    asyncio.run(both())
    assert _rows(db, "SELECT COUNT(*) FROM moment_comments WHERE moment_id='mu'") == [(20,)]
