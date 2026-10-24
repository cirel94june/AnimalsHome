"""线路管理：默认模型由小猫自己选（不再落到不存在的旧默认值上）；Codex 的模型跟着账号自动更新。"""

import asyncio
import json

import pytest

import ai_providers
import codex_models
import config
import routes.settings as settings_routes


@pytest.fixture
def models(monkeypatch, tmp_path):
    table = {
        "Codex-6.1-Sol": {"provider": "codex_cli", "model": "gpt-6.1-sol", "vision": True},
        "红豆沙·claude": {"provider": "custom_openai", "model": "claude", "route_id": "custom_route_1", "route_name": "红豆沙"},
        "狗蛋专属": {"provider": "custom_openai", "model": "gem", "route_id": "seat_ai3", "route_name": "狗蛋"},
        "官Gem": {"provider": "gemini", "model": "gemini-x"},
    }
    monkeypatch.setattr(config, "MODELS", table)
    monkeypatch.setattr(config, "get_key", lambda provider: "")  # 没填 Gemini Key → 官Gem 用不了
    monkeypatch.setattr(ai_providers, "_CODEX_SCRIPT", "/codex.js")
    home = tmp_path / "codex"
    home.mkdir()
    monkeypatch.setattr(ai_providers, "_CODEX_HOME", str(home))
    monkeypatch.setattr(ai_providers, "_CODEX_CHAT_HOME", str(tmp_path / "chat"))
    monkeypatch.setattr(codex_models, "_OWNED", {})
    monkeypatch.setattr(codex_models, "_TRUSTED", None)
    monkeypatch.setattr(codex_models, "_trusted_copy_path", lambda: tmp_path / "trusted.json")
    monkeypatch.setitem(config.SETTINGS, "default_model", "")

    def cache(*slugs):
        (home / "models_cache.json").write_text(json.dumps({"models": [
            {"slug": s, "display_name": s.upper(), "input_modalities": ["text", "image"], "visibility": "list"}
            for s in slugs]}), encoding="utf-8")
    return table, cache


def test_stale_default_resolves_to_her_choice(models):
    table, cache = models
    config.SETTINGS["default_model"] = "红豆沙·claude"
    assert config.resolve_model_key("Gemini-3.5-flash") == "红豆沙·claude"  # 原作者写死、早已不存在的旧默认
    assert config.resolve_model_key("") == "红豆沙·claude"
    assert config.resolve_model_key("官Gem") == "官Gem"  # 还在、只是暂时用不了的不偷偷换


def test_without_a_choice_the_first_usable_shared_model_is_used(models):
    table, cache = models
    assert config.default_model_key() == "红豆沙·claude"  # Codex 只给 Lucien，不当默认
    assert config.default_model_key() != "狗蛋专属"  # 别人的专属线路不拿来当默认
    config.SETTINGS["default_model"] = "狗蛋专属"
    assert config.default_model_key() != "狗蛋专属"
    config.SETTINGS["default_model"] = "官Gem"  # 选了但用不了
    assert config.default_model_key() != "官Gem"


def test_codex_models_follow_the_account(models):
    table, cache = models
    assert config.model_usable(table["Codex-6.1-Sol"])  # 还没有目录时照旧可用
    cache("gpt-6.1-sol", "gpt-7-nova")
    assert codex_models.codex_model_key("gpt-6.1-sol") == "Codex-6.1-Sol"
    assert codex_models.codex_model_key("gpt-7-nova") == "Codex-7-Nova"
    assert codex_models.sync_models() == ["Codex-7-Nova"]  # 写死的不重复加
    assert table["Codex-7-Nova"]["model"] == "gpt-7-nova" and config.model_usable(table["Codex-7-Nova"])
    cache("gpt-7-nova")  # 6.1 下架了
    assert not config.model_usable(table["Codex-6.1-Sol"])
    cache("gpt-6.1-sol")  # 7 下架了：补进来的条目去掉
    codex_models.sync_models()
    assert "Codex-7-Nova" not in table


def test_rebuilding_the_model_list_keeps_codex_models(models, monkeypatch):
    table, cache = models
    cache("gpt-6.1-sol", "gpt-7-nova")
    monkeypatch.setattr(config, "BUILTIN_MODELS", {"Codex-6.1-Sol": table["Codex-6.1-Sol"]})
    monkeypatch.setattr(config, "ANTIGRAVITY_MODELS", {})
    monkeypatch.setitem(config.SETTINGS, "custom_model_routes", [])
    monkeypatch.setitem(config.SETTINGS, config.SEAT_ROUTES_KEY, [])
    monkeypatch.setattr(config, "save_settings", lambda *a, **k: None)
    config.refresh_custom_models()
    assert "Codex-7-Nova" in config.MODELS  # 选过它的 AI 不会被悄悄换成默认


def test_settings_default_model_validation(models, monkeypatch):
    table, cache = models
    monkeypatch.setattr(settings_routes, "save_settings", lambda *a, **k: None)
    monkeypatch.setattr(settings_routes, "iter_selectable_models",
                        lambda: [(k, v) for k, v in table.items() if config.model_usable(v)])
    asyncio.run(settings_routes.update_settings(settings_routes.SettingsUpdate(default_model="红豆沙·claude")))
    assert config.SETTINGS["default_model"] == "红豆沙·claude"
    from fastapi import HTTPException
    for bad in ("狗蛋专属", "官Gem", "不存在"):
        with pytest.raises(HTTPException):
            asyncio.run(settings_routes.update_settings(settings_routes.SettingsUpdate(default_model=bad)))
    assert config.SETTINGS["default_model"] == "红豆沙·claude"


def test_codex_without_model_name_uses_codex_default(models):
    table, cache = models
    cache("gpt-7-nova")
    assert config.model_usable({"provider": "codex_cli"})  # 没写模型名：用 Codex 自己的默认
    assert not config.model_usable({"provider": "codex_cli", "model": "gpt-1-old"})


def test_unreadable_cache_keeps_the_last_trusted_catalog(models):
    """§66 复审 M2：缓存坏了不等于模型下架；明确的空目录才算都没了。"""
    table, cache = models
    cache("gpt-7-nova")
    codex_models.sync_models()
    home = __import__("pathlib").Path(__import__("ai_providers")._CODEX_HOME)
    (home / "models_cache.json").write_text("{broken", encoding="utf-8")
    table.pop("Codex-7-Nova")  # 模拟 MODELS 重建
    codex_models.sync_models()
    assert table["Codex-7-Nova"]["model"] == "gpt-7-nova"
    (home / "models_cache.json").write_text(json.dumps({"models": []}), encoding="utf-8")
    codex_models.sync_models()
    assert "Codex-7-Nova" not in table and codex_models.available_slugs() == set()


def test_user_route_with_same_name_is_never_overwritten(models):
    """§66 复审 M1：同名的自定义线路归小猫，Codex 同步不覆盖、不删除。"""
    table, cache = models
    cache("gpt-7-nova")
    codex_models.sync_models()
    mine = {"provider": "custom_openai", "model": "MY_OWN", "route_id": "custom_route_9"}
    table["Codex-7-Nova"] = mine  # 重建后同名位置是小猫自己的线路
    codex_models.sync_models()
    assert table["Codex-7-Nova"] is mine
    cache("gpt-6.1-sol")
    codex_models.sync_models()
    assert table["Codex-7-Nova"] is mine


@pytest.mark.parametrize("broken", [
    {"models": [None]}, {"models": [{}]}, {"models": [{"slug": ""}]},
    {"models": [{"slug": "gpt-x", "input_modalities": 42}]}, {"models": "nope"}, [],
])
def test_structurally_broken_cache_counts_as_unreadable(models, broken):
    """§67 复审 R1：结构或字段类型不对的目录不算「空目录」，用上一份可信的，也不覆盖可信副本。"""
    table, cache = models
    cache("gpt-7-nova")
    codex_models.sync_models()
    home = __import__("pathlib").Path(__import__("ai_providers")._CODEX_HOME)
    (home / "models_cache.json").write_text(json.dumps(broken), encoding="utf-8")
    codex_models.sync_models()
    assert table["Codex-7-Nova"]["model"] == "gpt-7-nova"
    codex_models._TRUSTED = None  # 模拟重启：从副本恢复
    assert codex_models.available_slugs() == {"gpt-7-nova"}


def test_refresh_skips_the_blocking_catalog_step(monkeypatch):
    """§67 复审 R2：刷新只问模型列表，不跑会卡住主循环的陪聊目录准备。"""
    import ai_providers
    seen = {}

    def env(*a, **k):
        seen.update(k)
        raise RuntimeError("stop here")
    monkeypatch.setattr(ai_providers, "_CODEX_SCRIPT", "/codex.js")
    monkeypatch.setattr(ai_providers, "_build_codex_chat_environment", env)
    with pytest.raises(RuntimeError, match="stop here"):
        asyncio.run(codex_models.refresh(timeout=1))
    assert seen == {"prepare_catalog": False}


def test_codex_never_lands_on_xiaoke(models):
    """2026-10-24 小猫：小克私聊落到了 Codex，GPT 顶着小克的人设说话。Codex 只给 Lucien。"""
    table, cache = models
    assert not config.is_shared_model("Codex-6.1-Sol")
    assert config.aion_model_key("Codex-6.1-Sol") == "红豆沙·claude"  # 以前存过的也换掉
    assert config.aion_model_key("红豆沙·claude") == "红豆沙·claude"
    config.SETTINGS["default_model"] = "Codex-6.1-Sol"
    assert config.default_model_key() == "红豆沙·claude"
    table.pop("红豆沙·claude")
    assert config.default_model_key() == ""  # 没有别的线路：不拿 Codex 顶替
    assert config.aion_model_key("Codex-6.1-Sol") == ""


def test_no_route_gives_a_clear_hint(models):
    table, cache = models
    table.pop("红豆沙·claude")

    async def collect():
        return [c async for c in ai_providers._stream_ai([{"role": "user", "content": "hi"}], "",
                                                         include_device_context=False)]
    chunks = asyncio.run(collect())
    assert chunks and "模型线路" in chunks[0] and "Lucien" in chunks[0]


def test_refresh_deadline_covers_process_start(monkeypatch):
    """§72 修复审 L1：启动子进程也算在时限里；启动迟到的进程事后照样收掉。"""
    import ai_providers
    import time as _time
    stopped = []

    class Proc:
        returncode = None

    async def slow_spawn(*a, **k):
        await asyncio.sleep(0.2)
        return Proc()

    async def fake_stop(process):
        stopped.append(process)
    monkeypatch.setattr(ai_providers, "_CODEX_SCRIPT", "/codex.js")
    monkeypatch.setattr(ai_providers, "_build_codex_chat_environment", lambda *a, **k: {})
    monkeypatch.setattr(asyncio, "create_subprocess_exec", slow_spawn)
    monkeypatch.setattr(codex_models, "_stop", fake_stop)

    async def run():
        started = _time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await codex_models.refresh(timeout=0.02)
        elapsed = _time.monotonic() - started
        await asyncio.sleep(0.3)  # 迟到的进程启动起来后被收掉
        return elapsed
    assert asyncio.run(run()) < 0.15
    assert len(stopped) == 1
