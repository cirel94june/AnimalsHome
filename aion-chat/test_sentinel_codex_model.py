"""哨兵走 Codex 登录态时模型可以自己选（小猫 2026-10-22：免得以后模型下架没法换）。"""

import asyncio
import json

import pytest

import ai_providers
import config
import routes.settings as settings_routes


def test_model_name_is_checked_and_defaults_to_luna():
    assert config.normalize_sentinel_codex_model("gpt-6-luna") == "gpt-6-luna"
    assert config.normalize_sentinel_codex_model("") == "gpt-5.6-luna"
    assert config.normalize_sentinel_codex_model("bad model; rm -rf") == "gpt-5.6-luna"
    assert config.normalize_sentinel_codex_model("x" * 200) == "gpt-5.6-luna"


def test_sentinel_config_uses_the_chosen_codex_model(monkeypatch):
    monkeypatch.setitem(config.SETTINGS, "sentinel_route", "codex_luna")
    monkeypatch.setitem(config.SETTINGS, "sentinel_codex_model", "gpt-6-luna")
    cfg = config.get_sentinel_config()
    assert cfg["route"] == "codex_luna" and cfg["model"] == "gpt-6-luna" and cfg["ready"] is True
    monkeypatch.setitem(config.SETTINGS, "sentinel_codex_model", "")
    assert config.get_sentinel_config()["model"] == "gpt-5.6-luna"  # 以前没选过的照旧


def test_codex_model_list_comes_from_the_codex_cache(monkeypatch, tmp_path):
    home, chat_home = tmp_path / "codex", tmp_path / "chat"
    home.mkdir()
    monkeypatch.setattr(ai_providers, "_CODEX_HOME", str(home))
    monkeypatch.setattr(ai_providers, "_CODEX_CHAT_HOME", str(chat_home))
    assert settings_routes.list_codex_models() == []  # 还没登录 / 没缓存
    (home / "models_cache.json").write_text(json.dumps({"models": [
        {"slug": "gpt-6-luna", "display_name": "GPT-6-Luna", "input_modalities": ["text", "image"], "visibility": "list"},
        {"slug": "gpt-reserve", "display_name": "GPT-Reserve", "input_modalities": ["text"], "visibility": "hide"},
        {"slug": "text-only", "display_name": "Text Only", "input_modalities": ["text"], "visibility": "list"},
    ]}), encoding="utf-8")
    assert settings_routes.list_codex_models() == [
        {"id": "gpt-6-luna", "name": "GPT-6-Luna", "vision": True},
        {"id": "text-only", "name": "Text Only", "vision": False},
    ]
    (home / "models_cache.json").write_text("not json", encoding="utf-8")
    assert settings_routes.list_codex_models() == []


def test_settings_save_and_read_the_codex_model(monkeypatch):
    saved = []
    monkeypatch.setattr(settings_routes, "save_settings", lambda *a, **k: saved.append(1))
    monkeypatch.setitem(config.SETTINGS, "sentinel_codex_model", "")
    asyncio.run(settings_routes.update_settings(settings_routes.SettingsUpdate(sentinel_codex_model="gpt-6-luna")))
    assert config.SETTINGS["sentinel_codex_model"] == "gpt-6-luna"
    asyncio.run(settings_routes.update_settings(settings_routes.SettingsUpdate(sentinel_codex_model="坏名字 !!")))
    assert config.SETTINGS["sentinel_codex_model"] == "gpt-5.6-luna"
    current = asyncio.run(settings_routes.get_codex_models())["current"]
    assert current == "gpt-5.6-luna"
