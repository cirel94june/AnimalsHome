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


LIVE = config.LIVE_STATE_HEADING


def _cached_prefix(messages):
    """缓存会覆盖的内容：标记之前（含标记那段）的全部文字。"""
    out = []
    for m in messages:
        content = m["content"]
        parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
        for p in parts:
            out.append(p["text"])
            if "cache_control" in p:
                return "".join(out)
    return None


def test_live_state_inside_the_ability_block_is_left_out_of_the_cache():
    def build(device_line):
        abilities = f"[系统能力]\n点歌……\n\n{LIVE}\n【当前日程列表】\n无\n\n【设备当前状态】\n{device_line}"
        return STABLE[:2] + [{"role": "user", "content": abilities}, {"role": "assistant", "content": "好的"}] + DYNAMIC

    a, idx = ai_providers._cache_breakpoint(build("电脑：3 秒前有键鼠输入"))
    b, _ = ai_providers._cache_breakpoint(build("电脑：40 秒前有键鼠输入"))
    assert idx == 2
    assert _cached_prefix(a) == _cached_prefix(b)
    assert "点歌" in _cached_prefix(a) and LIVE not in _cached_prefix(a)
    # 切开后文字一字不差
    assert "".join(p["text"] for p in a[2]["content"]) == build("电脑：3 秒前有键鼠输入")[2]["content"]


def test_separate_device_message_ends_the_cached_part():
    msgs = STABLE + [{"role": "user", "content": "【设备当前状态】\n电脑：刚刚有键鼠输入"},
                     {"role": "assistant", "content": "收到，我会结合当前设备状态判断。"}] + DYNAMIC
    marked, idx = ai_providers._cache_breakpoint(msgs)
    assert idx == 3
    assert "设备" not in _cached_prefix(marked)


def test_ability_block_puts_schedule_and_device_after_the_heading(monkeypatch):
    import context_builder

    async def no_items(*a, **k):
        return [{"name": "点歌", "text": "[MUSIC:歌名]"}]

    monkeypatch.setattr(context_builder, "build_capability_prompt_items", no_items)
    monkeypatch.setattr(context_builder, "format_ability_block", lambda items: "[系统能力]\n点歌")
    monkeypatch.setattr(context_builder, "is_capability_enabled", lambda name: name == "schedule")

    async def schedules():
        return []
    monkeypatch.setattr(context_builder, "get_active_schedules", schedules)
    monkeypatch.setattr(context_builder, "build_schedule_prompt", lambda s: "今天没有日程")
    monkeypatch.setattr(context_builder, "get_device_context_for_prompt", lambda: "【设备当前状态】\n电脑：5 秒前")
    monkeypatch.setattr(context_builder, "build_cli_file_storage_text", lambda key: "")
    block = asyncio.run(context_builder.build_ability_block("小猫", excluded_capabilities={"post_sentinel"}))
    head, tail = block.split(LIVE)
    assert "点歌" in head and "日程" not in head and "设备" not in head
    assert "今天没有日程" in tail and "5 秒前" in tail


def test_string_only_relay_gets_a_plain_retry(monkeypatch):
    sent = []

    def handle(request):
        body = json.loads(request.content)
        sent.append(_has_marker(body))
        if _has_marker(body):
            return httpx.Response(422, stream=_Stream(
                b'{"detail":[{"loc":["body","messages",3,"content"],"msg":"Input should be a valid string"}]}'))
        return _ok()
    ai_providers._NO_CACHE_MARKER.clear()
    ai_providers._NO_STREAM_USAGE.clear()
    cfg = {"model": "claude-x", "base_url": "https://string-only.example/v1"}
    assert _run(monkeypatch, handle, cfg) == "嗯"
    assert sent == [True, False]
    assert "string-only.example" in "".join(ai_providers._NO_CACHE_MARKER)


def test_type_error_on_another_message_is_not_blamed_on_the_marker(monkeypatch):
    sent = []

    def handle(request):
        sent.append(_has_marker(json.loads(request.content)))
        return httpx.Response(422, stream=_Stream(b'{"error":"messages.6.content: Input should be a valid string"}'))
    ai_providers._NO_CACHE_MARKER.clear()
    ai_providers._NO_STREAM_USAGE.clear()
    cfg = {"model": "claude-x", "base_url": "https://other-error.example/v1"}
    out = _run(monkeypatch, handle, cfg)
    assert "valid string" in out
    assert sent == [True]
    assert not ai_providers._NO_CACHE_MARKER
