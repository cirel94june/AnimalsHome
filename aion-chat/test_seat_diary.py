"""④ 日记：狗蛋等座位和 Lucien 一样，她安静一阵、攒够新对话后用自己的人设和模型写日记。"""

import asyncio
import json
from unittest.mock import AsyncMock

import aiosqlite
import pytest

import diary
import seat_chat
import seat_diary


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "d.db"

    async def setup():
        async with aiosqlite.connect(path) as conn:
            await conn.execute("CREATE TABLE chatroom_rooms (id TEXT PRIMARY KEY, type TEXT, actor TEXT DEFAULT '')")
            await conn.execute("CREATE TABLE chatroom_messages (id TEXT, room_id TEXT, sender TEXT, content TEXT, "
                               "created_at REAL)")
            await conn.execute("CREATE TABLE chatroom_digest_anchors (room_id TEXT PRIMARY KEY, anchor_ts REAL NOT NULL DEFAULT 0)")
            for rid, kind, actor in (("g1", "group", ""), ("s3", "seat_1v1", "ai3"), ("s4", "seat_1v1", "ai4"),
                                     ("billiards_1", "group", "")):
                await conn.execute("INSERT INTO chatroom_rooms VALUES (?,?,?)", (rid, kind, actor))
            await conn.commit()
    asyncio.run(setup())
    monkeypatch.setattr(seat_diary, "get_db", lambda: aiosqlite.connect(path))
    import config
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "seat-m": {"provider": "custom_openai", "model": "m"}})
    monkeypatch.setattr(config, "load_worldbook", lambda: {"user_name": "小猫"})
    monkeypatch.setattr(seat_chat, "check_ready", lambda a: ({}, "seat-m"))
    monkeypatch.setattr(seat_chat, "_persona_text", lambda a: "本少爷人设")
    monkeypatch.setattr(seat_chat.actors, "display_name", lambda a: {"ai3": "狗蛋", "aion": "小克"}.get(a, a))
    saved = AsyncMock(return_value={"id": "d1"})
    monkeypatch.setattr(diary, "save_diary_entry", saved)
    moment = AsyncMock()
    monkeypatch.setattr(diary, "publish_ai_moment", moment)
    import gift
    present = AsyncMock()
    monkeypatch.setattr(gift, "send_gift_from_decision", present)
    import capabilities
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: True)
    seen = []
    reply = {"text": json.dumps({"diary": {"title": "今天", "content": "她又熬夜了", "mood": "担心"},
                                 "post_moment": True, "moment": {"content": "长" * 900, "expect_reply": True},
                                 "givegift": False, "gift": {}}, ensure_ascii=False)}

    async def stream(messages, model_key, *a, **k):
        seen.append((messages, model_key))
        yield reply["text"]
    import ai_providers
    monkeypatch.setattr(ai_providers, "stream_ai", stream)
    seat_diary._failures.clear()

    def say(room, sender, ts, n=1):
        async def add():
            async with aiosqlite.connect(path) as conn:
                for i in range(n):
                    await conn.execute("INSERT INTO chatroom_messages VALUES (?,?,?,?,?)",
                                       (f"{room}{ts}{i}", room, sender, f"{sender} 说的第 {i} 句", ts + i))
                await conn.commit()
        asyncio.run(add())

    def anchor():
        async def get():
            async with aiosqlite.connect(path) as conn:
                row = await (await conn.execute("SELECT anchor_ts FROM chatroom_digest_anchors")).fetchone()
                return row[0] if row else 0
        return asyncio.run(get())

    class Env:
        pass
    e = Env()
    e.say, e.anchor, e.saved, e.moment, e.present, e.seen, e.reply = say, anchor, saved, moment, present, seen, reply
    return e


def _run(now=100_000.0):
    return asyncio.run(seat_diary.maybe_write_seat_diary("ai3", now=now))


def test_writes_a_diary_in_its_own_voice_after_she_goes_quiet(env):
    env.say("s3", "user", 1000, 10)
    env.say("g1", "aion", 2000, 10)
    env.say("s4", "user", 3000, 30)  # 别的座位私聊不算
    assert _run() == "written"
    messages, model_key = env.seen[0]
    assert model_key == "seat-m" and "本少爷人设" in messages[0]["content"]
    assert "【小猫】" in messages[-1]["content"] and "【小克】" in messages[-1]["content"]
    assert "s4" not in messages[-1]["content"]
    kwargs = env.saved.await_args.kwargs
    assert kwargs["author"] == "ai3" and kwargs["content"] == "她又熬夜了" and kwargs["source_type"] == "seat_digest"
    assert env.anchor() == 2009
    moment = env.moment.await_args.kwargs
    assert moment["author"] == "ai3" and len(moment["content"]) == 800 and moment["expect_reply"] is True
    assert env.present.await_args.kwargs == {"sender": "ai3"}
    assert _run() == "skip_too_few"  # 写过的不再写


@pytest.mark.parametrize("setup, expected", [
    (lambda e: e.say("s3", "user", 1000, 5), "skip_too_few"),
    (lambda e: e.say("g1", "aion", 1000, 25), "skip_no_user"),
    (lambda e: (e.say("g1", "aion", 1000, 20), e.say("s3", "user", 99_000)), "skip_not_quiet"),
    (lambda e: e.say("billiards_1", "user", 1000, 30), "skip_too_few"),
])
def test_waits_until_there_is_enough_and_she_is_quiet(env, setup, expected):
    setup(env)
    assert _run() == expected
    assert not env.seen


def test_not_ready_or_reselected_model_writes_nothing(env, monkeypatch):
    env.say("s3", "user", 1000, 25)
    monkeypatch.setattr(seat_chat, "check_ready", lambda a: (_ for _ in ()).throw(seat_chat.SeatNotReady("off")))
    assert _run() == "skip_not_ready"
    calls = iter([({}, "seat-m"), ({}, "other-m")])  # 开始时 seat-m，开口前改成了 other-m
    monkeypatch.setattr(seat_chat, "check_ready", lambda a: next(calls))
    assert _run() == "skip_not_ready"
    assert not env.seen and env.anchor() == 0


def test_moment_switch_off_keeps_the_diary_only(env, monkeypatch):
    import capabilities
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: name != "moment")
    env.say("s3", "user", 1000, 25)
    assert _run() == "written"
    env.saved.assert_awaited_once()
    env.moment.assert_not_called()


def test_unparseable_reply_retries_then_skips_that_stretch(env):
    env.reply["text"] = "我不想写"
    env.say("s3", "user", 1000, 25)
    assert [_run(), _run()] == ["failed", "failed"] and env.anchor() == 0
    assert _run() == "failed" and env.anchor() == 1024  # 第三次还不行就跳过这一段
    env.saved.assert_not_called()


def test_seat_diary_tts_says_voice_is_not_set_yet(monkeypatch):
    import routes.diary as diary_routes
    monkeypatch.setattr(diary_routes, "_load_diary", AsyncMock(return_value={"id": "d", "author": "ai3"}))
    import actors
    monkeypatch.setattr(actors, "display_name", lambda a: "狗蛋")
    resp = asyncio.run(diary_routes.synthesize_diary_tts("d"))
    assert resp.status_code == 400 and "狗蛋" in resp.body.decode()
