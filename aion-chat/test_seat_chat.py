import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import actors
import memory_hub_bridge
import seat_chat
import routes.chatroom as chatroom_routes

JASPER_SECTIONS = {"identity_core": "我是 Jasper，说话慢一点。", "communication_style": "短句"}


@pytest.fixture
def seats(tmp_path, monkeypatch):
    monkeypatch.setattr(actors, "ACTORS_PATH", tmp_path / "actors.json")
    monkeypatch.setattr(actors, "load_worldbook", lambda: {"ai_name": "小克", "user_name": "Ceci"})
    monkeypatch.setattr(actors, "_connor_name", lambda: "Lucien")
    monkeypatch.setitem(seat_chat.MODELS, "gemini-relay", {"provider": "custom_openai", "model": "gemini-3-pro"})
    actors._save_raw({"seats": {"ai3": {"persona_sections": JASPER_SECTIONS, "model": "gemini-relay"}}})
    return tmp_path


def test_ready_requires_persona_and_own_model(seats):
    actor, model = seat_chat.check_ready("ai3")
    assert actor["name"] == "Jasper" and model == "gemini-relay"
    with pytest.raises(seat_chat.SeatNotReady, match="启用"):
        seat_chat.check_ready("ai4")
    with pytest.raises(seat_chat.SeatNotReady):
        seat_chat.check_ready("connor")  # 前两位不走座位私聊

    data = actors._load_raw()
    data["seats"]["ai3"]["model"] = "已删除的线路"
    actors._save_raw(data)
    assert seat_chat.seat_model("ai3") == ""  # 不存在的模型不能悄悄换成默认模型
    with pytest.raises(seat_chat.SeatNotReady, match="选模型"):
        seat_chat.check_ready("ai3")

    data["seats"]["ai3"]["model"] = "gemini-relay"
    data["seats"]["ai3"]["persona_sections"] = {}
    actors._save_raw(data)
    with pytest.raises(seat_chat.SeatNotReady, match="人设"):
        seat_chat.check_ready("ai3")


def test_history_keeps_only_user_and_this_seat():
    msgs = [
        {"sender": "ai3", "content": "开场白", "created_at": 1},
        {"sender": "user", "content": "在吗", "created_at": 2},
        {"sender": "ai3", "content": "在", "created_at": 3},
        {"sender": "system", "content": "提示", "created_at": 4},
        {"sender": "ai3", "content": "[失败]", "attachments": [{"type": "chatroom_reply_failure"}], "created_at": 5},
        {"sender": "user", "content": "", "attachments": ["/a.png"], "created_at": 6},
    ]
    history = seat_chat._history("ai3", msgs, 30)
    assert [m["role"] for m in history] == ["user", "assistant", "user"]
    assert history[0]["content"].startswith("在吗") and history[2]["content"].startswith("[图片]")


def test_context_has_persona_user_info_and_hub_memory(seats, monkeypatch):
    monkeypatch.setattr(seat_chat, "load_worldbook", lambda: {"user_name": "Ceci", "user_persona": "喜欢猫"})
    ctx = AsyncMock(return_value="[交接卡]\n上次聊到一半的电影\n\n[跨端记忆]\n最近在搬家")
    monkeypatch.setattr(memory_hub_bridge, "context_block", ctx)

    messages = asyncio.run(seat_chat.build_context("ai3", [{"sender": "user", "content": "我回来了", "created_at": 1}],
                                                  chat_id="cr_1_ai3"))
    joined = "\n".join(m["content"] for m in messages)
    assert "我是 Jasper" in joined and "喜欢猫" in joined
    assert "上次聊到一半的电影" in joined and "最近在搬家" in joined
    ctx.assert_awaited_once_with("ai3", "我回来了", "cr_1_ai3", "private")
    assert messages[-1]["role"] == "user" and messages[-1]["content"].startswith("我回来了")


@asynccontextmanager
async def _memory_db():
    async with aiosqlite.connect(":memory:") as db:
        await db.executescript("""
            CREATE TABLE chatroom_rooms (id TEXT PRIMARY KEY, title TEXT, type TEXT, aion_persona TEXT,
                connor_persona TEXT, context_minutes INTEGER DEFAULT 30, ai_chat_rounds INTEGER DEFAULT 3,
                actor TEXT DEFAULT '', created_at REAL, updated_at REAL);
        """)

        @asynccontextmanager
        async def get_db():
            yield db

        yield get_db


def test_create_seat_room_once_per_seat(seats, monkeypatch):
    async def run():
        async with _memory_db() as get_db:
            with patch.object(chatroom_routes, "get_db", get_db), \
                    patch.object(chatroom_routes.manager, "broadcast", new=AsyncMock()):
                body = chatroom_routes.RoomCreate(title="", type="seat_1v1", actor="ai3")
                first = await chatroom_routes.create_room(body)
                assert first["title"] == "和 Jasper 私聊" and first["actor"] == "ai3"
                again = await chatroom_routes.create_room(body)
                assert again["id"] == first["id"]
                with pytest.raises(chatroom_routes.HTTPException):
                    await chatroom_routes.create_room(chatroom_routes.RoomCreate(type="seat_1v1", actor="ai4"))
                with pytest.raises(chatroom_routes.HTTPException):
                    await chatroom_routes.create_room(chatroom_routes.RoomCreate(type="weird"))

    asyncio.run(run())


def test_new_window_opens_another_room_for_the_same_seat(seats, monkeypatch):
    """2026-10-24 小猫：每位 AI 的私聊都能开多个窗口。不带 new_window 回到最近用的那个。"""
    async def run():
        async with _memory_db() as get_db:
            with patch.object(chatroom_routes, "get_db", get_db),                     patch.object(chatroom_routes.manager, "broadcast", new=AsyncMock()):
                first = await chatroom_routes.create_room(chatroom_routes.RoomCreate(title="", type="seat_1v1", actor="ai3"))
                second = await chatroom_routes.create_room(
                    chatroom_routes.RoomCreate(title="周末计划", type="seat_1v1", actor="ai3", new_window=True))
                assert second["id"] != first["id"] and second["title"] == "周末计划" and second["actor"] == "ai3"
                async with get_db() as db:
                    await db.execute("UPDATE chatroom_rooms SET updated_at=? WHERE id=?", (9e12, second["id"]))
                again = await chatroom_routes.create_room(chatroom_routes.RoomCreate(title="", type="seat_1v1", actor="ai3"))
                assert again["id"] == second["id"]  # 最近用的那个

    asyncio.run(run())


def test_init_db_keeps_multiple_seat_windows(tmp_path, monkeypatch):
    """重启不再把同一座位的多个窗口合并，也不再有「一个座位一个房间」的唯一索引。"""
    import sqlite3
    import database
    path = tmp_path / "chat.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE chatroom_rooms (id TEXT PRIMARY KEY, title TEXT NOT NULL, type TEXT NOT NULL DEFAULT 'group',
                aion_persona TEXT DEFAULT '', connor_persona TEXT DEFAULT '', context_minutes INTEGER DEFAULT 30,
                ai_chat_rounds INTEGER DEFAULT 3, created_at REAL NOT NULL, updated_at REAL NOT NULL, actor TEXT DEFAULT '');
            CREATE UNIQUE INDEX idx_chatroom_seat_room ON chatroom_rooms(actor) WHERE type='seat_1v1';
            CREATE TABLE chatroom_messages (id TEXT PRIMARY KEY, room_id TEXT NOT NULL, sender TEXT NOT NULL,
                content TEXT NOT NULL, attachments TEXT DEFAULT '[]', created_at REAL NOT NULL);
            INSERT INTO chatroom_rooms (id,title,type,actor,created_at,updated_at) VALUES ('r1','和 Jasper 私聊','seat_1v1','ai3',1,1);
            INSERT INTO chatroom_messages (id,room_id,sender,content,created_at) VALUES ('m1','r1','user','a',1);
        """)
    monkeypatch.setattr(database, "DB_PATH", path)
    asyncio.run(database.init_db())
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO chatroom_rooms (id,title,type,actor,created_at,updated_at) VALUES ('r2','x','seat_1v1','ai3',2,2)")
        conn.commit()
    asyncio.run(database.init_db())
    with sqlite3.connect(path) as conn:
        assert sorted(r[0] for r in conn.execute("SELECT id FROM chatroom_rooms WHERE type='seat_1v1'")) == ["r1", "r2"]
        assert conn.execute("SELECT room_id FROM chatroom_messages").fetchall() == [("r1",)]


def _stream(*chunks):
    async def gen(messages, model_key, meta=None, *args, **kwargs):
        assert model_key == "gemini-relay"
        for chunk in chunks:
            yield chunk
    return gen


def test_seat_reply_streams_saves_and_captures_as_seat(seats, monkeypatch):
    saved = {}

    async def save_msg(room_id, sender, content, msg_id=None, attachments=None, **kw):
        saved.update(room_id=room_id, sender=sender, content=content, id=msg_id)
        return dict(saved)

    capture = MagicMock()
    monkeypatch.setattr(seat_chat, "build_context", AsyncMock(return_value=[{"role": "user", "content": "x"}]))
    monkeypatch.setattr(chatroom_routes, "stream_ai", _stream("好呀，", "我在。[NOTE:记得喝水]"))
    monkeypatch.setattr(chatroom_routes, "_save_msg", save_msg)
    monkeypatch.setattr(chatroom_routes, "process_note_commands", AsyncMock(side_effect=lambda t, a: t.replace("[NOTE:记得喝水]", "")))
    monkeypatch.setattr(memory_hub_bridge, "schedule_capture", capture)

    async def run():
        q = asyncio.Queue()
        msgs = [{"sender": "user", "content": "在吗", "created_at": 1}]
        await chatroom_routes._generate_seat_reply("room", {"actor": "ai3"}, msgs, q, 30)
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        return events

    events = asyncio.run(run())
    assert [e["type"] for e in events][0] == "seat_start" and events[-1]["type"] == "seat_done"
    assert all(e.get("actor") == "ai3" for e in events)
    assert saved["sender"] == "ai3" and saved["content"] == "好呀，我在。"
    capture.assert_called_once_with("ai3", "在吗", "好呀，我在。")


def test_seat_reply_without_model_explains_instead_of_switching(seats, monkeypatch):
    data = actors._load_raw()
    data["seats"]["ai3"]["model"] = ""
    actors._save_raw(data)
    failure = AsyncMock()
    stream = MagicMock()
    monkeypatch.setattr(chatroom_routes, "_save_chatroom_generation_failure", failure)
    monkeypatch.setattr(chatroom_routes, "stream_ai", stream)
    asyncio.run(chatroom_routes._generate_seat_reply("room", {"actor": "ai3"}, [], asyncio.Queue(), 30))
    stream.assert_not_called()
    assert "选模型" in str(failure.await_args.args[2])


def test_seat_reply_stops_if_line_removed_while_reading_memory(seats, monkeypatch):
    async def context_then_delete(actor, msgs, **kw):  # 取 Hub 记忆的那几秒里线路被删
        seat_chat.MODELS.pop("gemini-relay", None)
        return [{"role": "user", "content": "x"}]

    stream = MagicMock()
    failure = AsyncMock()
    monkeypatch.setattr(seat_chat, "build_context", context_then_delete)
    monkeypatch.setattr(chatroom_routes, "stream_ai", stream)
    monkeypatch.setattr(chatroom_routes, "_save_chatroom_reply_failure", failure)
    asyncio.run(chatroom_routes._generate_seat_reply("room", {"actor": "ai3"}, [], asyncio.Queue(), 30))
    stream.assert_not_called()  # 没有换成默认模型去生成
    assert "改动" in failure.await_args.args[3]


def test_seat_reply_provider_error_is_saved_as_seat_failure(seats, monkeypatch):
    async def broken(messages, model_key, meta=None, *a, **kw):
        meta["provider_error"] = "HTTP 401"
        yield "[错误]"

    failure = AsyncMock()
    capture = MagicMock()
    monkeypatch.setattr(seat_chat, "build_context", AsyncMock(return_value=[]))
    monkeypatch.setattr(chatroom_routes, "stream_ai", broken)
    monkeypatch.setattr(chatroom_routes, "_save_chatroom_reply_failure", failure)
    monkeypatch.setattr(memory_hub_bridge, "schedule_capture", capture)
    asyncio.run(chatroom_routes._generate_seat_reply("room", {"actor": "ai3"}, [], asyncio.Queue(), 30))
    assert failure.await_args.args[1] == "ai3" and "401" in failure.await_args.args[3]
    capture.assert_not_called()


def test_actor_model_must_exist(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import config

    monkeypatch.setattr(actors, "ACTORS_PATH", tmp_path / "actors.json")
    monkeypatch.setattr(actors, "load_worldbook", lambda: {})
    monkeypatch.setattr(actors, "_connor_name", lambda: "Lucien")
    monkeypatch.setitem(config.MODELS, "gemini-relay", {"provider": "custom_openai", "model": "g"})
    app = FastAPI()
    app.include_router(actors.router)
    client = TestClient(app)
    assert client.put("/api/actors/ai3", json={"model": "不存在"}).status_code == 400
    assert client.put("/api/actors/ai3", json={"model": "gemini-relay"}).json()["model"] == "gemini-relay"
    assert client.put("/api/actors/ai3", json={"model": ""}).json()["model"] == ""


def test_group_history_names_every_speaker_and_merges_turns(seats):
    import actors
    msgs = [
        {"sender": "user", "content": "今晚吃什么", "created_at": 1},
        {"sender": "aion", "content": "火锅", "created_at": 2},
        {"sender": "connor", "content": "烧烤", "created_at": 3},
        {"sender": "ai3", "content": "本少爷要吃火锅", "created_at": 4},
        {"sender": "user", "content": "那就火锅", "created_at": 5},
    ]
    hist = seat_chat._history("ai3", msgs, 30, group=True, user_name="小猫")
    assert [h["role"] for h in hist] == ["user", "assistant", "user"]
    first = hist[0]["content"]
    assert "【小猫】今晚吃什么" in first and "火锅" in first and "烧烤" in first
    assert "【" + actors.display_name("aion") + "】" in first
    assert hist[1]["content"] == "本少爷要吃火锅"
    # 私聊照旧：只有小猫和自己
    private = seat_chat._history("ai3", msgs, 30)
    assert all("【" not in h["content"] for h in private)
    assert [h["role"] for h in private] == ["user", "assistant", "user"]
