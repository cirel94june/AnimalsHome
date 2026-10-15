import asyncio
import sqlite3

import pytest

import ai_providers
import usage_log


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_log, "USAGE_DB", tmp_path / "usage.db")
    monkeypatch.setattr(usage_log, "_last_prune", "")
    monkeypatch.setitem(ai_providers.MODELS, "测试模型", {"provider": "custom_openai", "model": "m"})
    return tmp_path


def _fake_provider(monkeypatch, *, usage=None, text="好的呀"):
    async def fake(messages, model_key, meta=None, *args, **kwargs):
        if usage:
            meta.update(usage)
        yield text
    monkeypatch.setattr(ai_providers, "_stream_ai", fake)


async def _drain(gen):
    return "".join([c async for c in gen])


def test_real_usage_is_recorded_under_the_calling_feature(book, monkeypatch):
    _fake_provider(monkeypatch, usage={"prompt_tokens": 1200, "completion_tokens": 30})
    assert asyncio.run(_drain(ai_providers.stream_ai([{"role": "user", "content": "嗨"}], "测试模型"))) == "好的呀"
    s = usage_log.summary(1)
    assert s["total"] == {"requests": 1, "prompt": 1200, "completion": 30, "estimated": 0, "cache_read": 0, "cache_write": 0}
    # 从测试模块调用，归到「其他（模块名）」
    assert s["by_kind"][0]["name"] == f"其他（{__name__}）"
    assert s["by_model"][0]["name"] == "测试模型"


def test_missing_usage_is_estimated_and_marked(book, monkeypatch):
    _fake_provider(monkeypatch, text="abcd")
    asyncio.run(_drain(ai_providers.stream_ai([{"role": "user", "content": [{"type": "text", "text": "你好世界"}]}], "测试模型")))
    t = usage_log.summary(1)["total"]
    assert (t["prompt"], t["completion"], t["estimated"]) == (4, 1, 1)


def test_reused_meta_with_identical_real_numbers_counts_both_as_real(book, monkeypatch):
    """同一个 meta 两次真实用量都是 100/10：两次都是真实数字（Codex 复审 M2 的反例）。"""
    _fake_provider(monkeypatch, usage={"prompt_tokens": 100, "completion_tokens": 10,
                                       "raw": {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 60}}})
    meta = {}
    for _ in range(2):
        asyncio.run(_drain(ai_providers.stream_ai([{"role": "user", "content": "x"}], "测试模型", meta)))
    t = usage_log.summary(1)["total"]
    assert (t["requests"], t["prompt"], t["estimated"], t["cache_read"]) == (2, 200, 0, 120)


def test_closing_early_waits_for_the_provider_to_clean_up(book, monkeypatch):
    """提前关闭外层时，下层的清理先跑完，再记账、再返回（Codex 复审 M1）。"""
    order = []

    async def provider(messages, model_key, meta=None, *a, **k):
        try:
            yield "一"
            yield "二"
        finally:
            await asyncio.sleep(0)
            order.append("provider closed")
    monkeypatch.setattr(ai_providers, "_stream_ai", provider)
    monkeypatch.setattr(usage_log, "record_call", lambda *a, **k: order.append("recorded"))

    async def run():
        gen = ai_providers.stream_ai([{"role": "user", "content": "x"}], "测试模型")
        await gen.__anext__()
        await gen.aclose()
        order.append("returned")
    asyncio.run(run())
    assert order == ["provider closed", "recorded", "returned"]


def test_reused_meta_does_not_count_the_previous_call_again(book, monkeypatch):
    meta = {"prompt_tokens": 999, "completion_tokens": 9}
    _fake_provider(monkeypatch, text="ab")  # 这次中转站没给用量
    asyncio.run(_drain(ai_providers.stream_ai([{"role": "user", "content": "嗯"}], "测试模型", meta)))
    t = usage_log.summary(1)["total"]
    assert t["prompt"] == 1 and t["estimated"] == 1


def test_cancelled_stream_is_still_counted_once(book, monkeypatch):
    _fake_provider(monkeypatch, usage={"prompt_tokens": 50, "completion_tokens": 5})

    async def first_chunk_only():
        gen = ai_providers.stream_ai([{"role": "user", "content": "x"}], "测试模型")
        async for _ in gen:
            break
        await gen.aclose()
    asyncio.run(first_chunk_only())
    assert usage_log.summary(1)["total"]["requests"] == 1


def test_unknown_model_is_counted_under_the_model_actually_used(book, monkeypatch):
    _fake_provider(monkeypatch)
    asyncio.run(_drain(ai_providers.stream_ai([], "不存在的模型")))
    used = ai_providers.resolve_model_key("不存在的模型")
    assert [m["name"] for m in usage_log.summary(1)["by_model"]] == [used]


def test_simple_ai_call_counts_under_its_real_caller(book, monkeypatch):
    _fake_provider(monkeypatch, usage={"prompt_tokens": 10, "completion_tokens": 2})
    monkeypatch.setattr(ai_providers, "_save_raw_trace", lambda *a, **k: None, raising=False)
    async def feature():  # 真实代码都是在自己的协程里 await 它
        return await ai_providers.simple_ai_call([{"role": "user", "content": "x"}], "测试模型")
    asyncio.run(feature())
    assert usage_log.summary(1)["by_kind"][0]["name"] == f"其他（{__name__}）"


def test_module_kinds_and_json_usage_shapes(book):
    assert usage_log.kind_for_module("autonomy") == "自主活动"
    assert usage_log.kind_for_module("memory_hub_jobs") == "做梦 / 交接卡"
    usage_log.record_json("哨兵 · g", "哨兵（后台小模型）", {"usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3}}, "", "")
    usage_log.record_json("x", "出门逛", {"usage": {"prompt_tokens": 5, "completion_tokens": 1}}, "", "")
    usage_log.record_json("x", "出门逛", {}, "一二三", "")
    kinds = {k["name"]: k for k in usage_log.summary(1)["by_kind"]}
    assert kinds["哨兵（后台小模型）"]["prompt"] == 7
    assert kinds["出门逛"]["requests"] == 2 and kinds["出门逛"]["estimated"] == 1


def test_bookkeeping_failure_never_breaks_chat(book, monkeypatch):
    monkeypatch.setattr(usage_log, "USAGE_DB", book / "no" / "such" / "dir" / "usage.db")
    _fake_provider(monkeypatch)
    assert asyncio.run(_drain(ai_providers.stream_ai([], "测试模型"))) == "好的呀"


def test_old_days_are_pruned(book):
    conn = sqlite3.connect(usage_log.USAGE_DB)
    usage_log._connect().close()
    conn.execute("INSERT INTO usage (day, kind, model, requests, prompt, completion, estimated) VALUES ('2000-01-01','聊天','x',1,1,1,0)")
    conn.commit()
    conn.close()
    usage_log.record("x", "聊天", 1, 1, estimated=False)
    conn = sqlite3.connect(usage_log.USAGE_DB)
    assert conn.execute("SELECT COUNT(*) FROM usage WHERE day='2000-01-01'").fetchone()[0] == 0
    conn.close()


class _TrackedBody:
    """模拟中转站的流式正文：给一块后一直等；记录它什么时候被关掉。"""

    def __init__(self, order):
        self.order = order

    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0"}}]}\n\n'
        await asyncio.sleep(3600)
        yield b""

    async def aclose(self):
        self.order.append("http closed")


def _real_chain(monkeypatch, order):
    import httpx
    real_client = httpx.AsyncClient

    def handle(request):
        return httpx.Response(200, stream=_Wrapped(_TrackedBody(order)))
    monkeypatch.setattr(ai_providers.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    monkeypatch.setitem(ai_providers.MODELS, "真实链", {"provider": "custom_openai", "model": "m",
                                                      "base_url": "https://relay.test/v1"})
    monkeypatch.setattr(usage_log, "record_call", lambda *a, **k: order.append("recorded"))


import httpx as _httpx


class _Wrapped(_httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body

    def __aiter__(self):
        return self.body.__aiter__()

    async def aclose(self):
        await self.body.aclose()


def test_closing_early_closes_the_real_http_response_first(book, monkeypatch):
    """真实调用链：外层提前关闭时 HTTP 响应先关掉，再记账，再返回（Codex 复查 M1）。"""
    import httpx
    order = []
    _real_chain(monkeypatch, order)

    async def run():
        gen = ai_providers.stream_ai([{"role": "user", "content": "x"}], "真实链", include_device_context=False)
        assert await gen.__anext__() == "你"
        await gen.aclose()
        order.append("returned")
        await gen.aclose()  # 重复关闭不再记账
    asyncio.run(run())
    assert order == ["http closed", "recorded", "returned"]


def test_cancelling_the_task_also_closes_the_http_response(book, monkeypatch):
    order = []
    _real_chain(monkeypatch, order)

    async def consume():
        async for _ in ai_providers.stream_ai([{"role": "user", "content": "x"}], "真实链", include_device_context=False):
            pass

    async def run():
        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        order.append("returned")
    asyncio.run(run())
    assert order == ["http closed", "recorded", "returned"]


def test_cost_uses_token_prices_and_cheaper_cache_reads():
    total = {"requests": 10, "prompt": 1_000_000, "completion": 100_000, "cache_read": 800_000}
    prices = {"inp": 20, "out": 100, "cache": 2, "call": 0.5}
    # 20 万原价读入 ×20 + 80 万缓存 ×2 + 10 万写出 ×100（元/百万）
    assert usage_log.estimate_cost(total, prices) == pytest.approx(4 + 1.6 + 10)
    assert usage_log.estimate_cost(total, {"inp": 20, "out": 100}) == pytest.approx(20 + 10)  # 没填缓存价按读入价
    assert usage_log.estimate_cost(total, {"call": 0.5}) == pytest.approx(5)
    assert usage_log.estimate_cost(total, {}) is None


def test_prices_live_on_the_server_and_bad_values_are_dropped(book, monkeypatch):
    monkeypatch.setattr(usage_log, "PRICES_FILE", book / "usage_prices.json")
    assert usage_log.load_prices() == {k: None for k in usage_log.PRICE_FIELDS}
    saved = usage_log.save_prices({"inp": "15", "out": 75, "cache": -1, "daily_limit": "abc", "call": float("nan"), "x": 1})
    assert saved == {"call": None, "inp": 15.0, "out": 75.0, "cache": None, "daily_limit": None}
    assert usage_log.load_prices() == saved


def test_today_spend_flags_when_over_the_line(book, monkeypatch):
    monkeypatch.setattr(usage_log, "PRICES_FILE", book / "usage_prices.json")
    usage_log.record("测试模型", "聊天", 2_000_000, 0, estimated=False)
    usage_log.save_prices({"inp": 1, "out": 1, "daily_limit": 5})
    assert usage_log.today_spend() == {"cost": 2.0, "limit": 5.0, "over": False}
    usage_log.save_prices({"inp": 3, "out": 1, "daily_limit": 5})
    assert usage_log.today_spend()["over"] is True
    usage_log.save_prices({"inp": 3, "out": 1})
    assert usage_log.today_spend()["over"] is False  # 没设提醒线就不提醒
