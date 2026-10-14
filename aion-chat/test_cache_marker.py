"""Claude 缓存标记：标在「不变的部分」最后一条上；中转站不认会自动去掉重发；每条线路可以关。"""

import asyncio
import json

import httpx

import ai_providers
import config

REAL_CLIENT = httpx.AsyncClient

STABLE = [
    {"role": "user", "content": "[系统设定 - 小克人设]\n你是小克"},
    {"role": "assistant", "content": "收到"},
    {"role": "user", "content": "[系统能力]\n点歌……"},
    {"role": "assistant", "content": "收到"},
]
DYNAMIC = [
    {"role": "user", "content": "系统当前的准确时间是 2026年10月14日 10:00"},
    {"role": "assistant", "content": "收到。"},
    {"role": "user", "content": "在吗"},
]


def test_breakpoint_goes_on_the_last_stable_message():
    marked = ai_providers._with_cache_breakpoint(STABLE + DYNAMIC)
    assert marked[3]["content"] == [{"type": "text", "text": "收到", "cache_control": {"type": "ephemeral"}}]
    assert all(isinstance(m["content"], str) for i, m in enumerate(marked) if i != 3)
    assert STABLE[3]["content"] == "收到"  # 不改原列表
    seat = STABLE + [{"role": "user", "content": "[当前时间] 2026-10-14 10:00"}]
    assert "cache_control" in json.dumps(ai_providers._with_cache_breakpoint(seat)[3])


def test_no_boundary_means_no_marker():
    plain = [{"role": "user", "content": "hello"}]
    assert ai_providers._with_cache_breakpoint(plain) is plain


class _Stream(httpx.AsyncByteStream):
    def __init__(self, body: bytes):
        self.body = body

    async def __aiter__(self):
        yield self.body


def _ok():
    chunk = {"choices": [{"delta": {"content": "嗯"}}]}
    return httpx.Response(200, stream=_Stream(b"data: " + json.dumps(chunk).encode() + b"\n\ndata: [DONE]\n\n"))


def _run(monkeypatch, handler, cfg):
    monkeypatch.setattr(ai_providers.httpx, "AsyncClient",
                        lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))

    async def go():
        return "".join([c async for c in ai_providers.call_custom_openai(STABLE + DYNAMIC, cfg, {})
                        if isinstance(c, str) and not c.startswith(ai_providers.CLI_STATUS_PREFIX)])
    return asyncio.run(go())


def _has_marker(body: dict) -> bool:
    return "cache_control" in json.dumps(body["messages"])


def test_relay_that_rejects_the_marker_gets_a_plain_retry_and_is_remembered(monkeypatch):
    sent = []

    def handle(request):
        body = json.loads(request.content)
        sent.append(_has_marker(body))
        if _has_marker(body):
            return httpx.Response(400, stream=_Stream(b'{"error":"unknown field cache_control"}'))
        return _ok()
    ai_providers._NO_CACHE_MARKER.clear()
    ai_providers._NO_STREAM_USAGE.clear()
    cfg = {"model": "claude-x", "base_url": "https://refuse-cache.example/v1"}
    assert _run(monkeypatch, handle, cfg) == "嗯"
    assert sent == [True, False]
    sent.clear()
    assert _run(monkeypatch, handle, cfg) == "嗯"
    assert sent == [False]


def test_marker_is_sent_by_default_and_can_be_turned_off(monkeypatch):
    sent = []

    def handle(request):
        sent.append(_has_marker(json.loads(request.content)))
        return _ok()
    ai_providers._NO_CACHE_MARKER.clear()
    _run(monkeypatch, handle, {"model": "claude-x", "base_url": "https://ok.example/v1"})
    _run(monkeypatch, handle, {"model": "claude-x", "base_url": "https://ok.example/v1", "cache_marker": False})
    assert sent == [True, False]


def test_route_setting_survives_normalization():
    routes = config.normalize_custom_model_routes([
        {"id": "a", "base_url": "https://a.example", "models": ["m"]},
        {"id": "b", "base_url": "https://b.example", "models": ["n"], "cache_marker": False},
    ])
    assert [r["cache_marker"] for r in routes] == [True, False]
