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

import memory_hub_bridge as bridge


CALLS: list[tuple[str, dict]] = []


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def fake_hub():
    hub = FastMCP("fake-memory-hub", stateless_http=True)

    @hub.tool()
    def context(source_ai: str = "claude", message: str = "", mode: str = "full", max_chars: int = 3000) -> str:
        CALLS.append(("context", {"source_ai": source_ai, "message": message, "mode": mode, "max_chars": max_chars}))
        if message == "slow":
            time.sleep(2)
        return json.dumps({"text": f"【最近动态】{source_ai} 在 TG 上陪小猫聊了蜡烛", "metadata": {}}, ensure_ascii=False)

    @hub.tool()
    def capture(action: str = "log", source_ai: str = "claude", user_message: str = "",
                ai_response: str = "", platform: str = "mcp") -> str:
        CALLS.append(("capture", {"action": action, "source_ai": source_ai, "user_message": user_message,
                                  "ai_response": ai_response, "platform": platform}))
        return json.dumps({"status": "ok"})

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


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "CONFIG_PATH", tmp_path / "memory_hub.json")
    monkeypatch.setattr(bridge, "_cooldown_until", 0.0)
    for key in ("MEMORY_HUB_URL", "MEMORY_HUB_TOKEN", "MEMORY_HUB_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    CALLS.clear()
    return tmp_path / "memory_hub.json"


def _write(path: Path, **cfg):
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


def test_disabled_by_default_does_nothing():
    assert asyncio.run(bridge.context_block("aion", "你好")) == ""
    assert CALLS == []


def test_context_block_injects_hub_text_for_mapped_actor(fake_hub, isolated_config):
    _write(isolated_config, enabled=True, url=fake_hub, actors={"aion": "claude", "connor": "lucien"})
    block = asyncio.run(bridge.context_block("connor", "今天怎么样"))
    assert block.startswith("[跨端记忆]")
    assert "lucien 在 TG 上陪小猫聊了蜡烛" in block
    assert CALLS[-1] == ("context", {"source_ai": "lucien", "message": "今天怎么样", "mode": "incremental", "max_chars": 2500})


def test_unmapped_actor_is_skipped(fake_hub, isolated_config):
    _write(isolated_config, enabled=True, url=fake_hub, actors={"aion": "claude"})
    assert asyncio.run(bridge.context_block("connor", "hi")) == ""
    assert CALLS == []


def test_capture_turn_logs_with_platform_and_identity(fake_hub, isolated_config):
    _write(isolated_config, enabled=True, url=fake_hub)
    ok = asyncio.run(bridge.capture_turn("aion", "我到家了", "欢迎回来", chat_type="group"))
    assert ok
    assert CALLS[-1] == ("capture", {"action": "log", "source_ai": "claude", "user_message": "我到家了",
                                     "ai_response": "欢迎回来", "platform": "aionshome-group"})


def test_schedule_capture_strips_tool_commands(fake_hub, isolated_config):
    _write(isolated_config, enabled=True, url=fake_hub)

    async def run():
        bridge.schedule_capture("aion", "放首歌", "好呀[MUSIC:晴天]")
        await asyncio.gather(*list(bridge._background_tasks))

    asyncio.run(run())
    assert CALLS[-1][1]["ai_response"] == "好呀"


def test_unreachable_hub_fails_fast_and_cools_down(isolated_config):
    _write(isolated_config, enabled=True, url=f"http://127.0.0.1:{_free_port()}/mcp")
    started = time.monotonic()
    assert asyncio.run(bridge.context_block("aion", "hi")) == ""
    assert time.monotonic() - started < 7
    assert bridge._in_cooldown()
    assert asyncio.run(bridge.capture_turn("aion", "a", "b")) is False


def test_slow_hub_times_out_without_blocking_chat(fake_hub, isolated_config):
    _write(isolated_config, enabled=True, url=fake_hub, context_timeout_seconds=0.5)
    started = time.monotonic()
    assert asyncio.run(bridge.context_block("aion", "slow")) == ""
    assert time.monotonic() - started < 1.5


def test_unwrap_text_handles_nested_result_payload():
    nested = json.dumps({"result": json.dumps({"text": "  记忆正文  ", "metadata": {}}, ensure_ascii=False)}, ensure_ascii=False)
    assert bridge._unwrap_text(nested) == "记忆正文"
    assert bridge._unwrap_text("纯文本") == "纯文本"


def test_env_overrides_config(isolated_config, monkeypatch):
    _write(isolated_config, enabled=False, url="")
    monkeypatch.setenv("MEMORY_HUB_URL", "http://127.0.0.1:1/mcp")
    monkeypatch.setenv("MEMORY_HUB_ENABLED", "true")
    monkeypatch.setenv("MEMORY_HUB_TOKEN", "secret")
    cfg = bridge.load_config()
    assert cfg["enabled"] and cfg["url"] == "http://127.0.0.1:1/mcp"
    assert bridge._headers(cfg)["Authorization"] == "Bearer secret"
