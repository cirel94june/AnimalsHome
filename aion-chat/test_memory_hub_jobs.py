import asyncio
import json
import socket
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import actors
import memory_hub_bridge as bridge
import memory_hub_jobs as jobs

CALLS: list[tuple[str, dict]] = []
HUB = {"already_dreamed": False, "too_few": False}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def fake_hub():
    hub = FastMCP("fake-memory-hub", stateless_http=True)

    @hub.tool()
    def capture(action: str = "log", source_ai: str = "claude", platform: str = "mcp", content: str = "",
                model: str = "", user_message: str = "", ai_response: str = "", chat_type: str = "private") -> str:
        CALLS.append(("capture", {"action": action, "source_ai": source_ai, "platform": platform,
                                  "content": content, "model": model}))
        return json.dumps({"status": "saved", "ai_id": source_ai, "platform": platform, "chars": len(content)})

    @hub.tool()
    def dream(content: str = "", source_ai: str = "claude", action: str = "write", kind: str = "reflection",
              model: str = "") -> str:
        CALLS.append(("dream", {"action": action, "source_ai": source_ai, "kind": kind, "content": content,
                                "model": model}))
        if action == "materials":
            data = {"ai_id": source_ai, "local_day": "2026-10-11", "already_dreamed_today": HUB["already_dreamed"],
                    "prompt": f"给 {source_ai} 的做梦提示词：白天聊过蜡烛", "materials": []}
            if HUB["too_few"]:
                data["reason"] = "too_few_materials"
            return json.dumps(data, ensure_ascii=False)
        return json.dumps({"status": "dreamed", "id": "dream_x"})

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
def env(tmp_path, monkeypatch, fake_hub):
    monkeypatch.setattr(bridge, "CONFIG_PATH", tmp_path / "memory_hub.json")
    monkeypatch.setattr(bridge, "OUTBOX_PATH", tmp_path / "outbox.db")
    monkeypatch.setattr(bridge, "_cooldown_until", {"read": 0.0, "write": 0.0})
    monkeypatch.setattr(jobs, "JOBS_PATH", tmp_path / "jobs.json")
    monkeypatch.setattr(jobs, "_status", {"handoff": {}, "dream": {}})
    monkeypatch.setattr(actors, "ACTORS_PATH", tmp_path / "actors.json")
    monkeypatch.setattr(actors, "load_worldbook", lambda: {"ai_name": "小克"})
    monkeypatch.setattr(actors, "_connor_name", lambda: "Lucien")
    monkeypatch.setattr(jobs, "load_worldbook", lambda: {"user_name": "Ceci", "ai_persona": "小克的人设"})
    monkeypatch.setitem(jobs.MODELS, "relay-claude", {"provider": "custom_openai", "model": "claude-x"})
    monkeypatch.setitem(jobs.MODELS, "relay-gemini", {"provider": "custom_openai", "model": "gemini-x"})
    for key in ("MEMORY_HUB_URL", "MEMORY_HUB_TOKEN", "MEMORY_HUB_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / "memory_hub.json").write_text(json.dumps({"enabled": True, "url": fake_hub}), encoding="utf-8")
    actors._save_raw({"seats": {
        "aion": {"model": "relay-claude"},
        "ai3": {"model": "relay-gemini", "persona_sections": {"identity_core": "Jasper 的人设"}},
    }})
    prompts: list[tuple[str, list]] = []

    async def fake_complete(model_key, messages):
        prompts.append((model_key, messages))
        return f"由 {model_key} 写的内容"

    monkeypatch.setattr(jobs, "complete", fake_complete)
    CALLS.clear()
    HUB.update(already_dreamed=False, too_few=False)
    return prompts


def _age_activity(seconds: float) -> None:
    with sqlite3.connect(bridge.OUTBOX_PATH) as conn:
        conn.execute("UPDATE activity SET last_ts=last_ts-?, last_private_ts=last_private_ts-?", (seconds, seconds))
        conn.execute("UPDATE recent_turns SET created_at=created_at-?", (seconds,))


def test_handoff_written_with_own_model_after_quiet_period(env):
    bridge._enqueue("aion", "今天好累", "抱抱你", "private")
    bridge._enqueue("aion", "群里随便说说", "嗯", "group")
    assert asyncio.run(jobs.run_handoffs()) == {}  # 还没安静 30 分钟

    _age_activity(31 * 60)
    assert asyncio.run(jobs.run_handoffs()) == {"aion": "saved"}
    model_key, messages = env[-1]
    assert model_key == "relay-claude"
    prompt = "\n".join(m["content"] for m in messages)
    assert "小克的人设" in prompt and "Ceci：今天好累" in prompt and "群里随便说说" not in prompt
    assert CALLS[-1] == ("capture", {"action": "handoff", "source_ai": "claude", "platform": "aionshome",
                                     "content": "由 relay-claude 写的内容", "model": "claude-x"})
    assert asyncio.run(jobs.run_handoffs()) == {}  # 这段已经写过

    bridge._enqueue("aion", "我又来了", "欢迎", "private")  # 新的一段
    _age_activity(31 * 60)
    assert asyncio.run(jobs.run_handoffs()) == {"aion": "saved"}
    prompt = "\n".join(m["content"] for m in env[-1][1])
    assert "我又来了" in prompt and "今天好累" not in prompt  # 隔了 30 分钟以上的上一段不混进来


def test_handoff_skipped_without_own_model(env):
    bridge._enqueue("connor", "在吗", "在", "private")  # Lucien 没设模型
    _age_activity(31 * 60)
    assert asyncio.run(jobs.run_handoffs()) == {"connor": "skipped:no_model"}
    assert env == [] and not [c for c in CALLS if c[0] == "capture"]
    assert asyncio.run(jobs.run_handoffs()) == {}


def test_handoff_failure_retries_later(env, monkeypatch):
    async def broken(model_key, messages):
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr(jobs, "complete", broken)
    bridge._enqueue("ai3", "晚安", "晚安", "private")
    _age_activity(31 * 60)
    assert asyncio.run(jobs.run_handoffs())["ai3"].startswith("failed")
    assert asyncio.run(jobs.run_handoffs()) == {}  # 30 分钟内不重试

    with sqlite3.connect(bridge.OUTBOX_PATH) as conn:
        conn.execute("UPDATE activity SET handoff_retry_at=0")
    monkeypatch.setattr(jobs, "complete", lambda m, msgs: asyncio.sleep(0, result="补写的卡"))
    assert asyncio.run(jobs.run_handoffs()) == {"ai3": "saved"}
    assert CALLS[-1][1]["source_ai"] == "jasper" and CALLS[-1][1]["model"] == "gemini-x"


def test_old_sessions_are_not_backfilled(env):
    bridge._enqueue("aion", "很久以前", "嗯", "private")
    _age_activity(7 * 3600)
    assert asyncio.run(jobs.run_handoffs()) == {"aion": "skipped:too_old"}
    assert env == []


def _night(hour=4, minute=0):
    return datetime(2026, 10, 11, hour, minute, tzinfo=jobs.LOCAL_TZ)


def test_dreams_each_ai_with_own_model_once_a_night(env):
    assert asyncio.run(jobs.run_dreams(_night(2, 0))) == {}  # 3:30 前不做
    results = asyncio.run(jobs.run_dreams(_night()))
    assert results == {"aion": "dreamed", "connor": "skipped:no_model", "ai3": "dreamed"}
    jasper_model, jasper_messages = env[-1]
    assert jasper_model == "relay-gemini"
    assert "Jasper 的人设" in jasper_messages[0]["content"]
    assert jasper_messages[-1]["content"] == "给 jasper 的做梦提示词：白天聊过蜡烛"
    writes = [c[1] for c in CALLS if c[0] == "dream" and c[1]["action"] == "write"]
    assert [(w["source_ai"], w["kind"], w["model"]) for w in writes] == [("claude", "dream", "claude-x"),
                                                                         ("jasper", "dream", "gemini-x")]
    CALLS.clear()
    assert asyncio.run(jobs.run_dreams(_night(5, 0))) == {}  # 今晚已经做过
    assert CALLS == []


def test_dream_respects_hub_already_dreamed_and_too_few(env):
    HUB["already_dreamed"] = True
    assert asyncio.run(jobs.run_dreams(_night()))["aion"] == "already_dreamed"
    assert env == []
    jobs.JOBS_PATH.unlink()
    HUB.update(already_dreamed=False, too_few=True)
    assert asyncio.run(jobs.run_dreams(_night()))["aion"] == "skipped:too_few_materials"


def test_dream_failure_retries_up_to_limit(env, monkeypatch):
    async def broken(model_key, messages):
        raise RuntimeError("HTTP 429")

    monkeypatch.setattr(jobs, "complete", broken)
    assert asyncio.run(jobs.run_dreams(_night()))["aion"].startswith("failed")
    assert asyncio.run(jobs.run_dreams(_night())) == {}  # 1 小时内不重试
    state = json.loads(jobs.JOBS_PATH.read_text(encoding="utf-8"))
    state["dream"]["aion"]["last_try"] = 0
    state["dream"]["aion"]["attempts"] = jobs.DREAM_MAX_ATTEMPTS
    jobs.JOBS_PATH.write_text(json.dumps(state), encoding="utf-8")
    assert "aion" not in asyncio.run(jobs.run_dreams(_night()))  # 次数用完，今晚放弃


def test_complete_collects_text_and_raises_provider_errors(monkeypatch):
    import ai_providers

    async def ok(messages, model_key, meta=None, *a, include_device_context=True, **kw):
        assert include_device_context is False  # 交接卡和梦不需要设备状态
        yield f"{ai_providers.CLI_STATUS_PREFIX}思考中"
        yield "梦见"
        yield "海边[MUSIC:晴天]"

    async def bad(messages, model_key, meta=None, *a, **kw):
        meta["provider_error"] = "HTTP 401"
        yield "[错误] 401"

    monkeypatch.setattr(ai_providers, "stream_ai", ok)
    assert asyncio.run(jobs.complete("k", [])) == "梦见海边"
    monkeypatch.setattr(ai_providers, "stream_ai", bad)
    with pytest.raises(RuntimeError, match="401"):
        asyncio.run(jobs.complete("k", []))


def test_own_model_never_falls_back(env):
    data = actors._load_raw()
    data["seats"]["aion"]["model"] = "已删除的线路"
    actors._save_raw(data)
    assert jobs.own_model("aion") == ""
    assert jobs.own_model("connor") == ""
    assert jobs.own_model("ai3") == "relay-gemini"
