"""AI 名片里直接用中转站：拉已保存中转站的模型（Key 不出服务器）、加模型、新建中转站。"""

import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
import relay_models
import routes.settings as rs


@pytest.fixture
def client(monkeypatch):
    settings = {
        "custom_model_routes": [{"id": "r1", "name": "红豆沙", "base_url": "https://a.example/v1", "api_key": "sk-secret",
                                 "models": [{"key": "claude-opus-5-5", "model": "claude-opus-5-5"}]}],
        config.SEAT_ROUTES_KEY: [{"id": "seat_ai3", "name": "Jasper 专属线路", "base_url": "https://g.example/v1",
                                  "api_key": "sk-g", "models": [{"key": "Jasper·gemini", "model": "gemini-3-pro"}]}],
    }
    saved = []
    monkeypatch.setattr(config, "SETTINGS", settings)
    monkeypatch.setattr(rs, "SETTINGS", settings)
    monkeypatch.setattr(config, "save_settings", lambda data: saved.append(json.loads(json.dumps(data))))
    monkeypatch.setattr(rs, "save_settings", lambda data: saved.append(json.loads(json.dumps(data))))
    config.refresh_custom_models()
    calls = []

    async def fake_fetch(base_url, api_key=""):
        calls.append((base_url, api_key))
        return ["claude-opus-5-5", "gemini-3-pro", "gpt-5-5"]
    monkeypatch.setattr(relay_models, "fetch", fake_fetch)
    app = FastAPI()
    app.include_router(rs.router)
    yield TestClient(app), settings, saved, calls
    monkeypatch.undo()
    config.refresh_custom_models()


def test_saved_route_models_use_the_stored_key_and_mark_added(client):
    c, _, _, calls = client
    r = c.post("/api/custom-routes/r1/models")
    assert r.status_code == 200
    assert calls == [("https://a.example/v1", "sk-secret")]
    assert {m["id"]: m["added"] for m in r.json()["models"]} == {
        "claude-opus-5-5": True, "gemini-3-pro": False, "gpt-5-5": False}
    assert "sk-secret" not in r.text


def test_add_model_is_idempotent_and_avoids_name_clashes(client):
    c, settings, saved, _ = client
    assert c.post("/api/custom-routes/r1/add-model", json={"model": "claude-opus-5-5"}).json() == {"key": "claude-opus-5-5"}
    assert saved == []  # 已经有了，不写盘
    r = c.post("/api/custom-routes/r1/add-model", json={"model": "gpt-5-5"})
    assert r.json() == {"key": "gpt-5-5"} and "gpt-5-5" in config.MODELS
    # 模型名和 Jasper 专属线路里的显示名撞了：加上中转站名
    r = c.post("/api/custom-routes/r1/add-model", json={"model": "Jasper·gemini"})
    assert r.json()["key"] == "红豆沙·Jasper·gemini"
    assert [m["model"] for m in settings["custom_model_routes"][0]["models"]] == [
        "claude-opus-5-5", "gpt-5-5", "Jasper·gemini"]


def test_add_model_rejects_bad_names_unknown_and_seat_routes(client):
    c, _, saved, _ = client
    assert c.post("/api/custom-routes/r1/add-model", json={"model": " "}).status_code == 400
    assert c.post("/api/custom-routes/r1/add-model", json={"model": "a\nb"}).status_code == 400
    assert c.post("/api/custom-routes/nope/add-model", json={"model": "x"}).status_code == 404
    assert c.post("/api/custom-routes/seat_ai3/add-model", json={"model": "x"}).status_code == 404
    assert saved == []


def test_create_route_from_the_card(client):
    c, settings, _, _ = client
    r = c.post("/api/custom-routes", json={"name": "冰可乐", "base_url": "https://b.example/v1/",
                                           "api_key": " sk-b ", "model": "claude-opus-5-5"})
    assert r.status_code == 200
    body = r.json()
    route = next(x for x in settings["custom_model_routes"] if x["id"] == body["route_id"])
    assert route["base_url"] == "https://b.example/v1" and route["api_key"] == "sk-b"
    assert body["key"] == "冰可乐·claude-opus-5-5"  # 红豆沙已经有同名模型
    assert config.MODELS[body["key"]]["base_url"] == "https://b.example/v1"
    assert "sk-b" not in r.text
    assert c.post("/api/custom-routes", json={"base_url": "https://u:p@b.example", "model": "m"}).status_code == 400


def test_stale_settings_page_cannot_erase_what_the_card_just_added(client):
    """设置页开着旧数据时，名片里刚加的模型 / 中转站不会被整份保存悄悄覆盖（Codex 审查 B2）。"""
    import asyncio
    from fastapi import HTTPException
    c, settings, _, _ = client
    page = asyncio.run(rs.get_settings())  # 设置页打开时拿到的数据和版本号
    stale_routes = page["custom_model_routes"]
    assert c.post("/api/custom-routes/r1/add-model", json={"model": "gpt-5-5"}).status_code == 200
    with pytest.raises(HTTPException) as err:
        asyncio.run(rs.update_settings(rs.SettingsUpdate(
            custom_model_routes=stale_routes, custom_routes_rev=page["custom_routes_rev"], tavily_api_key="probe")))
    assert err.value.status_code == 409
    assert "gpt-5-5" in config.MODELS and settings.get("tavily_api_key") != "probe"
    # 没带版本号的旧页面同样拒绝
    with pytest.raises(HTTPException):
        asyncio.run(rs.update_settings(rs.SettingsUpdate(custom_model_routes=stale_routes)))
    # 刷新后（拿到新版本号）可以正常保存，保存后版本号前进
    fresh = asyncio.run(rs.get_settings())
    result = asyncio.run(rs.update_settings(rs.SettingsUpdate(
        custom_model_routes=fresh["custom_model_routes"], custom_routes_rev=fresh["custom_routes_rev"])))
    assert result["custom_routes_rev"] == fresh["custom_routes_rev"] + 1
    assert "gpt-5-5" in config.MODELS
    # 只改别的设置、不带中转站列表时不需要版本号
    assert asyncio.run(rs.update_settings(rs.SettingsUpdate(tavily_api_key="x")))["ok"]
