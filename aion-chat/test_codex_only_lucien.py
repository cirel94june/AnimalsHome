"""Codex（GPT）只给 Lucien：小克和座位在任何入口都不会用到它（§72 修 Codex 复审 M1）。"""

import asyncio
import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

import actors
import chatroom
import config
import database
import memory_hub_jobs
import persona_evolution
import routes.chatroom as chatroom_routes

HERE = Path(__file__).parent


@pytest.fixture
def models(monkeypatch):
    table = {
        "GPT": {"provider": "codex_cli", "model": "gpt-6.1-sol"},
        "红豆沙·claude": {"provider": "custom_openai", "model": "claude", "route_id": "custom_route_1"},
    }
    monkeypatch.setattr(config, "MODELS", table)
    monkeypatch.setattr(memory_hub_jobs, "MODELS", table)
    monkeypatch.setattr(config, "model_usable", lambda cfg: True)
    monkeypatch.setitem(config.SETTINGS, "default_model", "")
    return table


@pytest.fixture
def cards(monkeypatch, models):
    store = {"seats": {"aion": {"model": "GPT", "background_model": ""},
                       "connor": {"model": "GPT", "background_model": ""},
                       "ai3": {"model": "", "background_model": "GPT", "enabled": True}}}
    monkeypatch.setattr(actors, "_load_raw", lambda: json.loads(json.dumps(store)))
    monkeypatch.setattr(actors, "_save_raw", lambda data: store.update(json.loads(json.dumps(data))))
    return store


def test_name_card_codex_is_ignored_for_xiaoke_and_seats(cards):
    assert memory_hub_jobs.own_model("aion") == ""
    assert memory_hub_jobs.own_model("connor") == "GPT"  # Lucien 照旧
    # 复审反例二：没有共用线路时 _aion_model() 给空串，后台线路不能从名片里取回 Codex
    assert memory_hub_jobs.background_model("aion", "") == ""
    assert memory_hub_jobs.background_model("aion", "GPT") == ""
    assert memory_hub_jobs.background_model("ai3", "红豆沙·claude") == "红豆沙·claude"  # 后台线路是 Codex：不用它


def test_saving_codex_for_xiaoke_or_seat_is_rejected(cards):
    class Req:
        def __init__(self, body):
            self.body = body

        async def json(self):
            return self.body

    for actor_id in ("aion", "ai3"):
        for field in ("model", "background_model"):
            with pytest.raises(HTTPException) as err:
                asyncio.run(actors.api_update_actor(actor_id, Req({field: "GPT"})))
            assert "Lucien" in err.value.detail
    with pytest.raises(HTTPException):
        chatroom_routes._apply_config_update({}, chatroom_routes.ConfigUpdate(aion_model="GPT"))
    cfg = chatroom_routes._apply_config_update({}, chatroom_routes.ConfigUpdate(connor_model="GPT"))
    assert cfg["connor_model"] == "GPT"


def test_persona_growth_never_runs_on_codex(models, monkeypatch):
    monkeypatch.setattr(persona_evolution, "_chatroom_config", lambda: {"aion_model": "GPT"})
    assert asyncio.run(persona_evolution._main_ai_model()) == "红豆沙·claude"
    models.pop("红豆沙·claude")
    with pytest.raises(RuntimeError):
        asyncio.run(persona_evolution._main_ai_model())
    called = []

    async def fake_call(messages, model_key, **kw):
        called.append(model_key)
        return "{}"
    monkeypatch.setattr(persona_evolution, "simple_ai_call", fake_call)
    with pytest.raises(RuntimeError):  # 就算有人直接把 Codex 传进来，调用前也挡住
        asyncio.run(persona_evolution._call_actor_model("main_ai", [], "GPT"))
    assert called == []


def test_startup_scrubs_stored_codex_choices(tmp_path, monkeypatch, cards):
    path = tmp_path / "chat.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    asyncio.run(database.init_db())
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            INSERT INTO conversations (id,title,model,created_at,updated_at) VALUES
              ('c1','旧','GPT',1,1), ('c2','好','红豆沙·claude',2,2);
        """)
    room = {"aion_model": "GPT", "connor_model": "GPT"}
    monkeypatch.setattr(chatroom, "load_chatroom_config", lambda: dict(room))
    monkeypatch.setattr(chatroom, "save_chatroom_config", lambda data: room.update(data))
    changed = asyncio.run(config.scrub_codex_choices())
    with sqlite3.connect(path) as conn:
        assert dict(conn.execute("SELECT id, model FROM conversations")) == {"c1": "红豆沙·claude", "c2": "红豆沙·claude"}
    assert room == {"aion_model": "", "connor_model": "GPT"}  # Lucien 的不动
    assert cards["seats"]["aion"]["model"] == "" and cards["seats"]["connor"]["model"] == "GPT"
    assert cards["seats"]["ai3"]["background_model"] == ""
    assert "chatroom.aion_model" in changed and "aion.model" in changed


# 每个以小克身份生成的入口，挑模型时都经过 aion_model_key / codex_forbidden（防止以后新加的读法漏掉）
XIAOKE_ENTRY_POINTS = {
    "schedule.py": 2,          # 闹铃、定时监控
    "camera.py": 1,            # 监控唤醒
    "location.py": 1,          # 定位唤醒
    "routes/moments.py": 1,    # 朋友圈评论
    "persona_evolution.py": 1, # 人格成长
    "autonomy.py": 2,          # 自主活动
    "fund.py": 1, "routes/gift.py": 1, "english_corner.py": 1, "memory.py": 1,
    "repair_worker.py": 1, "routes/floating_chat.py": 1,
}


@pytest.mark.parametrize("name,count", sorted(XIAOKE_ENTRY_POINTS.items()))
def test_xiaoke_entry_points_use_the_identity_resolver(name, count):
    text = (HERE / name).read_text(encoding="utf-8")
    assert len(re.findall(r"aion_model_key\(", text)) >= count
