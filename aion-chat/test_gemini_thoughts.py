"""2026-10-29 狗蛋（Gemini，走 OpenAI 兼容中转站）的 💭 一直是空的。

4Router 实测：要带 extra_body.google.thinking_config.include_thoughts 才回思考；
而且边写边传时会把思考丢掉，只有一次性返回才带。
"""

import asyncio
import json

import httpx
import pytest

import ai_providers

REAL_CLIENT = httpx.AsyncClient
USAGE = {"prompt_tokens": 10, "completion_tokens": 200, "completion_tokens_details": {"reasoning_tokens": 180}}


class _Stream(httpx.AsyncByteStream):
    def __init__(self, body: bytes):
        self.body = body

    async def __aiter__(self):
        yield self.body


def _sse(*chunks):
    return b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"


@pytest.fixture(autouse=True)
def _fresh():
    for memo in (ai_providers._NO_GEMINI_THOUGHTS, ai_providers._STREAM_DROPS_THOUGHTS,
                 ai_providers._NO_STREAM_USAGE, ai_providers._NO_CACHE_MARKER, ai_providers._NO_LONG_CACHE):
        memo.clear()
    yield


def _relay(monkeypatch, handle):
    sent = []

    def record(request):
        body = json.loads(request.content)
        sent.append(body)
        return handle(body)
    monkeypatch.setattr(ai_providers.httpx, "AsyncClient",
                        lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(record), **kw))
    return sent


def _call(model, meta=None):
    cfg = {"model": model, "base_url": "https://relay.example/v1", "cache_marker": False}

    async def run():
        return "".join([c async for c in ai_providers.call_custom_openai(
            [{"role": "user", "content": "x"}], cfg, meta if meta is not None else {})
            if isinstance(c, str) and not c.startswith(ai_providers.CLI_STATUS_PREFIX)])
    return asyncio.run(run())


def test_gemini_asks_for_thoughts_and_stores_streamed_ones(monkeypatch):
    sent = _relay(monkeypatch, lambda body: httpx.Response(200, stream=_Stream(_sse(
        {"choices": [{"delta": {"reasoning_content": "先算 17×20"}}]},
        {"choices": [{"delta": {"content": "391"}}]}, {"choices": [], "usage": USAGE}))))
    meta = {}
    assert _call("gemini-3.8-flash", meta) == "391"
    assert sent[0]["extra_body"] == {"google": {"thinking_config": {"include_thoughts": True}}}
    assert meta["reasoning_content"] == "先算 17×20"
    assert not ai_providers._STREAM_DROPS_THOUGHTS  # 边写边传也给了思考：不用改


def test_other_models_are_left_alone(monkeypatch):
    sent = _relay(monkeypatch, lambda body: httpx.Response(200, stream=_Stream(_sse(
        {"choices": [{"delta": {"content": "嗯"}}]}, {"choices": [], "usage": USAGE}))))
    for model in ("deepseek-reasoner", "kimi-k2-thinking", "grok-4", "[kiro量高缓]claude-opus-4-6-thinking"):
        assert _call(model) == "嗯"
    assert all("extra_body" not in body and body["stream"] is True for body in sent)
    assert not ai_providers._STREAM_DROPS_THOUGHTS


def test_relay_that_drops_streamed_thoughts_switches_to_whole_replies(monkeypatch):
    def handle(body):
        if body["stream"]:
            return httpx.Response(200, stream=_Stream(_sse(
                {"choices": [{"delta": {"content": "391"}}]}, {"choices": [], "usage": USAGE})))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "391",
                                                                     "reasoning_content": "17×23=391"}}],
                                         "usage": USAGE})
    sent = _relay(monkeypatch, handle)
    first = {}
    assert _call("gemini-3.8-flash", first) == "391"
    assert "reasoning_content" not in first  # 这一条丢了，记下来
    second = {}
    assert _call("gemini-3.8-flash", second) == "391"
    assert second["reasoning_content"] == "17×23=391" and second["completion_tokens"] == 200
    assert [body["stream"] for body in sent] == [True, False]
    assert "stream_options" not in sent[1] and sent[1]["extra_body"]


def test_no_reasoning_tokens_means_nothing_was_dropped(monkeypatch):
    usage = {"prompt_tokens": 10, "completion_tokens": 3, "completion_tokens_details": {"reasoning_tokens": 0}}
    _relay(monkeypatch, lambda body: httpx.Response(200, stream=_Stream(_sse(
        {"choices": [{"delta": {"content": "嗯"}}]}, {"choices": [], "usage": usage}))))
    assert _call("gemini-3.8-flash") == "嗯"
    assert not ai_providers._STREAM_DROPS_THOUGHTS


def test_relay_that_rejects_the_switch_gets_it_removed(monkeypatch):
    def handle(body):
        if "extra_body" in body:
            return httpx.Response(400, stream=_Stream(b'{"error":"unknown field extra_body"}'))
        return httpx.Response(200, stream=_Stream(_sse({"choices": [{"delta": {"content": "391"}}]})))
    sent = _relay(monkeypatch, handle)
    assert _call("gemini-3.8-flash") == "391"
    assert [("extra_body" in b) for b in sent] == [True, False]
    sent.clear()
    assert _call("gemini-3.8-flash") == "391"  # 记住了，不再先试
    assert [("extra_body" in b) for b in sent] == [False]


def test_whole_reply_errors_are_reported(monkeypatch):
    ai_providers._STREAM_DROPS_THOUGHTS.add(("https://relay.example/v1/chat/completions", "gemini-3.8-flash"))
    _relay(monkeypatch, lambda body: httpx.Response(503, json={"error": "busy"}))
    meta = {}
    assert "busy" in _call("gemini-3.8-flash", meta)
    assert meta["provider_error"].startswith("HTTP 503")


def test_whole_reply_that_rejects_the_switch_falls_back_like_streaming(monkeypatch):
    """复审 M1：先发现流式丢思考，改一次性；一次性又不认开关 → 记住、去掉开关，照常聊天。"""
    def handle(body):
        if "extra_body" in body and not body["stream"]:
            return httpx.Response(400, json={"error": "unknown field extra_body"})
        return httpx.Response(200, stream=_Stream(_sse(
            {"choices": [{"delta": {"content": "391"}}]}, {"choices": [], "usage": USAGE})))
    sent = _relay(monkeypatch, handle)
    assert _call("gemini-3.8-flash") == "391"  # 流式，丢了思考 → 记下
    meta = {}
    assert _call("gemini-3.8-flash", meta) == "391"  # 一次性被拒 → 去开关重发
    assert "provider_error" not in meta
    assert [(b["stream"], "extra_body" in b) for b in sent] == [(True, True), (False, True), (True, False)]
    assert "https://relay.example/v1/chat/completions" in ai_providers._NO_GEMINI_THOUGHTS
    sent.clear()
    assert _call("gemini-3.8-flash") == "391"
    assert [(b["stream"], "extra_body" in b) for b in sent] == [(True, False)]


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="<html>502 Bad Gateway</html>"),
    httpx.Response(200, json=[{"choices": []}]),
    httpx.Response(200, json={"choices": "oops"}),
    httpx.Response(200, json={"choices": [{"message": "oops"}], "usage": []}),
    httpx.Response(200, json={"choices": [{"message": {"content": {"unexpected": "object"}}}]}),
    httpx.Response(200, json={"choices": [{"message": {"content": "OK", "reasoning_content": ["bad"]}}]}),
    httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}], "usage": ["bad"]}),
])
def test_broken_whole_replies_are_recorded_and_not_resent(monkeypatch, response):
    """复审 L2：200 但不是想要的 JSON，也要记 provider_error，不重发生成请求。"""
    ai_providers._STREAM_DROPS_THOUGHTS.add(("https://relay.example/v1/chat/completions", "gemini-3.8-flash"))
    sent = _relay(monkeypatch, lambda body: response)
    meta = {}
    _call("gemini-3.8-flash", meta)
    assert meta.get("provider_error")
    assert len(sent) == 1


def test_whole_reply_without_optional_fields_is_fine(monkeypatch):
    ai_providers._STREAM_DROPS_THOUGHTS.add(("https://relay.example/v1/chat/completions", "gemini-3.8-flash"))
    _relay(monkeypatch, lambda body: httpx.Response(200, json={
        "choices": [{"message": {"content": "OK", "reasoning_content": None}}], "usage": None}))
    meta = {}
    assert _call("gemini-3.8-flash", meta) == "OK"
    assert "provider_error" not in meta
