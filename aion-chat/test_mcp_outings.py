import asyncio
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import actors
import config
import mcp_client
import mcp_outings

POSTS: list[str] = []
SEEN_HEADERS: list[str] = []


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def forum():
    hub = FastMCP("fake-forum", stateless_http=True)

    @hub.tool()
    def read_posts() -> str:
        """看看论坛最新的帖子"""
        return "1. 今天的晚霞好看吗\n2. 有人在玩新出的解谜游戏吗"

    @hub.tool()
    def reply(post_id: int, text: str) -> str:
        """回复一个帖子"""
        POSTS.append(f"{post_id}:{text}")
        return "回复成功"

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(hub.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_client, "MCP_SERVERS_PATH", tmp_path / "mcp_servers.json")
    (tmp_path / "mcp_servers.json").write_text(json.dumps({"servers": []}), encoding="utf-8")
    monkeypatch.setattr(actors, "ACTORS_PATH", tmp_path / "actors.json")
    monkeypatch.setattr(actors, "load_worldbook", lambda: {"ai_name": "小克"})
    monkeypatch.setattr(actors, "_connor_name", lambda: "Lucien")
    monkeypatch.setattr(mcp_outings, "load_worldbook", lambda: {"user_name": "Ceci"})
    monkeypatch.setitem(config.MODELS, "relay-gemini", {"provider": "custom_openai", "model": "g",
                                                        "base_url": "https://relay.example/v1", "api_key": "k"})
    monkeypatch.setitem(config.MODELS, "Codex-test", {"provider": "codex_cli", "model": "gpt"})
    actors._save_raw({"seats": {"ai3": {"model": "relay-gemini", "persona_sections": {"identity_core": "Jasper 的人设"}},
                                "connor": {"model": "Codex-test"}}})
    POSTS.clear()
    return tmp_path


def test_save_list_update_delete_and_headers_never_returned(env):
    t = mcp_outings.save_tool({"name": "小论坛", "url": "https://forum.example/mcp", "description": "大家聊天的地方",
                               "autonomy": True, "actors": ["ai3"], "headers": "Authorization: Bearer secret-token"})
    assert t["header_names"] == ["Authorization"] and "secret-token" not in json.dumps(mcp_outings.list_tools())
    with pytest.raises(ValueError, match="同名"):
        mcp_outings.save_tool({"name": "小论坛", "url": "https://x.example/mcp"})
    # 编辑时 headers=None 表示不改
    mcp_outings.save_tool({"name": "论坛", "url": "https://forum.example/mcp", "headers": None, "autonomy": False},
                          original_name="小论坛")
    raw = json.loads(mcp_client.MCP_SERVERS_PATH.read_text(encoding="utf-8"))["servers"]
    assert raw[0]["name"] == "论坛" and raw[0]["headers"] == {"Authorization": "Bearer secret-token"}
    assert mcp_outings.delete_tool("论坛") and not mcp_outings.list_tools()


@pytest.mark.parametrize("bad, message", [
    ({"name": "", "url": "https://a.example"}, "名字"),
    ({"name": "a", "url": "ftp://a"}, "http"),
    ({"name": "a", "url": "https://a.example", "type": "stdio"}, "类型"),
    ({"name": "a", "url": "https://a.example", "headers": "没有冒号"}, "名字: 值"),
])
def test_bad_input(env, bad, message):
    with pytest.raises(ValueError, match=message):
        mcp_outings.save_tool(bad)


def test_places_respect_enabled_autonomy_and_actor_list(env):
    mcp_outings.save_tool({"name": "论坛", "url": "https://a.example/mcp", "autonomy": True})
    mcp_outings.save_tool({"name": "游戏", "url": "https://b.example/mcp", "autonomy": True, "actors": ["connor"]})
    mcp_outings.save_tool({"name": "关着的", "url": "https://c.example/mcp", "autonomy": True, "enabled": False})
    mcp_outings.save_tool({"name": "只能手动", "url": "https://d.example/mcp", "autonomy": False})
    assert [p["name"] for p in mcp_outings.places_for("ai3")] == ["论坛"]
    assert [p["name"] for p in mcp_outings.places_for("connor")] == ["论坛", "游戏"]
    assert [p["name"] for p in mcp_outings.places_for("ai3", autonomy_only=False)] == ["论坛", "只能手动"]


def test_tool_model_needs_api_line(env):
    assert mcp_outings.tool_model_for("ai3") == "relay-gemini"
    assert mcp_outings.tool_model_for("connor") == ""  # Codex 不能走工具调用
    assert mcp_outings.tool_model_for("aion") == ""  # 没选模型


def test_outing_runs_tools_over_real_connection_and_reports(env, forum):
    mcp_outings.save_tool({"name": "小论坛", "url": forum, "description": "大家聊天的地方", "autonomy": True})
    calls = []

    async def fake_model(model_key, messages, tools):
        calls.append((model_key, [m["role"] for m in messages], [t["function"]["name"] for t in tools or []]))
        if tools is None:  # 回家后讲见闻
            assert "read_posts" in messages[-1]["content"]
            return {"content": "我刚去小论坛看了看，有人问晚霞，我回了一句。"}
        if len(calls) == 1:
            return {"content": "", "tool_calls": [{"id": "c1", "function": {"name": "read_posts", "arguments": "{}"}}]}
        if len(calls) == 2:
            assert messages[-1]["role"] == "tool" and "晚霞" in messages[-1]["content"]
            return {"content": "", "tool_calls": [{"id": "c2", "function": {
                "name": "reply", "arguments": json.dumps({"post_id": 1, "text": "很好看"}, ensure_ascii=False)}}]}
        return {"content": "逛完了"}

    result = asyncio.run(mcp_outings.run_outing("ai3", "小论坛", model_call=fake_model))
    assert result["summary"].startswith("我刚去小论坛") and result["model"] == "relay-gemini"
    assert POSTS == ["1:很好看"]
    assert [a.split(" ")[0] for a in result["actions"]] == ["read_posts", "reply"]
    assert calls[0][2] == ["read_posts", "reply"] and calls[-1][2] == []
    system = mcp_outings._system_prompt("Jasper", "Ceci", mcp_outings._server("小论坛"), "")
    assert "不要透露Ceci的任何个人信息" in system and "大家聊天的地方" in system


def test_outing_stops_after_max_rounds(env, forum, monkeypatch):
    monkeypatch.setattr(mcp_outings, "MAX_ROUNDS", 3)
    mcp_outings.save_tool({"name": "小论坛", "url": forum})
    rounds = []

    async def greedy(model_key, messages, tools):
        if tools is None:
            return {"content": "逛了好久"}
        rounds.append(1)
        return {"content": "", "tool_calls": [{"id": f"c{len(rounds)}", "function": {"name": "read_posts", "arguments": "{}"}}]}

    assert asyncio.run(mcp_outings.run_outing("ai3", "小论坛", model_call=greedy))["summary"] == "逛了好久"
    assert len(rounds) == 3


def test_outing_without_tool_model_is_refused(env):
    mcp_outings.save_tool({"name": "小论坛", "url": "https://a.example/mcp"})
    with pytest.raises(ValueError, match="能调用工具的模型"):
        asyncio.run(mcp_outings.run_outing("connor", "小论坛"))


def test_test_connection_lists_tools(env, forum):
    mcp_outings.save_tool({"name": "小论坛", "url": forum})
    assert asyncio.run(mcp_outings.test_tool("小论坛")) == {"ok": True, "tools": ["read_posts", "reply"]}
