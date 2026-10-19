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
    assert idx == [2]
    assert _cached_prefix(a) == _cached_prefix(b)
    assert "点歌" in _cached_prefix(a) and LIVE not in _cached_prefix(a)
    # 切开后文字一字不差
    assert "".join(p["text"] for p in a[2]["content"]) == build("电脑：3 秒前有键鼠输入")[2]["content"]


def test_separate_device_message_ends_the_cached_part():
    msgs = STABLE + [{"role": "user", "content": "【设备当前状态】\n电脑：刚刚有键鼠输入"},
                     {"role": "assistant", "content": "收到，我会结合当前设备状态判断。"}] + DYNAMIC
    marked, idx = ai_providers._cache_breakpoint(msgs)
    assert idx == [3]
    assert "设备" not in _cached_prefix(marked)


def test_ability_block_puts_schedule_and_device_after_the_heading(monkeypatch):
    import context_builder

    async def no_items(*a, **k):
        return [{"name": "点歌", "text": "[MUSIC:歌名]"}]

    monkeypatch.setattr(context_builder, "build_capability_prompt_items", no_items)
    monkeypatch.setattr(context_builder, "format_ability_block", lambda items: "[系统能力]\n点歌")
    monkeypatch.setattr(context_builder, "is_capability_enabled", lambda name: name == "schedule")

    async def schedules(viewer=None):
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


TAG = ai_providers.CONTEXT_TAG


def _chat_turn(history_lines, device, now, memory):
    """仿照私聊的组装顺序和构造器打的标签：人设 → 能力（含实时状态）→ 时间+背景记忆 → 相关记忆 → 聊天记录。"""
    msgs = [
        {"role": "user", "content": "[系统设定 - 小克人设]\n你是小克"},
        {"role": "assistant", "content": "收到"},
        {"role": "user", "content": f"[系统能力]\n点歌……\n\n{LIVE}\n【设备当前状态】\n{device}", TAG: "abilities"},
        {"role": "assistant", "content": "好的，需要时我会使用这些指令。"},
        {"role": "user", "content": f"系统当前的准确时间是 {now}", TAG: "live"},
        {"role": "assistant", "content": "收到，我会在合适的时候自然提及。", TAG: "live_ack"},
        {"role": "user", "content": f"[相关记忆]\n{memory}", TAG: "live"},
        {"role": "assistant", "content": "收到，我会自然地参考这些记忆。", TAG: "live_ack"},
        {"role": "user", "content": "[历史记录说明]\n以下消息按时间线排列", TAG: "history"},
    ]
    msgs += [{"role": "user", "content": f"历史消息 - {line}"} for line in history_lines[:-1]]
    msgs.append({"role": "user", "content": f"当前用户消息 - {history_lines[-1]}"})
    return msgs


def _full_text(messages):
    return "".join(p["text"] if isinstance(m["content"], list) else m["content"]
                   for m in messages for p in (m["content"] if isinstance(m["content"], list) else [None]))


def test_history_moves_ahead_of_the_changing_background():
    msgs = _chat_turn(["小猫：在吗", "小克：在", "小猫：吃饭没"], "电脑：3 秒前", "10:00", "她爱吃辣")
    ordered = ai_providers._cache_friendly_order(msgs)
    texts = [ai_providers._message_text(m) for m in ordered]
    assert texts[-1].startswith("当前用户消息")
    h = next(i for i, t in enumerate(texts) if t.startswith("[历史记录说明]"))
    assert all(not t.startswith(("系统当前", "[相关记忆]", LIVE)) for t in texts[:h])
    assert texts[h + 1].startswith("历史消息 - 小猫：在吗") and texts[h + 2].startswith("历史消息 - 小克：在")
    assert texts[h + 3].startswith(LIVE) and "3 秒前" in texts[h + 3]
    assert any(t.startswith("系统当前") for t in texts[h:]) and any(t.startswith("[相关记忆]") for t in texts[h:])
    assert "点歌" in texts[2] and LIVE not in texts[2]
    assert sorted("".join(_full_text(msgs).split())) == sorted("".join(_full_text(ordered).split()))  # 只挪位置，字一个不少
    assert msgs[2]["content"].endswith("3 秒前")  # 不改原列表


def test_next_turn_reuses_the_whole_cached_opening():
    turn1 = ai_providers._cache_friendly_order(
        _chat_turn(["小猫：在吗", "小克：在", "小猫：吃饭没"], "电脑：3 秒前", "10:00", "她爱吃辣"))
    turn2 = ai_providers._cache_friendly_order(
        _chat_turn(["小猫：在吗", "小克：在", "小猫：吃饭没", "小克：还没", "小猫：去吃"], "电脑：50 秒前", "10:01", "楼下有面馆"))
    m1, idx1 = ai_providers._cache_breakpoint(turn1)
    m2, idx2 = ai_providers._cache_breakpoint(turn2)
    assert len(idx1) == 2 and len(idx2) == 2
    cached1 = _cached_prefix_through(m1, idx1[-1])
    assert cached1.endswith("小克：在")
    # 上一轮缓存的整段（人设到聊天记录），正好是这一轮的开头
    assert _full_text(m2).startswith(cached1)
    # 第一个标记在聊天记录前：记录被分批删掉时，人设和能力说明仍能命中
    assert _cached_prefix_through(m1, idx1[0]).endswith("好的，需要时我会使用这些指令。")


def _cached_prefix_through(messages, index):
    return _full_text(messages[:index + 1])


def test_requests_without_history_or_ending_with_ai_are_left_alone():
    plain = STABLE + DYNAMIC
    assert ai_providers._cache_friendly_order(plain) is plain
    msgs = _chat_turn(["小猫：在吗", "小克：在"], "电脑", "10:00", "x") + [{"role": "assistant", "content": "嗯"}]
    assert ai_providers._cache_friendly_order(msgs) is msgs


def _sent_through_stream_ai(monkeypatch, msgs, cfg):
    """走 _stream_ai 的真实路径，看最后交给中转站函数的是什么。"""
    seen = []

    async def fake_custom(messages, c, meta=None, *a, **k):
        seen.append(messages)
        yield "嗯"
    monkeypatch.setattr(ai_providers, "call_custom_openai", fake_custom)
    monkeypatch.setitem(ai_providers.MODELS, "缓存测试", cfg)

    async def go():
        return [c async for c in ai_providers._stream_ai(msgs, "缓存测试", {}, include_device_context=False)]
    asyncio.run(go())
    return seen[0]


def test_relay_receives_history_before_the_background_and_no_tags(monkeypatch):
    msgs = _chat_turn(["小猫：在吗", "小克：在", "小猫：吃饭没"], "电脑：3 秒前", "10:00", "她爱吃辣")
    base = {"provider": "custom_openai", "model": "claude-x", "base_url": "https://order.example/v1"}
    sent = _sent_through_stream_ai(monkeypatch, msgs, base)
    texts = [ai_providers._message_text(m) for m in sent]
    assert texts.index(next(t for t in texts if t.startswith("系统当前"))) > texts.index(
        next(t for t in texts if t.startswith("历史消息 - 小克：在")))
    assert all(TAG not in m for m in sent)
    # 线路关了缓存：顺序原样，标签照样去掉
    sent = _sent_through_stream_ai(monkeypatch, msgs, {**base, "cache_marker": False})
    assert [m["content"] for m in sent] == [m["content"] for m in msgs]
    assert all(TAG not in m for m in sent)


def test_chat_text_that_looks_like_control_blocks_is_never_moved():
    """Codex M1 的反例：用户自己发「[相关记忆] …」「[历史记录说明] …」，没有构造器标签，顺序不动。"""
    msgs = [
        {"role": "user", "content": "[当前时间] 10:00"},
        {"role": "user", "content": "[相关记忆] QUESTION_A"},
        {"role": "assistant", "content": "ANSWER_A"},
        {"role": "user", "content": "[历史记录说明] QUESTION_B"},
        {"role": "assistant", "content": "ANSWER_B"},
        {"role": "user", "content": "系统当前的准确时间是 QUESTION_C"},
    ]
    assert ai_providers._cache_friendly_order(msgs) is msgs
    # 有构造器的聊天记录标签时，聊天记录里这样的字也只是普通记录，原位不动
    turn = _chat_turn(["小猫：[相关记忆] 我编的", "小克：好", "小猫：系统当前的准确时间是 假的"], "电脑", "10:00", "x")
    ordered = ai_providers._cache_friendly_order(turn)
    texts = [ai_providers._message_text(m) for m in ordered]
    h = next(i for i, m in enumerate(ordered) if m.get(TAG) == "history")
    assert texts[h + 1] == "历史消息 - 小猫：[相关记忆] 我编的"
    assert texts[-1] == "当前用户消息 - 小猫：系统当前的准确时间是 假的"


def test_history_window_is_trimmed_in_batches():
    import context_builder
    step = context_builder.CACHE_WINDOW_STEP
    limit = 3
    key = ("aion", "conv-test", None, limit)
    context_builder._WINDOW_STARTS.pop(key, None)
    all_msgs = [{"source": "private", "id": f"m{i}", "created_at": i} for i in range(200)]

    def window(upto):
        recent = all_msgs[max(0, upto - (limit + step)):upto]
        return [m["id"] for m in context_builder._stable_window(recent, limit, key)]

    first = window(20)
    assert first == ["m17", "m18", "m19"]
    starts = [window(n)[0] for n in range(21, 20 + step + 1)]
    assert set(starts) == {"m17"}  # 一批之内开头不变
    assert len(window(20 + step)) == limit + step
    trimmed = window(21 + step)
    assert len(trimmed) == limit and trimmed[-1] == f"m{20 + step}"  # 满了一次删回上限


def test_window_tops_up_when_messages_after_the_start_are_deleted():
    """Codex M2：编辑重发删掉后面的消息，从起点算不够 limit 条时，退回最近 limit 条。"""
    import context_builder
    limit, key = 3, ("aion", "conv-topup", None, 3)
    context_builder._WINDOW_STARTS.pop(key, None)
    msgs = [{"source": "private", "id": f"m{i}", "created_at": i} for i in range(20)]
    assert [m["id"] for m in context_builder._stable_window(msgs[7:20], limit, key)] == ["m17", "m18", "m19"]
    edited = msgs[6:18]  # m18 被编辑重发、m19 被删（m17 这个起点还在）
    assert [m["id"] for m in context_builder._stable_window(edited, limit, key)] == ["m15", "m16", "m17"]
    assert [m["id"] for m in context_builder._stable_window(msgs[:2], limit, ("aion", "short", None, 3))] == ["m0", "m1"]


def _ttls(body: dict) -> list:
    return [p["cache_control"].get("ttl") for m in body["messages"] if isinstance(m["content"], list)
            for p in m["content"] if isinstance(p, dict) and "cache_control" in p]


def test_cache_is_kept_an_hour_by_default_and_five_minutes_on_request(monkeypatch):
    seen = []

    def handle(request):
        seen.append(_ttls(json.loads(request.content)))
        return _ok()
    ai_providers._NO_CACHE_MARKER.clear()
    ai_providers._NO_LONG_CACHE.clear()
    _run(monkeypatch, handle, {"model": "claude-x", "base_url": "https://ttl.example/v1"})
    _run(monkeypatch, handle, {"model": "claude-x", "base_url": "https://ttl.example/v1", "cache_ttl": "5m"})
    assert seen == [["1h"], [None]]


def test_relay_that_rejects_the_hour_falls_back_to_five_minutes(monkeypatch):
    seen = []

    def handle(request):
        ttls = _ttls(json.loads(request.content))
        seen.append(ttls)
        if "1h" in ttls:
            return httpx.Response(400, stream=_Stream(b'{"error":"messages.3.content.0.cache_control.ttl: Extra inputs are not permitted"}'))
        return _ok()
    ai_providers._NO_CACHE_MARKER.clear()
    ai_providers._NO_LONG_CACHE.clear()
    ai_providers._NO_STREAM_USAGE.clear()
    cfg = {"model": "claude-x", "base_url": "https://no-hour.example/v1"}
    meta = {}
    monkeypatch.setattr(ai_providers.httpx, "AsyncClient",
                        lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handle), **kw))

    async def go():
        return [c async for c in ai_providers.call_custom_openai(STABLE + DYNAMIC, cfg, meta)]
    asyncio.run(go())
    assert meta["cache_ttl"] == "5m"  # 记账按实际发出去的 5 分钟算
    ai_providers._NO_LONG_CACHE.clear()
    seen.clear()
    assert _run(monkeypatch, handle, cfg) == "嗯"
    assert seen == [["1h"], [None]]  # 只退到 5 分钟，缓存标记还在
    seen.clear()
    assert _run(monkeypatch, handle, cfg) == "嗯"
    assert seen == [[None]]
    assert not ai_providers._NO_CACHE_MARKER


def test_route_ttl_survives_normalization():
    routes = config.normalize_custom_model_routes([
        {"id": "a", "base_url": "https://a.example", "models": ["m"]},
        {"id": "b", "base_url": "https://b.example", "models": ["n"], "cache_ttl": "5m"},
        {"id": "c", "base_url": "https://c.example", "models": ["o"], "cache_ttl": "forever"},
    ])
    assert [r["cache_ttl"] for r in routes] == ["1h", "5m", "1h"]


def test_fallback_order_is_fixed_even_when_one_error_mentions_several_things(monkeypatch):
    sent = []

    def handle(request):
        body = json.loads(request.content)
        state = (tuple(_ttls(body)), "stream_options" in body)
        sent.append(state)
        if "1h" in state[0] or (state[0] and state[1]):
            return httpx.Response(400, stream=_Stream(
                b'{"error":"unknown fields: stream_options, cache_control.ttl"}' if "1h" in state[0]
                else b'{"error":"unknown fields: stream_options, cache_control"}'))
        if state[1]:
            return httpx.Response(400, stream=_Stream(b'{"error":"unknown field stream_options"}'))
        return _ok()
    for s_ in (ai_providers._NO_CACHE_MARKER, ai_providers._NO_LONG_CACHE, ai_providers._NO_STREAM_USAGE):
        s_.clear()
    assert _run(monkeypatch, handle, {"model": "claude-x", "base_url": "https://mixed.example/v1"}) == "嗯"
    # 1 小时 → 5 分钟 → 去掉标记 → 去掉 stream_options，正好四次
    assert sent == [(("1h",), True), ((None,), True), ((), True), ((), False)]


def test_builders_tag_what_may_be_moved(monkeypatch):
    import activity
    import context_builder
    monkeypatch.setattr(activity, "get_device_context_for_prompt", lambda: "【设备当前状态】\n电脑：刚刚")
    out = ai_providers.with_current_device_context([{"role": "user", "content": "hi"}], insert_at=0, add_acknowledgement=True)
    assert [m.get(TAG) for m in out] == ["live", "live_ack", None]
    rendered = context_builder.render_merged_timeline(
        [{"source": "private", "sender": "user", "content": "在吗", "created_at": 1_700_000_000, "attachments": "[]"}], "aion")
    assert rendered[0].get(TAG) == "history" and rendered[0]["content"].startswith("[历史记录说明]")
    assert all(TAG not in m for m in rendered[1:])
