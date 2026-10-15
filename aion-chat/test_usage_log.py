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
    assert s["total"] == {"requests": 1, "prompt": 1200, "completion": 30, "estimated": 0, "cache_read": 0, "cache_write": 0, "cache_write_1h": 0}
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


def test_token_cost_prices_hour_and_five_minute_writes_separately():
    total = {"requests": 10, "prompt": 1_000_000, "completion": 100_000, "cache_read": 600_000,
             "cache_write": 200_000, "cache_write_1h": 100_000}
    # 20 万原价 ×20 + 60 万读缓存 ×2 + 10 万 1 小时写 ×40 + 10 万 5 分钟写 ×25 + 10 万写出 ×100
    assert usage_log.token_cost(total, {"inp": 20, "out": 100, "cache": 2}) == pytest.approx(4 + 1.2 + 4 + 2.5 + 10)
    assert usage_log.token_cost(total, {"inp": 20, "out": 100, "cache": 2, "write": 30}) == pytest.approx(4 + 1.2 + 6 + 10)
    assert usage_log.token_cost(total, {"call": 1}) is None
    assert usage_log.call_cost(total, {"call": 0.5}) == pytest.approx(5)


def test_hour_writes_come_from_the_relay_breakdown_or_the_ttl_actually_sent():
    usage = {"cache_creation": {"ephemeral_5m_input_tokens": 300, "ephemeral_1h_input_tokens": 700}}
    assert usage_log.cache_write_1h(usage, 1000, "5m") == 700  # 明细优先
    assert usage_log.cache_write_1h({}, 1000, "1h") == 1000
    assert usage_log.cache_write_1h({}, 1000, "5m") == 0      # 1 小时被退回 5 分钟
    assert usage_log.cache_write_1h({}, 1000, None) == 0


def _rows(book):
    with sqlite3.connect(book / "usage.db") as c:
        return c.execute("SELECT route, model, cache_write, cache_write_1h FROM usage ORDER BY route").fetchall()


def test_route_is_fixed_when_recorded_so_rebinding_a_model_does_not_rewrite_history(book, monkeypatch):
    monkeypatch.setattr(usage_log, "PRICES_FILE", book / "usage_prices.json")
    import config
    monkeypatch.setitem(config.MODELS, "REVIEW_MODEL", {"provider": "custom_openai", "route_id": "A", "route_name": "甲站"})
    usage_log.record_call("REVIEW_MODEL", "聊天", {"prompt_tokens": 100, "completion_tokens": 1}, "", "")
    usage_log.save_prices({"routes": {"A": {"call": 1}, "B": {"call": 10}}})
    assert usage_log.today_spend(lambda r: r)["cost"] == 1
    monkeypatch.setitem(config.MODELS, "REVIEW_MODEL", {"provider": "custom_openai", "route_id": "B", "route_name": "乙站"})
    assert usage_log.today_spend(lambda r: r)["cost"] == 1  # 改绑不改旧账
    monkeypatch.delitem(config.MODELS, "REVIEW_MODEL")
    assert usage_log.today_spend(lambda r: r)["cost"] == 1  # 删掉也不改
    assert _rows(book)[0][0] == "A"


def test_ttl_actually_sent_is_what_the_write_is_priced_at(book, monkeypatch):
    """Codex M4：配置 1 小时但这次退回 5 分钟，写缓存按 1.25 倍算。"""
    monkeypatch.setattr(usage_log, "PRICES_FILE", book / "usage_prices.json")
    meta = {"prompt_tokens": 1_000_000, "completion_tokens": 0, "cache_ttl": "5m",
            "raw": {"prompt_tokens": 1_000_000, "cache_creation_input_tokens": 1_000_000}}
    usage_log.record_call("测试模型", "聊天", meta, "", "", route="A")
    usage_log.save_prices({"routes": {"A": {"inp": 10, "out": 0}}})
    assert usage_log.today_spend(lambda r: r)["cost"] == pytest.approx(12.5)


def test_old_table_is_migrated_and_old_rows_are_marked_unknown(book, monkeypatch):
    with sqlite3.connect(book / "usage.db") as c:
        c.execute("CREATE TABLE usage (day TEXT, kind TEXT, model TEXT, requests INTEGER, prompt INTEGER, "
                  "completion INTEGER, estimated INTEGER, PRIMARY KEY (day, kind, model))")
        c.execute("INSERT INTO usage VALUES (date('now','localtime'), '聊天', '旧模型', 3, 300, 30, 0)")
    monkeypatch.setattr(usage_log, "PRICES_FILE", book / "usage_prices.json")
    usage_log.record("新模型", "聊天", 10, 1, estimated=False, route="A")
    assert _rows(book) == [("", "旧模型", 0, 0), ("A", "新模型", 0, 0)]
    usage_log.save_prices({"routes": {"A": {"call": 1}, "": {"call": 100}}})
    routes = {r["id"]: r for r in usage_log.route_costs(usage_log.summary(1)["by_route"], usage_log.load_prices(), lambda r: r)}
    assert routes[""]["unknown"] is True and routes[""]["cost"] is None  # 旧记录不计价
    assert routes["A"]["cost"] == 1


def test_each_relay_has_its_own_prices_and_billing():
    by_route = [
        {"name": "a", "requests": 20, "prompt": 2_000_000, "completion": 0, "cache_read": 0, "cache_write": 0, "cache_write_1h": 0},
        {"name": "b", "requests": 4, "prompt": 1_000_000, "completion": 0, "cache_read": 0, "cache_write": 0, "cache_write_1h": 0},
        {"name": "other", "requests": 3, "prompt": 1_000_000, "completion": 0, "cache_read": 0, "cache_write": 0, "cache_write_1h": 0},
    ]
    prices = usage_log._clean_prices({"routes": {
        "a": {"inp": 3, "out": 15, "call": 0.2},                      # 两种都填、没选：按量
        "b": {"inp": 3, "out": 15, "call": 0.1, "billing": "call"},  # 选了按次
    }})
    routes = {r["id"]: r for r in usage_log.route_costs(by_route, prices, lambda r: r.upper())}
    assert routes["a"]["name"] == "A" and routes["a"]["cost"] == pytest.approx(6) and routes["a"]["cost_call"] == pytest.approx(4)
    assert routes["b"]["cost"] == pytest.approx(0.4) and routes["b"]["cost_token"] == pytest.approx(3)
    assert routes["other"]["cost"] is None  # 没填价不算
    assert usage_log.total_cost(list(routes.values())) == pytest.approx(6.4)


def test_prices_are_cleaned_and_saved_on_the_server(book, monkeypatch):
    monkeypatch.setattr(usage_log, "PRICES_FILE", book / "usage_prices.json")
    assert usage_log.load_prices() == {"daily_limit": None, "routes": {}}
    saved = usage_log.save_prices({"daily_limit": True, "routes": {"a": {
        "inp": "15", "out": 75, "cache": -1, "call": float("nan"), "write": 10**400, "billing": "free", "x": 1},
        "b": {"inp": 1e308, "out": 2e6}}, "junk": 1})
    empty = {"call": None, "inp": None, "out": None, "cache": None, "write": None, "billing": ""}
    assert saved == {"daily_limit": None, "routes": {
        "a": {**empty, "inp": 15.0, "out": 75.0}, "b": empty}}
    assert usage_log.load_prices() == saved
    assert usage_log.save_prices({"routes": "bad"}) == {"daily_limit": None, "routes": {}}


def test_huge_but_allowed_numbers_never_crash_the_total():
    import math
    big = {"name": "a", "requests": 10**9, "prompt": 10**15, "completion": 10**15,
           "cache_read": 0, "cache_write": 0, "cache_write_1h": 0}
    prices = usage_log._clean_prices({"routes": {"a": {"inp": 1e6, "out": 1e6, "call": 1e6}}})
    routes = usage_log.route_costs([big], prices, lambda r: r)
    assert all(v is None or math.isfinite(v) for v in (routes[0]["cost"], usage_log.total_cost(routes)))


def test_today_spend_flags_when_over_the_line(book, monkeypatch):
    monkeypatch.setattr(usage_log, "PRICES_FILE", book / "usage_prices.json")
    usage_log.record("测试模型", "聊天", 2_000_000, 0, estimated=False, route="a")
    usage_log.save_prices({"daily_limit": 5, "routes": {"a": {"inp": 1, "out": 1}}})
    assert usage_log.today_spend(lambda r: r) == {"cost": 2.0, "limit": 5.0, "over": False}
    usage_log.save_prices({"daily_limit": 5, "routes": {"a": {"inp": 3, "out": 1}}})
    assert usage_log.today_spend(lambda r: r)["over"] is True
    usage_log.save_prices({"routes": {"a": {"inp": 3, "out": 1}}})
    assert usage_log.today_spend(lambda r: r)["over"] is False  # 没设提醒线就不提醒


def test_price_page_lists_every_relay_and_names_old_ones(book, monkeypatch):
    from routes import usage as usage_routes
    monkeypatch.setattr(usage_log, "PRICES_FILE", book / "usage_prices.json")
    import config
    monkeypatch.setitem(config.MODELS, "甲-claude", {"provider": "custom_openai", "route_id": "a", "route_name": "甲站", "cache_ttl": "1h"})
    monkeypatch.setitem(config.MODELS, "乙-claude", {"provider": "custom_openai", "route_id": "b", "route_name": "乙站", "cache_ttl": "5m"})
    usage_log.save_prices({"routes": {"a": {"inp": 3, "out": 15}}})
    data = asyncio.run(usage_routes.usage_prices())
    rows = {r["id"]: r for r in data["routes"]}
    assert rows["a"]["name"] == "甲站" and rows["a"]["long_cache"] is True and rows["a"]["prices"]["inp"] == 3
    assert rows["b"]["long_cache"] is False and rows["b"]["prices"]["inp"] is None
    assert "other" in rows
    assert usage_routes._route_name("a") == "甲站"
    assert usage_routes._route_name("gone") == "gone（已删除）"
    assert "未知" in usage_routes._route_name("") and "其他" in usage_routes._route_name("other")
    assert usage_log.route_for_model("甲-claude") == "a" and usage_log.route_for_model("哨兵 · x") == "other"
