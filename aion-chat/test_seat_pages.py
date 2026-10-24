"""§75 排查：只认小克和 Lucien、漏了狗蛋和座位 4～6 的地方（AI 认人、相册、许愿、点名说话、人设编辑）。"""

import asyncio
import json
import sqlite3

import pytest
from fastapi import HTTPException

import actors
import album
import context_builder
import routes.album as album_routes
import routes.chatroom as chatroom_routes


@pytest.fixture
def seats(monkeypatch):
    store = {"seats": {"ai3": {"name": "Jasper", "enabled": True, "model": "",
                               "persona_sections": {"identity_core": "我是狗蛋"}}}}
    monkeypatch.setattr(actors, "_load_raw", lambda: json.loads(json.dumps(store)))
    monkeypatch.setattr(actors, "_save_raw", lambda data: store.update(json.loads(json.dumps(data))))
    return store


def test_xiaoke_reads_seat_lines_with_names(seats):
    """A1：小克 / Lucien 读群聊记录时，狗蛋的话写「Jasper」，不写「ai3」。"""
    assert context_builder._seat_display_name("ai3") == "Jasper"
    assert context_builder._seat_display_name("someone") == "someone"


def test_seat_lines_are_ai_lines_not_hers(seats):
    """A2 / A3：狗蛋说的话是 AI 说的，不当成小猫说的。"""
    assert chatroom_routes._is_ai_sender("ai3") and chatroom_routes._is_ai_sender("connor")
    assert not chatroom_routes._is_ai_sender("user")
    assert chatroom_routes._seat_name_map()["ai3"] == "Jasper"


def test_each_ai_has_an_album_and_seat_photos_go_home(tmp_path, seats):
    """相册：每位 AI 一本；以前狗蛋生成、被归进小克相册的照片挪回他自己的；没入住的座位留空位。"""
    root = tmp_path / "album"
    store = album.AlbumStore(root)
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, format="PNG")
    mine = store.save_photo(buf.getvalue(), source="generated", actor="ai3", prompt="自拍")
    assert mine["album_id"] == "ai3"
    with sqlite3.connect(root / "album.sqlite3") as db:  # 模拟旧数据：被归进了小克的相册
        db.execute("UPDATE photos SET album_id='aion' WHERE id=?", (mine["id"],))
    album.AlbumStore(root)  # 重启时迁移
    with sqlite3.connect(root / "album.sqlite3") as db:
        assert db.execute("SELECT album_id FROM photos WHERE id=?", (mine["id"],)).fetchone()[0] == "ai3"
    cats = {c["id"]: c for c in album_routes.album_categories()}
    assert cats["ai3"]["name"] == "Jasper相册" and not cats["ai3"]["placeholder"]
    assert cats["ai4"]["placeholder"]


def test_persona_endpoints_for_lucien_and_seats(seats, monkeypatch):
    class Req:
        def __init__(self, body):
            self.body = body

        async def json(self):
            return self.body
    got = asyncio.run(actors.api_get_persona("ai3"))
    assert got["sections"]["identity_core"] == "我是狗蛋"
    asyncio.run(actors.api_save_persona("ai3", Req({"sections": {"identity_core": "我是狗蛋，本少爷"}})))
    assert seats["seats"]["ai3"]["persona_sections"]["identity_core"] == "我是狗蛋，本少爷"
    for bad in ({"sections": {}}, {"sections": {"identity_core": ""}}, {"sections": {"what": "x"}}, {"sections": "x"}):
        with pytest.raises(HTTPException):  # 写错不会把已有人设清空
            asyncio.run(actors.api_save_persona("ai3", Req(bad)))
    with pytest.raises(HTTPException):  # 小克的在世界书本身改
        asyncio.run(actors.api_get_persona("aion"))
    import chatroom
    room = {}
    monkeypatch.setattr(chatroom, "load_chatroom_config", lambda: dict(room))
    monkeypatch.setattr(chatroom, "save_chatroom_config", lambda data: room.update(data))
    asyncio.run(actors.api_save_persona("connor", Req({"sections": {"identity_core": "我是 Lucien"}})))
    assert room["connor_persona_sections"] == {"identity_core": "我是 Lucien"} and "Lucien" in room["connor_persona"]


def test_reply_once_and_edit_accept_seats():
    """手动轮流能点狗蛋说话；狗蛋的回复也能编辑（源码里不再只认两位）。"""
    from pathlib import Path
    text = (Path(__file__).parent / "routes" / "chatroom.py").read_text(encoding="utf-8")
    start = text.index("async def reply_once(")
    body = text[start:text.index("@router.post", start + 10)]
    assert "seat_chat.check_ready(speaker)" in body and "_generate_seat_reply(" in body
    assert 'if not _is_ai_sender(msg["sender"]):' in text


def test_wish_can_be_announced_to_a_seat(seats, monkeypatch):
    import routes.wishes as wishes_routes
    import seat_chat

    class Ready(Exception):
        pass

    def check_ready(actor):
        if actor != "ai3":
            raise seat_chat.SeatNotReady("还没入住")
        return {}, "m"
    monkeypatch.setattr(seat_chat, "check_ready", check_ready)
    with pytest.raises(HTTPException):
        asyncio.run(wishes_routes.api_announce_wish("w1", wishes_routes.WishAnnounce(target="ai4")))
    with pytest.raises(HTTPException):
        asyncio.run(wishes_routes.api_announce_wish("w1", wishes_routes.WishAnnounce(target="nobody")))
    monkeypatch.setattr(wishes_routes, "get_wish", lambda wish_id: _none())
    with pytest.raises(HTTPException) as err:  # 座位通过了身份检查，接着才去找愿望
        asyncio.run(wishes_routes.api_announce_wish("w1", wishes_routes.WishAnnounce(target="ai3")))
    assert err.value.status_code == 404


async def _none():
    return None
