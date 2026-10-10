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

    messages = asyncio.run(seat_chat.build_context("ai3", [{"sender": "user", "content": "我回来了", "created_at": 1}]))
    joined = "\n".join(m["content"] for m in messages)
    assert "我是 Jasper" in joined and "喜欢猫" in joined
    assert "上次聊到一半的电影" in joined and "最近在搬家" in joined
    ctx.assert_awaited_once_with("ai3", "我回来了")
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
