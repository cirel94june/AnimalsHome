import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import outing_tools  # noqa: E402

KEY = "k" * 32


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(outing_tools, "CONFIG_PATH", tmp_path / "outing_tools.json")
    monkeypatch.setattr(outing_tools, "DB_PATH", tmp_path / "outing_tools.db")
    monkeypatch.setattr(outing_tools, "_running", {})
    monkeypatch.setattr(outing_tools, "_running_total", 0)
    monkeypatch.setenv("AIONSHOME_OUTING_KEY_AION", KEY)
    monkeypatch.delenv("AIONSHOME_OUTING_KEY_CONNOR", raising=False)
    calls = []

    async def fake_tool(tool, args):
        calls.append((tool, args))
        if args.get("query") == "boom":
            raise RuntimeError("Error code: 401 invalid api key sk-secret123456")
        if args.get("query") == "slow":
            await asyncio.sleep(5)
        return "结果" * 2000
    monkeypatch.setattr(outing_tools, "run_tool", fake_tool)
    monkeypatch.setattr(outing_tools, "TOTAL_TIMEOUT_SECONDS", 0.5)
    app = FastAPI()
    app.include_router(outing_tools.router)
    return TestClient(app), calls


def _post(client, rid="req-00000001", key=KEY, **body):
    payload = {"tool": "search", "args": {"query": "天气"}, "request_id": rid, "chat_type": "private", **body}
    return client.post("/api/outing/tool", json=payload, headers={"Authorization": f"Bearer {key}"})


def test_borrow_with_the_right_key(env):
    client, calls = env
    res = _post(client)
    assert res.status_code == 200 and res.json()["ok"] and len(res.json()["result"]) == outing_tools.MAX_RESULT_CHARS
    assert calls == [("search", {"query": "天气"})]
    row = outing_tools.recent_calls()[0]
    assert row["actor"] == "aion" and row["status"] == "ok" and "天气" not in str(row)  # 日志不记搜索词


@pytest.mark.parametrize("key", ["", "wrong" * 8, "k" * 31])
def test_wrong_or_missing_key_is_refused(env, key):
    client, calls = env
    assert _post(client, key=key).status_code == 401 and calls == []


def test_short_configured_key_counts_as_not_set(env, monkeypatch):
    client, _ = env
    monkeypatch.setenv("AIONSHOME_OUTING_KEY_CONNOR", "short")
    assert _post(client, key="short").status_code == 401


def test_same_request_id_only_runs_once(env):
    client, calls = env
    assert _post(client, rid="req-dup-0001").status_code == 200
    assert _post(client, rid="req-dup-0001").status_code == 409
    assert len(calls) == 1


def test_hourly_quota_is_claimed_before_work(env):
    client, calls = env
    outing_tools.save_settings({"bots": {"aion": {"per_hour": 2}}})
    assert [_post(client, rid=f"req-quota-{i:04d}").status_code for i in range(3)] == [200, 200, 429]
    assert len(calls) == 2


def test_public_group_needs_her_to_ask(env):
    client, calls = env
    assert _post(client, chat_type="public_group").status_code == 403
    assert _post(client, rid="req-asked-0001", chat_type="public_group", owner_asked=True).status_code == 200
    assert len(calls) == 1


def test_switches_and_tool_list(env):
    client, calls = env
    outing_tools.save_settings({"bots": {"aion": {"tools": ["read"]}}})
    assert _post(client).status_code == 403
    outing_tools.save_settings({"enabled": False, "bots": {"aion": {"tools": ["search"]}}})
    assert _post(client, rid="req-off-0001").status_code == 403
    assert calls == []
    with pytest.raises(ValueError):
        outing_tools.save_settings({"bots": {"aion": {"tools": ["camera"]}}})


def test_failures_only_return_a_category(env):
    client, _ = env
    res = _post(client, args={"query": "boom"})
    assert res.json() == {"ok": False, "reason": "线路拒绝了请求（钥匙无效、没权限或余额不足）"}
    assert "secret" not in str(outing_tools.recent_calls())


def test_timeout_is_reported_and_slot_released(env):
    client, _ = env
    assert _post(client, args={"query": "slow"}).json()["ok"] is False
    assert outing_tools.recent_calls()[0]["status"] == "timeout"
    assert outing_tools._running_total == 0


def test_concurrency_caps(env, monkeypatch):
    client, _ = env
    monkeypatch.setattr(outing_tools, "_running", {"aion": outing_tools.PER_BOT_CONCURRENCY})
    assert _post(client).status_code == 429
    monkeypatch.setattr(outing_tools, "_running", {})
    monkeypatch.setattr(outing_tools, "_running_total", outing_tools.GLOBAL_CONCURRENCY)
    assert _post(client, rid="req-busy-0002").status_code == 429
    assert outing_tools.recent_calls() == []  # 没认领额度


@pytest.mark.parametrize("bad", [{"tool": "camera"}, {"request_id": "x"}, {"chat_type": "dm"}])
def test_bad_requests(env, bad):
    client, calls = env
    assert _post(client, **bad).status_code == 400 and calls == []


def test_real_tools_format_results(monkeypatch):
    import taobao_shopping
    import taobao_union
    import web_search

    async def fake_search(query, **kw):
        return {"results": [{"title": "上海天气", "url": "https://w.example/sh", "content": "晴，18 度"}]}

    async def fake_extract(url, **kw):
        return {"results": [{"url": url, "raw_content": "正文内容"}]}

    async def fake_union(keyword, **kw):
        return {"products": [{"title": "猫抓板", "price": "19.9", "shop": "猫店", "url": "https://item.taobao.com/item.htm?id=1"}],
                "skipped": 0}

    class Store:
        async def settings(self):
            return {"union_app_key": "abc", "union_adzone_id": "123"}

    async def get_store():
        return Store()
    monkeypatch.setattr(web_search, "tavily_search", fake_search)
    monkeypatch.setattr(web_search, "tavily_extract", fake_extract)
    monkeypatch.setattr(taobao_union, "search", fake_union)
    monkeypatch.setattr(taobao_shopping, "get_store", get_store)
    assert "晴，18 度" in asyncio.run(outing_tools.run_tool("search", {"query": "上海天气"}))
    assert "正文内容" in asyncio.run(outing_tools.run_tool("read", {"url": "https://w.example/sh"}))
    assert asyncio.run(outing_tools.run_tool("taobao", {"keyword": "猫抓板"})) \
        == "- 猫抓板｜19.9｜猫店｜https://item.taobao.com/item.htm?id=1"
    # 狐狸 §168 Low：放宽过的词要告诉 bot
    async def broadened(keyword, **kw):
        return {"products": [{"title": "仓鼠笼", "price": "9", "shop": "店", "url": "https://item.taobao.com/item.htm?id=2"}],
                "skipped": 0, "keyword_used": "仓鼠"}
    monkeypatch.setattr(taobao_union, "search_broadening", broadened)
    out = asyncio.run(outing_tools.run_tool("taobao", {"keyword": "仓鼠 磁吸"}))
    assert out.startswith("原词「仓鼠 磁吸」没搜到，下面是放宽成「仓鼠」搜到的")
    with pytest.raises(outing_tools.OutingRefused):
        asyncio.run(outing_tools.run_tool("read", {"url": "file:///etc/passwd"}))
