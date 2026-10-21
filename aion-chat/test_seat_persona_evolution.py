"""⑤ 人格成长：狗蛋等座位和小克 / Lucien 一样，读她的点赞 / 点踩理由和自己的日记，改写自己的人设分区。"""

import asyncio
import json
from unittest.mock import AsyncMock

import aiosqlite
import pytest

import actors
import persona_evolution as pe
import seat_chat


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "e.db"

    async def setup():
        async with aiosqlite.connect(path) as conn:
            await conn.execute("CREATE TABLE chatroom_rooms (id TEXT PRIMARY KEY, type TEXT, actor TEXT DEFAULT '', title TEXT)")
            await conn.execute(
                "CREATE TABLE chatroom_messages (id TEXT, room_id TEXT, sender TEXT, content TEXT, created_at REAL, "
                "ai_feedback_rating TEXT, ai_feedback_reason TEXT, ai_feedback_created_at REAL, ai_feedback_updated_at REAL)")
            await conn.execute("CREATE TABLE diary_entries (id TEXT, author TEXT, title TEXT, content TEXT, mood TEXT, "
                               "source_type TEXT, source_ref TEXT, source_start_ts REAL, source_end_ts REAL, created_at REAL)")
            await conn.execute("""CREATE TABLE persona_evolution_runs (
                id TEXT PRIMARY KEY, actor TEXT NOT NULL DEFAULT 'main_ai', trigger TEXT NOT NULL DEFAULT 'manual',
                status TEXT NOT NULL DEFAULT 'draft', model TEXT DEFAULT '', window_start_ts REAL NOT NULL,
                window_end_ts REAL NOT NULL, window_label TEXT DEFAULT '', feedback_count INTEGER NOT NULL DEFAULT 0,
                summary TEXT DEFAULT '', patch_json TEXT DEFAULT '{}', user_message TEXT DEFAULT '',
                raw_response TEXT DEFAULT '', error TEXT DEFAULT '', auto_applied INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, applied_at REAL,
                before_json TEXT DEFAULT '{}', after_json TEXT DEFAULT '{}', diff_json TEXT DEFAULT '[]',
                feedback_fingerprint TEXT DEFAULT '', daily_memory_json TEXT DEFAULT '[]')""")
            await conn.execute("""CREATE TABLE persona_evolution_items (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, source TEXT NOT NULL, message_id TEXT NOT NULL,
                conv_id TEXT DEFAULT '', room_id TEXT DEFAULT '', room_title TEXT DEFAULT '', speaker TEXT DEFAULT '',
                rating TEXT NOT NULL, reason TEXT DEFAULT '', message_content TEXT DEFAULT '', context_json TEXT DEFAULT '[]',
                message_created_at REAL, feedback_updated_at REAL, created_at REAL NOT NULL)""")
            for rid, kind, actor in (("g1", "group", ""), ("s3", "seat_1v1", "ai3"), ("s4", "seat_1v1", "ai4")):
                await conn.execute("INSERT INTO chatroom_rooms VALUES (?,?,?,?)", (rid, kind, actor, rid))
            await conn.commit()
    asyncio.run(setup())
    monkeypatch.setattr(pe, "get_db", lambda: aiosqlite.connect(path))
    monkeypatch.setattr(actors, "ACTORS_PATH", tmp_path / "actors.json")
    (tmp_path / "actors.json").write_text(json.dumps({"seats": {"ai3": {
        "enabled": True, "name": "狗蛋",
        "persona_sections": {"identity_core": "我是狗蛋", "relationship_core": "小猫的家人",
                             "communication_style": "说话很冲"}}}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(actors, "display_name", lambda a: {"ai3": "狗蛋"}.get(a, a))
    monkeypatch.setattr(pe, "_chatroom_names", lambda: ("小猫", "小克", "Lucien"))
    monkeypatch.setattr(pe, "load_worldbook", lambda: {
        "user_name": "小猫", "ai_name": "小克", "ai_persona": "小克的人设原文",
        "ai_persona_sections": {"communication_style": "小克的说话方式"},
        "persona_section_locks": {"ai": {"communication_style": "locked"}}})
    saved_wb = []
    monkeypatch.setattr(pe, "save_worldbook", saved_wb.append)
    monkeypatch.setattr(seat_chat, "check_ready", lambda a: ({}, "seat-m"))

    def add_msg(mid, room, sender, ts, rating=None, reason=None):
        async def go():
            async with aiosqlite.connect(path) as conn:
                await conn.execute("INSERT INTO chatroom_messages VALUES (?,?,?,?,?,?,?,?,?)",
                                   (mid, room, sender, f"{sender} 在 {room} 说的话", ts, rating, reason, ts, ts))
                await conn.commit()
        asyncio.run(go())
    return {"add": add_msg, "saved_wb": saved_wb, "path": path}


def test_seat_feedback_covers_group_and_own_private_only(env):
    env["add"]("u1", "s3", "user", 90)
    env["add"]("m1", "g1", "ai3", 100, "like", "这句很暖")
    env["add"]("m2", "s3", "ai3", 110, "dislike", "太冲了")
    env["add"]("m3", "s4", "ai3", 120, "dislike", "别的座位私聊")  # 不该出现
    env["add"]("m4", "g1", "ai3", 130)  # 没评分
    env["add"]("m5", "g1", "aion", 140, "like", "小克的")
    items = asyncio.run(pe.fetch_actor_feedback("ai3", 0, 1000))
    assert [(i["message_id"], i["source"], i["reason"]) for i in items] == [
        ("m1", "group", "这句很暖"), ("m2", "seat_private", "太冲了")]
    assert items[1]["context"][0]["text"] == "user 在 s3 说的话"


def test_seat_review_rewrites_its_own_sections_not_aion(env, monkeypatch):
    import time
    env["add"]("m2", "s3", "ai3", time.time() - 60, "dislike", "太冲了")
    prompts = []

    async def model(messages, model_key, **kw):
        prompts.append((messages, model_key))
        return json.dumps({
            "summary": "我说话太冲了", "should_update": True, "user_message": "我会温柔一点",
            "section_updates": [
                {"section": "communication_style", "content": "说话直接但温柔", "reason": "她说太冲"},
                {"section": "identity_core", "content": "我不是狗蛋了", "reason": "试图改核心身份"},
            ]}, ensure_ascii=False)
    monkeypatch.setattr(pe, "simple_ai_call", model)
    result = asyncio.run(pe.run_persona_evolution("ai3", trigger="manual", window_mode="current"))
    messages, model_key = prompts[0]
    assert model_key == "seat-m"
    whole = json.dumps(messages, ensure_ascii=False)
    assert "说话很冲" in whole and "小克的说话方式" not in whole and "小克的人设原文" not in whole
    sections = actors.persona_sections("ai3")
    assert sections["communication_style"] == "说话直接但温柔"  # 小克锁住的分区不影响座位
    assert sections["identity_core"] == "我是狗蛋"  # 核心身份始终锁定
    assert env["saved_wb"] == []  # 没碰小克的世界书
    assert result.get("status") in ("applied", "reviewed")


def test_seat_model_must_still_be_selected(env, monkeypatch):
    monkeypatch.setattr(seat_chat, "check_ready", lambda a: ({}, "other-m"))
    with pytest.raises(RuntimeError):
        asyncio.run(pe._call_actor_model("ai3", [{"role": "user", "content": "x"}], "seat-m"))
    monkeypatch.setattr(seat_chat, "check_ready", lambda a: (_ for _ in ()).throw(seat_chat.SeatNotReady("off")))
    with pytest.raises(seat_chat.SeatNotReady):
        asyncio.run(pe._actor_model("ai3"))


def test_seat_switch_and_routes(env):
    from routes import persona_evolution as routes
    assert asyncio.run(routes.get_seat_config("ai3")) == {"enabled": False}
    asyncio.run(routes.update_seat_config("ai3", routes.ConnorConfigUpdate(enabled=True)))
    assert pe._actor_auto_enabled("ai3") is True and actors.persona_evolution_enabled("ai3")
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        asyncio.run(routes.get_seat_config("aion"))


def test_seat_messages_can_be_rated(monkeypatch, tmp_path):
    import routes.chatroom as chatroom_routes
    path = tmp_path / "r.db"

    async def setup():
        async with aiosqlite.connect(path) as conn:
            await conn.execute("CREATE TABLE chatroom_rooms (id TEXT PRIMARY KEY, type TEXT, actor TEXT DEFAULT '', title TEXT)")
            await conn.execute(
                "CREATE TABLE chatroom_messages (id TEXT, room_id TEXT, sender TEXT, content TEXT, created_at REAL, "
                "ai_feedback_rating TEXT, ai_feedback_reason TEXT, ai_feedback_created_at REAL, ai_feedback_updated_at REAL)")
            for rid, kind, actor in (("g1", "group", ""), ("s3", "seat_1v1", "ai3")):
                await conn.execute("INSERT INTO chatroom_rooms VALUES (?,?,?,?)", (rid, kind, actor, rid))
            for mid, room, sender in (("a", "g1", "ai3"), ("b", "s3", "ai3"), ("c", "s3", "user")):
                await conn.execute("INSERT INTO chatroom_messages (id, room_id, sender, content, created_at) "
                                   "VALUES (?,?,?,?,1)", (mid, room, sender, "x"))
            await conn.commit()
    asyncio.run(setup())
    monkeypatch.setattr(chatroom_routes, "get_db", lambda: aiosqlite.connect(path))
    monkeypatch.setattr(chatroom_routes.manager, "broadcast", AsyncMock())
    body = chatroom_routes.MessageFeedbackUpdate(rating="like", reason="喜欢")
    for mid in ("a", "b"):
        asyncio.run(chatroom_routes.update_message_feedback(mid, body))
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        asyncio.run(chatroom_routes.update_message_feedback("c", body))
