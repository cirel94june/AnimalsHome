import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import actors
import chatroom
import config


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(actors, "ACTORS_PATH", tmp_path / "actors.json")
    monkeypatch.setattr(actors, "load_worldbook", lambda: {"ai_name": "小克"})
    monkeypatch.setattr(actors, "_connor_name", lambda: "Lucien")
    monkeypatch.setattr(chatroom, "CHATROOM_CONFIG_PATH", tmp_path / "chatroom_config.json")
    saved = {}
    monkeypatch.setattr(config, "SETTINGS", {"custom_model_routes": [
        {"id": "relay", "name": "原有线路", "base_url": "https://relay.example/v1", "api_key": "k0",
         "models": [{"key": "原有模型", "model": "m0"}]}]})
    monkeypatch.setattr(config, "save_settings", lambda data: saved.update(json.loads(json.dumps(data))))
    config.refresh_custom_models()
    app = FastAPI()
    app.include_router(actors.router)
    yield TestClient(app), saved
    monkeypatch.undo()
    config.refresh_custom_models()


def _route(saved, rid):
    routes = (saved.get("custom_model_routes") or []) + (saved.get("seat_model_routes") or [])
    return next((r for r in routes if r["id"] == rid), None)


def test_own_api_becomes_a_dedicated_route_and_key_is_never_returned(client):
    c, saved = client
    r = c.put("/api/actors/ai3", json={"api": {"base_url": "https://gem.example/v1/", "api_key": "sk-secret",
                                               "model": "gemini-3-pro"}})
    assert r.status_code == 200
    body = r.json()
    assert "sk-secret" not in r.text
    assert body["model"] == "Jasper专属·gemini-3-pro"
    assert body["api"] == {"base_url": "https://gem.example/v1", "model": "gemini-3-pro",
                           "key": "Jasper专属·gemini-3-pro", "has_key": True}
    route = _route(saved, "seat_ai3")
    assert route["api_key"] == "sk-secret" and route["name"] == "Jasper 专属线路"
    assert config.MODELS["Jasper专属·gemini-3-pro"]["base_url"] == "https://gem.example/v1"
    assert _route(saved, "relay")  # 其他线路不受影响
    assert "sk-secret" not in c.get("/api/actors").text

    # Key 留空 = 沿用；换模型名会换模型键
    c.put("/api/actors/ai3", json={"api": {"base_url": "https://gem.example/v1", "api_key": "", "model": "gemini-3-flash"}})
    assert _route(saved, "seat_ai3")["api_key"] == "sk-secret"
    assert actors.get_actor("ai3")["model"] == "Jasper专属·gemini-3-flash"
    assert "Jasper专属·gemini-3-pro" not in config.MODELS


def test_seat_key_never_in_settings_response_and_survives_stale_settings_page(client, monkeypatch):
    import routes.settings as settings_routes
    monkeypatch.setattr(settings_routes, "save_settings", lambda data: None)  # 不写真实设置文件
    c, saved = client
    c.put("/api/actors/ai3", json={"api": {"base_url": "https://gem.example/v1", "api_key": "audit-secret", "model": "g"}})
    # 设置页 GET 不含座位线路和 Key
    page = asyncio.run(settings_routes.get_settings())
    assert "audit-secret" not in json.dumps(page, ensure_ascii=False)
    assert all(not r["id"].startswith("seat_") for r in page["custom_model_routes"])
    # 一个早就打开的旧设置页把整份线路发回来（还夹带一条伪造的 seat_ 线路）
    stale = [{"id": "relay", "name": "原有线路", "base_url": "https://relay.example/v1", "api_key": "k0",
              "models": [{"key": "原有模型", "model": "m0"}]},
             {"id": "seat_ai3", "name": "伪造", "base_url": "https://evil.example/v1", "api_key": "x",
              "models": [{"key": "伪造", "model": "x"}]}]
    asyncio.run(settings_routes.update_settings(settings_routes.SettingsUpdate(custom_model_routes=stale)))
    assert config.MODELS["Jasper专属·g"]["base_url"] == "https://gem.example/v1"  # 座位线路没被覆盖
    assert "伪造" not in config.MODELS
    assert actors.api_summary("ai3")["has_key"] is True


def test_clear_key_is_explicit(client):
    c, saved = client
    c.put("/api/actors/ai3", json={"api": {"base_url": "https://gem.example/v1", "api_key": "k", "model": "g"}})
    c.put("/api/actors/ai3", json={"api": {"base_url": "https://gem.example/v1", "api_key": "", "model": "g"}})
    assert actors.api_summary("ai3")["has_key"] is True  # 留空 = 不改
    c.put("/api/actors/ai3", json={"api": {"base_url": "https://gem.example/v1", "clear_key": True, "model": "g"}})
    assert actors.api_summary("ai3")["has_key"] is False


def test_old_mixed_storage_is_migrated(client, monkeypatch):
    monkeypatch.setitem(config.SETTINGS, "custom_model_routes", config.SETTINGS["custom_model_routes"] + [
        {"id": "seat_ai3", "name": "Jasper 专属线路", "base_url": "https://gem.example/v1", "api_key": "old",
         "models": [{"key": "Jasper专属·old", "model": "old"}]}])
    config.refresh_custom_models()
    assert [r["id"] for r in config.SETTINGS["custom_model_routes"]] == ["relay"]
    assert [r["id"] for r in config.SETTINGS["seat_model_routes"]] == ["seat_ai3"]
    assert "Jasper专属·old" in config.MODELS


def test_bad_color_rejected(client):
    c, _ = client
    assert c.put("/api/actors/ai3", json={"color": "red\" onclick=\"x"}).status_code == 400
    assert c.put("/api/actors/ai3", json={"color": "#5ca88f"}).status_code == 200


def test_clearing_own_api_removes_route_and_model(client):
    c, saved = client
    c.put("/api/actors/ai3", json={"api": {"base_url": "https://gem.example/v1", "api_key": "k", "model": "g"}})
    body = c.put("/api/actors/ai3", json={"api": None, "model": ""}).json()
    assert body["api"] is None and body["model"] == ""
    assert _route(saved, "seat_ai3") is None and _route(saved, "relay")


def test_switching_to_existing_line_keeps_own_api_saved(client):
    c, saved = client
    c.put("/api/actors/ai3", json={"api": {"base_url": "https://gem.example/v1", "api_key": "k", "model": "g"}})
    body = c.put("/api/actors/ai3", json={"model": "原有模型"}).json()
    assert body["model"] == "原有模型" and body["api"]["model"] == "g"


def test_builtin_ais_sync_their_chatroom_model(client):
    c, _ = client
    c.put("/api/actors/connor", json={"api": {"base_url": "https://gpt.example/v1", "api_key": "k", "model": "gpt-x"}})
    assert chatroom.load_chatroom_config()["connor_model"] == "Lucien专属·gpt-x"
    c.put("/api/actors/aion", json={"model": "原有模型"})
    assert chatroom.load_chatroom_config()["aion_model"] == "原有模型"
    c.put("/api/actors/ai3", json={"model": "原有模型"})  # 座位 3 不改聊天室的前两位
    cfg = chatroom.load_chatroom_config()
    assert cfg["aion_model"] == "原有模型" and cfg["connor_model"] == "Lucien专属·gpt-x"


@pytest.mark.parametrize("api, message", [
    ({"base_url": "gem.example/v1", "model": "g"}, "http"),
    ({"base_url": "https://gem.example/v1", "model": ""}, "模型名"),
    ("not-a-dict", "格式"),
])
def test_bad_input_is_rejected(client, api, message):
    c, saved = client
    r = c.put("/api/actors/ai3", json={"api": api})
    assert r.status_code == 400 and message in r.json()["detail"]
    assert not saved  # 什么都没写
