"""后台线路（同一个模型换一家中转站）+ 用量里的缓存 + 流式用量请求的回退。"""

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import actors
import ai_providers
import chatroom
import config
import memory_hub_jobs
import usage_log

REAL_CLIENT = httpx.AsyncClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(actors, "ACTORS_PATH", tmp_path / "actors.json")
    monkeypatch.setattr(actors, "load_worldbook", lambda: {"ai_name": "小克"})
    monkeypatch.setattr(actors, "_connor_name", lambda: "Lucien")
    monkeypatch.setattr(chatroom, "CHATROOM_CONFIG_PATH", tmp_path / "chatroom_config.json")
    monkeypatch.setattr(config, "SETTINGS", {"custom_model_routes": [
        {"id": "per_call", "name": "按次站", "base_url": "https://a.example/v1", "api_key": "k1",
         "models": [{"key": "按次·opus", "model": "claude-opus-5-5"}, {"key": "按次·gemini", "model": "gemini-3-pro"}]},
        {"id": "per_token", "name": "按量站", "base_url": "https://b.example/v1", "api_key": "k2",
         "models": [{"key": "按量·opus", "model": "anthropic/claude-opus-5-5"}]},
    ]})
    monkeypatch.setattr(config, "save_settings", lambda data: None)
    config.refresh_custom_models()
    app = FastAPI()
    app.include_router(actors.router)
    yield TestClient(app)
    monkeypatch.undo()
    config.refresh_custom_models()


def test_same_underlying_model_ignores_relay_prefix_and_date_suffix(client):
    assert config.same_underlying_model("按次·opus", "按量·opus")
    assert not config.same_underlying_model("按次·opus", "按次·gemini")
    config.MODELS["dated"] = {"provider": "custom_openai", "model": "claude-opus-5-5-20261001"}
    config.MODELS["dashed"] = {"provider": "custom_openai", "model": "claude-opus-5-5-2026-10-01"}
    config.MODELS["opus-x"] = {"provider": "custom_openai", "model": "claude-opus-5-5-x"}
    assert config.same_underlying_model("dated", "按次·opus")
    assert config.same_underlying_model("dashed", "按次·opus")
    assert not config.same_underlying_model("opus-x", "按次·opus")
    # 版本号不同就是不同模型（Codex 复审 H1 的反例）
    assert not config.same_model_id("claude-sonnet-4", "claude-sonnet-4-5")
    assert not config.same_model_id("claude-opus-5", "claude-opus-5-5")
    assert not config.same_model_id("gpt-5", "gpt-5-1")


def test_card_refuses_a_background_route_with_a_different_model(client):
    assert client.put("/api/actors/ai3", json={"model": "按次·opus"}).status_code == 200
    bad = client.put("/api/actors/ai3", json={"background_model": "按次·gemini"})
    assert bad.status_code == 400 and "同一个模型" in bad.json()["detail"]
    ok = client.put("/api/actors/ai3", json={"background_model": "按量·opus"})
    assert ok.status_code == 200 and ok.json()["background_model"] == "按量·opus"


def test_background_model_is_used_only_while_it_matches(client):
    client.put("/api/actors/ai3", json={"model": "按次·opus", "background_model": "按量·opus", "enabled": True})
    assert memory_hub_jobs.background_model("ai3") == "按量·opus"
    # 后来平时的模型换了：后台线路不再是同一个模型，就照旧用平时的，不会让别的模型替 TA 写
    actors_data = json.loads(Path(actors.ACTORS_PATH).read_text(encoding="utf-8"))
    actors_data["seats"]["ai3"]["model"] = "按次·gemini"
    Path(actors.ACTORS_PATH).write_text(json.dumps(actors_data), encoding="utf-8")
    assert memory_hub_jobs.background_model("ai3") == "按次·gemini"
    # 没选后台线路：和平时一样；内置两位可以传入平时用的
    assert memory_hub_jobs.background_model("ai4") == ""
    client.put("/api/actors/aion", json={"background_model": "按量·opus"})
    assert memory_hub_jobs.background_model("aion", "按次·opus") == "按量·opus"
    assert memory_hub_jobs.background_model("aion", "按次·gemini") == "按次·gemini"


def test_rejected_card_changes_nothing_even_with_a_new_own_api(client):
    """同一次提交里自己填 API + 不匹配的后台线路：先校验再写，拒绝时线路和名片都不变（Codex 复审 M3）。"""
    before_routes = json.dumps(config.SETTINGS.get(config.SEAT_ROUTES_KEY), sort_keys=True)
    bad = client.put("/api/actors/ai3", json={
        "api": {"base_url": "https://gem.example/v1", "api_key": "sk-x", "model": "gemini-3-pro"},
        "background_model": "按量·opus"})
    assert bad.status_code == 400
    assert json.dumps(config.SETTINGS.get(config.SEAT_ROUTES_KEY), sort_keys=True) == before_routes
    assert actors.get_actor("ai3")["model"] == "" and actors.api_summary("ai3") is None
    ok = client.put("/api/actors/ai3", json={
        "api": {"base_url": "https://op.example/v1", "api_key": "sk-x", "model": "claude-opus-5-5"},
        "background_model": "按量·opus"})
    assert ok.status_code == 200 and ok.json()["background_model"] == "按量·opus"


def _dream_setup(client, monkeypatch, during_wait):
    client.put("/api/actors/ai3", json={"model": "按次·opus", "background_model": "按量·opus", "enabled": True})
    bridge = memory_hub_jobs.bridge
    monkeypatch.setattr(bridge, "load_config", lambda: {})
    monkeypatch.setattr(bridge, "source_ai_for", lambda actor, cfg=None: "jasper")
    written = []

    async def call_tool(cfg, tool, args, sent=None):
        if args.get("action") == "materials":
            during_wait()  # 等 Hub 给材料的这几秒里，线路 / 名片被改了
            return json.dumps({"prompt": "今天的材料"})
        written.append(args)
        return json.dumps({"status": "dreamed"})
    monkeypatch.setattr(bridge, "_call_tool", call_tool)

    async def fake_stream(messages, model_key, meta=None, *a, **k):
        yield "梦"
    monkeypatch.setattr(ai_providers, "_stream_ai", fake_stream)
    return written


def test_dream_stops_if_the_background_route_is_edited_while_waiting(client, monkeypatch):
    def edit_route():
        config.MODELS["按量·opus"] = dict(config.MODELS["按量·opus"], model="gemini-3-pro")
    written = _dream_setup(client, monkeypatch, edit_route)
    with pytest.raises(Exception):
        asyncio.run(memory_hub_jobs.dream_for("ai3"))
    assert written == []


def test_dream_stops_if_the_ais_own_model_changes_while_waiting(client, monkeypatch):
    def change_card():
        data = json.loads(Path(actors.ACTORS_PATH).read_text(encoding="utf-8"))
        data["seats"]["ai3"]["model"] = "按次·gemini"
        Path(actors.ACTORS_PATH).write_text(json.dumps(data), encoding="utf-8")
    written = _dream_setup(client, monkeypatch, change_card)
    with pytest.raises(RuntimeError, match="换过"):
        asyncio.run(memory_hub_jobs.dream_for("ai3"))
    assert written == []


def test_dream_is_written_with_the_pinned_background_route(client, monkeypatch):
    written = _dream_setup(client, monkeypatch, lambda: None)
    assert asyncio.run(memory_hub_jobs.dream_for("ai3")) == "dreamed"
    assert written and written[0]["source_ai"] == "jasper"


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_log, "USAGE_DB", tmp_path / "usage.db")
    return tmp_path


@pytest.mark.parametrize("usage, expect", [
    ({"prompt_tokens": 1000, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 800}}, (1000, 800, 0)),
    # Claude 原生格式：读入不含缓存，统一成含缓存的总数（Codex 复审 L1 的反例）
    ({"input_tokens": 1000, "output_tokens": 10, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 100},
     (2000, 900, 100)),
    # OpenAI 格式里即使带了 Claude 的缓存字段，prompt_tokens 也已经是总数
    ({"prompt_tokens": 2000, "completion_tokens": 1, "cache_read_input_tokens": 900}, (2000, 900, 0)),
    ({"promptTokenCount": 2000, "candidatesTokenCount": 5, "cachedContentTokenCount": 1500}, (2000, 1500, 0)),
])
def test_cache_numbers_are_recorded_from_each_shape(book, usage, expect):
    data = {"usageMetadata": usage} if "promptTokenCount" in usage else {"usage": usage}
    usage_log.record_json("m", "聊天", data, "", "")
    t = usage_log.summary(1)["total"]
    assert (t["prompt"], t["cache_read"], t["cache_write"]) == expect


def test_old_notebook_gets_the_cache_columns(book):
    conn = sqlite3.connect(usage_log.USAGE_DB)
    conn.execute("CREATE TABLE usage (day TEXT, kind TEXT, model TEXT, requests INTEGER, prompt INTEGER, "
                 "completion INTEGER, estimated INTEGER, PRIMARY KEY (day, kind, model))")
    conn.execute("INSERT INTO usage VALUES (date('now','localtime'),'聊天','m',1,10,1,0)")
    conn.commit()
    conn.close()
    usage_log.record("m", "聊天", 10, 1, estimated=False, cache_read=7)
    t = usage_log.summary(1)["total"]
    assert t["requests"] == 2 and t["cache_read"] == 7


class _Stream(httpx.AsyncByteStream):
    def __init__(self, body: bytes):
        self.body = body

    async def __aiter__(self):
        yield self.body


def _sse(*chunks):
    return b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"


def test_stream_usage_is_requested_and_dropped_for_relays_that_refuse(monkeypatch):
    sent = []

    def handle(request):
        body = json.loads(request.content)
        sent.append(body)
        if "stream_options" in body:
            return httpx.Response(400, stream=_Stream(b'{"error":"unknown field stream_options"}'))
        return httpx.Response(200, stream=_Stream(_sse({"choices": [{"delta": {"content": "你好"}}]})))
    monkeypatch.setattr(ai_providers.httpx, "AsyncClient",
                        lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handle), **kw))
    ai_providers._NO_STREAM_USAGE.clear()
    cfg = {"model": "m", "base_url": "https://refuse.example/v1"}

    async def run():
        return "".join([c async for c in ai_providers.call_custom_openai([{"role": "user", "content": "x"}], cfg, {})
                        if isinstance(c, str)])
    assert asyncio.run(run()) == "你好"
    assert [("stream_options" in b) for b in sent] == [True, False]
    sent.clear()
    assert asyncio.run(run()) == "你好"  # 记住了，不再先试一次
    assert [("stream_options" in b) for b in sent] == [False]


def test_stream_usage_with_cache_reaches_meta(monkeypatch):
    usage = {"prompt_tokens": 3000, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 2500}}

    def handle(request):
        assert json.loads(request.content)["stream_options"] == {"include_usage": True}
        return httpx.Response(200, stream=_Stream(_sse({"choices": [{"delta": {"content": "嗯"}}]},
                                                       {"choices": [], "usage": usage})))
    monkeypatch.setattr(ai_providers.httpx, "AsyncClient",
                        lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handle), **kw))
    ai_providers._NO_STREAM_USAGE.clear()
    meta = {}

    async def run():
        return [c async for c in ai_providers.call_custom_openai(
            [{"role": "user", "content": "x"}], {"model": "m", "base_url": "https://ok.example"}, meta)]
    asyncio.run(run())
    assert meta["prompt_tokens"] == 3000 and usage_log.cache_tokens(meta["raw"]) == (2500, 0)


def test_upgrading_the_usual_model_is_never_blocked_by_an_old_background_route(client):
    """平时的模型升级（比如换到新版本）时，名片会把旧的后台线路一起提交：照常保存，旧后台线路自动清空并提醒。"""
    config.MODELS["新版·sonnet"] = {"provider": "custom_openai", "model": "claude-sonnet-9"}
    assert client.put("/api/actors/aion", json={"model": "按次·opus", "background_model": "按量·opus"}).status_code == 200
    r = client.put("/api/actors/aion", json={"model": "新版·sonnet", "background_model": "按量·opus"})
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "新版·sonnet" and body["background_model"] == "" and "已经清空" in body["notice"]
    assert memory_hub_jobs.background_model("aion") == "新版·sonnet"
    # 但这次新选一条对不上的后台线路，仍然拒绝
    bad = client.put("/api/actors/aion", json={"background_model": "按量·opus"})
    assert bad.status_code == 400
