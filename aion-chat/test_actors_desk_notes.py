import asyncio
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import actors
import desk_notes


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(actors, "ACTORS_PATH", tmp_path / "actors.json")
    monkeypatch.setattr(actors, "AVATAR_DIR", tmp_path / "skin")
    monkeypatch.setattr(actors, "load_worldbook", lambda: {"ai_name": "小克", "user_name": "Ceci"})
    monkeypatch.setattr(actors, "_connor_name", lambda: "Lucien")
    monkeypatch.setattr(desk_notes, "NOTES_PATH", tmp_path / "desk_notes.json")
    app = FastAPI()
    app.include_router(actors.router)
    app.include_router(desk_notes.router)
    return TestClient(app)


def test_six_seats_with_builtin_names_from_existing_settings(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    data = client.get("/api/actors").json()
    assert data["user"]["name"] == "Ceci"
    seats = data["actors"]
    assert [a["id"] for a in seats] == ["aion", "connor", "ai3", "ai4", "ai5", "ai6"]
    assert [a["name"] for a in seats[:3]] == ["小克", "Lucien", "Jasper"]
    assert [a["enabled"] for a in seats] == [True, True, True, False, False, False]
    assert actors.memory_hub_id("connor") == "lucien"
    assert actors.memory_hub_id("ai4") == ""  # 空位不接 Memory Hub


def test_update_rules(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    aion = client.put("/api/actors/aion", json={"enabled": False, "name": "别的名字", "meet_since": "2025-03-14"}).json()
    assert aion["enabled"] is True and aion["name"] == "小克" and aion["meet_since"] == "2025-03-14"
    assert client.put("/api/actors/ai3", json={"meet_since": "昨天"}).status_code == 400
    assert client.put("/api/actors/ai3", json={"avatar": "javascript:alert(1)"}).status_code == 400
    assert client.put("/api/actors/nobody", json={}).status_code == 404
    seat4 = client.put("/api/actors/ai4", json={"enabled": True, "name": "新朋友", "memory_hub": "newfriend"}).json()
    assert seat4["enabled"] and seat4["name"] == "新朋友"
    assert actors.memory_hub_id("ai4") == "newfriend"


def test_avatar_upload(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    png = b"\x89PNG\r\n\x1a\n" + b"1" * 32
    url = client.post("/api/actors/ai3/avatar", files={"file": ("a.png", png, "image/png")}).json()["url"]
    assert url.startswith("/skin-assets/") and actors.get_actor("ai3")["avatar"] == url
    assert client.post("/api/actors/ai3/avatar", files={"file": ("a.txt", b"x", "text/plain")}).status_code == 400


def test_ai_note_command_is_extracted_from_reply(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    text = asyncio.run(desk_notes.process_note_commands("早点睡。[NOTE：今晚月亮很圆]", "connor"))
    assert text == "早点睡。"
    notes = desk_notes.load_notes()
    assert notes[-1]["author"] == "connor" and notes[-1]["text"] == "今晚月亮很圆"
    assert asyncio.run(desk_notes.process_note_commands("没有指令", "aion")) == "没有指令"


def test_user_notes_api_newest_first_and_delete(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    first = client.post("/api/desk/notes", json={"text": "第一张"}).json()
    client.post("/api/desk/notes", json={"text": "第二张"})
    listed = client.get("/api/desk/notes").json()
    assert [n["text"] for n in listed] == ["第二张", "第一张"] and listed[0]["author"] == "user"
    assert client.post("/api/desk/notes", json={"text": "  "}).status_code == 400
    assert client.delete(f"/api/desk/notes/{first['id']}").json() == {"ok": True}
    assert [n["text"] for n in client.get("/api/desk/notes").json()] == ["第二张"]
    for i in range(40):
        desk_notes.add_note("user", f"n{i}")
    assert len(desk_notes.load_notes()) == desk_notes.MAX_NOTES
