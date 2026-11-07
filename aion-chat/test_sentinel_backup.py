"""哨兵备用线路（11-07 小猫：怕 Codex 没额度时哨兵就停了，想接中转模型）。

平时走 Codex；Codex 出错、没回话时这一次改用设置里选的中转线路模型。原线路照旧不受影响。
"""
import asyncio
from pathlib import Path

import pytest

import config
import memory
import post_sentinel

CODEX = {"provider": "codex", "model": "gpt-5.6-luna", "ready": True}
RELAY = {"provider": "custom_openai", "model": "qwen-vl", "base_url": "https://relay.example/v1/", "api_key": "RELAY_KEY",
         "route_name": "小中转"}


@pytest.fixture
def backup(monkeypatch):
    monkeypatch.setitem(config.MODELS, "小中转·qwen-vl", RELAY)
    monkeypatch.setitem(config.SETTINGS, "sentinel_backup_model", "小中转·qwen-vl")


def test_backup_config_comes_from_a_saved_relay_line(backup):
    cfg = config.get_sentinel_backup_config()
    assert cfg == {"route": "backup", "provider": "openai_compatible", "base_url": "https://relay.example/v1",
                   "api_key": "RELAY_KEY", "model": "qwen-vl", "use_openai": True, "ready": True,
                   "label": "小中转·qwen-vl"}


@pytest.mark.parametrize("key,entry", [
    ("", None), ("没了", None), ("codex-x", {"provider": "codex_cli", "model": "gpt"}),
    ("缺key", {"provider": "custom_openai", "model": "m", "base_url": "https://r.example", "api_key": ""}),
])
def test_no_usable_backup(monkeypatch, key, entry):
    if entry:
        monkeypatch.setitem(config.MODELS, key, entry)
    monkeypatch.setitem(config.SETTINGS, "sentinel_backup_model", key)
    assert config.get_sentinel_backup_config() is None


def test_chat_url_with_or_without_v1():
    assert memory._chat_url({"base_url": "https://relay.example/v1"}) == "https://relay.example/v1/chat/completions"
    assert memory._chat_url({"base_url": "https://api.siliconflow.cn"}) == "https://api.siliconflow.cn/v1/chat/completions"


def _fake_once(monkeypatch, codex_result):
    calls = []

    async def once(cfg, prompt, timeout, use_chat_slot):
        calls.append((cfg.get("provider"), cfg.get("model"), use_chat_slot))
        if cfg.get("provider") == "codex":
            if isinstance(codex_result, BaseException):
                raise codex_result
            return codex_result
        return "备用的回答"
    monkeypatch.setattr(memory, "_sentinel_text_once", once)
    return calls


@pytest.mark.parametrize("codex_result", [RuntimeError("usage limit reached"), "", "   ", None])
def test_codex_trouble_uses_backup(backup, monkeypatch, codex_result):
    calls = _fake_once(monkeypatch, codex_result)
    assert asyncio.run(memory._call_sentinel_text(CODEX, "看看")) == "备用的回答"
    assert [c[:2] for c in calls] == [("codex", "gpt-5.6-luna"), ("openai_compatible", "qwen-vl")]


def test_codex_fine_no_backup(backup, monkeypatch):
    calls = _fake_once(monkeypatch, "Codex 的回答")
    assert asyncio.run(memory._call_sentinel_text(CODEX, "看看")) == "Codex 的回答"
    assert len(calls) == 1


def test_without_backup_codex_error_still_raises(monkeypatch):
    monkeypatch.setitem(config.SETTINGS, "sentinel_backup_model", "")
    _fake_once(monkeypatch, RuntimeError("usage limit reached"))
    with pytest.raises(RuntimeError, match="usage limit"):
        asyncio.run(memory._call_sentinel_text(CODEX, "看看"))


def test_original_route_is_not_rerouted(backup, monkeypatch):
    calls = []

    async def once(cfg, prompt, timeout, use_chat_slot):
        calls.append(cfg["provider"])
        raise RuntimeError("relay down")
    monkeypatch.setattr(memory, "_sentinel_text_once", once)
    with pytest.raises(RuntimeError):
        asyncio.run(memory._call_sentinel_text({"provider": "openai_compatible", "use_openai": True}, "看看"))
    assert calls == ["openai_compatible"]


def test_cancel_is_not_swallowed(backup, monkeypatch):
    _fake_once(monkeypatch, asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(memory._call_sentinel_text(CODEX, "看看"))


def test_vision_uses_backup_too(backup, monkeypatch):
    calls = []

    async def once(cfg, prompt, img, mime, timeout):
        calls.append(cfg["provider"])
        if cfg["provider"] == "codex":
            raise RuntimeError("quota")
        return f"看到了 {img} {mime}"
    monkeypatch.setattr(memory, "_sentinel_vision_once", once)
    assert asyncio.run(memory._call_sentinel_vision(CODEX, "看图", "IMG", "image/png")) == "看到了 IMG image/png"
    assert calls == ["codex", "openai_compatible"]


def test_post_sentinel_goes_through_the_same_entry(backup, monkeypatch):
    calls = _fake_once(monkeypatch, RuntimeError("quota"))
    monkeypatch.setattr(post_sentinel, "get_sentinel_config", lambda: CODEX)
    monkeypatch.setattr(memory, "_sentinel_text_once", memory._sentinel_text_once)  # 已经替换成假的
    try:
        asyncio.run(post_sentinel._call_model("提示"))
    except ValueError:
        pass  # 「备用的回答」不是 JSON，解析失败是预期的；这里只看走没走备用
    assert calls == [("codex", "gpt-5.6-luna", False), ("openai_compatible", "qwen-vl", False)]  # 不占聊天名额


def test_backup_relay_request_shape(backup, monkeypatch):
    sent = {}

    class Response:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": " 好的 "}}]}

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            sent.update(url=url, model=json["model"], auth=headers["Authorization"])
            return Response()
    monkeypatch.setattr(memory.httpx, "AsyncClient", Client)
    monkeypatch.setattr(memory, "_count_sentinel", lambda *a: None)
    text = asyncio.run(memory._sentinel_text_once(config.get_sentinel_backup_config(), "看看", 10, True))
    assert text == "好的"
    assert sent == {"url": "https://relay.example/v1/chat/completions", "model": "qwen-vl", "auth": "Bearer RELAY_KEY"}


def test_settings_page_and_api_wired():
    root = Path(__file__).parent
    page = (root / "static" / "settings.html").read_text(encoding="utf-8")
    assert 'id="sentinelBackupInput"' in page and "sentinel_backup_model: $(\"sentinelBackupInput\").value" in page
    assert "fetchSentinelModels(this)" in page and "/api/custom-routes/models" in page
    api = (root / "routes" / "settings.py").read_text(encoding="utf-8")
    assert 'SETTINGS["sentinel_backup_model"]' in api and '"sentinel_backup_model": SETTINGS.get' in api
