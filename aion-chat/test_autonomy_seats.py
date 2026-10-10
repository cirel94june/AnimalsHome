import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import actors
import autonomy
import autonomy_state
import chatroom
import config
import mcp_client
import mcp_outings


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(autonomy_state, "DB_PATH", tmp_path / "chat.db")
    monkeypatch.setattr(actors, "ACTORS_PATH", tmp_path / "actors.json")
    monkeypatch.setattr(actors, "load_worldbook", lambda: {"ai_name": "小克"})
    monkeypatch.setattr(actors, "_connor_name", lambda: "Lucien")
    monkeypatch.setattr(chatroom, "get_chatroom_names", lambda: ("Ceci", "小克", "Lucien"))
    monkeypatch.setattr(mcp_client, "MCP_SERVERS_PATH", tmp_path / "mcp_servers.json")
    (tmp_path / "mcp_servers.json").write_text(json.dumps({"servers": []}), encoding="utf-8")
    monkeypatch.setitem(config.MODELS, "relay-gemini", {"provider": "custom_openai", "model": "g",
                                                        "base_url": "https://relay.example/v1", "api_key": "k"})
    actors._save_raw({"seats": {"ai3": {"model": "relay-gemini", "persona_sections": {"identity_core": "Jasper"}}}})
    return tmp_path


def test_status_lists_enabled_seats_only(env):
    roles = asyncio.run(autonomy_state.autonomy_status_payload())["roles"]
    assert [(r["actor"], r["name"]) for r in roles] == [("aion", "小克"), ("connor", "Lucien"), ("ai3", "Jasper")]
    assert roles[2]["config"]["enabled"] is False  # 新座位默认不自己醒来
    assert "mcp_outing" in roles[2]["config"]["actions"]


def _offered_actions(env, monkeypatch, actor):
    seen = {}

    async def fake_ask(actor_id, instruction, **kw):
        seen["instruction"] = instruction
        return {"action": "rest", "reason": "测试"}

    monkeypatch.setattr(autonomy, "_ask_actor_json", fake_ask)
    monkeypatch.setattr(autonomy, "recent_niche_index", AsyncMock(return_value=[]))
    monkeypatch.setattr(autonomy, "_has_active_user_wishes", AsyncMock(return_value=True))
    monkeypatch.setattr(autonomy, "_is_idle_web_roam_available", lambda: True)
    asyncio.run(autonomy._select_action(actor, manual=True))
    return {line.split(":")[0][2:] for line in seen["instruction"].splitlines() if line.startswith("- ")}


def test_seat_only_gets_seat_safe_actions_and_outing_when_possible(env, monkeypatch):
    offered = _offered_actions(env, monkeypatch, "ai3")
    assert offered == {"rest", "private_chat", "web_roam", "wish_pool"}  # 还没有可去的地方

    mcp_outings.save_tool({"name": "小论坛", "url": "https://forum.example/mcp", "autonomy": True})
    assert "mcp_outing" in _offered_actions(env, monkeypatch, "ai3")

    actors._save_raw({"seats": {"ai3": {"model": "", "persona_sections": {"identity_core": "Jasper"}}}})
    assert "mcp_outing" not in _offered_actions(env, monkeypatch, "ai3")  # 没有能调用工具的模型就不提供


def test_disabled_seat_never_wakes(env):
    actors._save_raw({"seats": {"ai3": {"enabled": False}}})
    result = asyncio.run(autonomy.IdleAutonomyManager().run_actor_once("ai3", manual=True))
    assert result["skipped"] == "seat disabled"


def test_outing_prefers_places_not_visited_recently(env, monkeypatch):
    for name in ("论坛", "游戏"):
        mcp_outings.save_tool({"name": name, "url": f"https://{name}.example/mcp".replace("论坛", "a").replace("游戏", "b"),
                               "autonomy": True})

    async def run():
        async with aiosqlite.connect(":memory:") as db:
            await db.execute("CREATE TABLE idle_events (actor TEXT, action TEXT, target_id TEXT, created_at REAL)")
            await db.execute("INSERT INTO idle_events VALUES ('ai3','mcp_outing','论坛',1)")
            await db.commit()

            @asynccontextmanager
            async def get_db():
                yield db

            monkeypatch.setattr(autonomy, "get_db", get_db)
            go = AsyncMock(return_value={"ok": True, "message": {"id": "m"}})
            import routes.mcp_tools as mcp_tools
            monkeypatch.setattr(mcp_tools, "go_out", go)
            for _ in range(5):
                await autonomy._run_mcp_outing("ai3")
            return [c.args[1] for c in go.await_args_list]

    assert asyncio.run(run()) == ["游戏"] * 5


def test_seat_messages_and_context_use_seat_room(env, monkeypatch):
    import routes.chatroom as chatroom_routes
    monkeypatch.setattr(chatroom_routes, "_get_or_create_seat_room", AsyncMock(return_value="room-ai3"))
    saved = AsyncMock(return_value={"id": "m1"})
    monkeypatch.setattr(autonomy, "_save_autonomy_chatroom_message", saved)
    asyncio.run(autonomy._save_private_message("ai3", "我回来了", force_private=True))
    assert saved.await_args.args[:3] == ("room-ai3", "ai3", "我回来了")

    monkeypatch.setattr(chatroom_routes, "_load_room_and_messages", AsyncMock(return_value=({}, [
        {"sender": "user", "content": "在吗", "created_at": 1}, {"sender": "ai3", "content": "在", "created_at": 2}])))
    monkeypatch.setattr(autonomy, "load_worldbook", lambda: {"user_persona": "喜欢猫"})
    messages = asyncio.run(autonomy._actor_context("ai3", 30))
    joined = "\n".join(m["content"] for m in messages)
    assert "Jasper" in joined and "喜欢猫" in joined and messages[-1] == {"role": "assistant", "content": "在"}
    assert autonomy._actor_label("ai3") == "Jasper"
