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
