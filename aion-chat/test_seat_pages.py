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
    with sqlite3.connect(root / "album.sqlite3") as db:  # 模拟升级前的旧库：照片在小克相册、还没做过迁移
        db.execute("UPDATE photos SET album_id='aion' WHERE id=?", (mine["id"],))
        db.execute("DELETE FROM album_migrations")
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


def test_repair_room_advisors_and_roles(seats, monkeypatch):
    """维修室：狗蛋（已入住、准备好）能出主意；动手的只有 Lucien；没入住的座位发不过去。"""
    import repair_context
    import repair_store
    import seat_chat

    def check_ready(actor):
        if actor != "ai3":
            raise seat_chat.SeatNotReady("还没入住")
        return {}, "m"
    monkeypatch.setattr(seat_chat, "check_ready", check_ready)
    assert repair_context.repair_advisors() == ["aion", "ai3"]
    import chatroom
    monkeypatch.setattr(chatroom, "get_chatroom_names", lambda: ("小猫", "小克", "Lucien"))
    assert repair_context.repair_names()["ai3"] == "Jasper"
    steer = repair_store.RepairStore.can_steer
    assert steer({"kind": "discuss", "payload": {"recipient": "connor"}})
    assert steer({"kind": "discuss", "payload": {"recipient": "both"}})
    assert not steer({"kind": "discuss", "payload": {"recipient": "ai3"}})  # 只发给出主意的人：不能中途补充给执行者
    assert not steer({"kind": "discuss", "payload": {"recipient": "aion"}})


def test_repair_both_goes_advisors_then_executor(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock, patch
    import repair_worker
    order = []

    async def advisor(store, job, actor="aion"):
        order.append(actor)

    async def executor(store, job, instructions, context):
        order.append("connor")

    class Store:
        def cancelled(self, job_id): return False
        def event(self, *a, **k): pass
        def message(self, *a, **k): pass
    job = {"id": "j1", "task_id": "t1", "kind": "discuss", "payload": {"recipient": "both"}}
    with patch("repair_context.repair_advisors", return_value=["aion", "ai3"]), \
            patch("repair_context.repair_names", return_value={"user": "小猫", "aion": "小克", "connor": "Lucien", "ai3": "Jasper"}), \
            patch("repair_context.build_context", new_callable=AsyncMock, return_value=("", "")), \
            patch.object(repair_worker, "reply_aion", advisor), patch("repair_codex.run_codex", executor):
        asyncio.run(repair_worker.discuss(Store(), job))
    assert order == ["aion", "ai3", "connor"]


def test_album_seat_migration_runs_once(tmp_path, seats):
    """§78 M2：挪回座位相册只做一次；之后她手动挪到小克相册的，重启不会被改回去。"""
    root = tmp_path / "album"
    store = album.AlbumStore(root)
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, format="PNG")
    photo = store.save_photo(buf.getvalue(), source="generated", actor="ai3", prompt="自拍")
    store.move_photos([photo["id"]], "aion")  # 她自己挪的
    album.AlbumStore(root)
    album.AlbumStore(root)
    with sqlite3.connect(root / "album.sqlite3") as db:
        assert db.execute("SELECT album_id FROM photos WHERE id=?", (photo["id"],)).fetchone()[0] == "aion"


def test_repair_seat_context_has_own_private_chat(tmp_path, monkeypatch, seats):
    """§78 L4：维修室里座位的生活背景带上 TA 自己的私聊，不带别的座位的。"""
    import database
    path = tmp_path / "chat.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    asyncio.run(database.init_db())
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            INSERT INTO chatroom_rooms (id,title,type,actor,created_at,updated_at) VALUES
              ('g','家','group','',1,1), ('s3','狗蛋','seat_1v1','ai3',1,1), ('s4','别人','seat_1v1','ai4',1,1);
            INSERT INTO chatroom_messages (id,room_id,sender,content,created_at) VALUES
              ('m1','g','user','群里',10), ('m2','s3','user','和狗蛋私聊',11), ('m3','s4','user','别人的私聊',12);
        """)
    rows = asyncio.run(context_builder.fetch_merged_timeline("ai3", 20))
    assert sorted(r["content"] for r in rows) == ["和狗蛋私聊", "群里"]


def test_group_digest_and_worldbook_guards_in_source():
    """§78 L2：群聊摘要收座位的话；M1 / L1：世界书读到之前不能存、存失败底部按钮停下。"""
    from pathlib import Path
    here = Path(__file__).parent
    chat = (here / "routes" / "chatroom.py").read_text(encoding="utf-8")
    assert 'if sender != "user" and not _is_ai_sender(sender):' in chat and "**_seat_name_map()," in chat
    wb = (here / "static" / "worldbook.html").read_text(encoding="utf-8")
    assert "load !== wbPersonaLoad" in wb and "if (!wbPersonaLoaded)" in wb
    assert '!(await saveOtherPersona())' in wb
