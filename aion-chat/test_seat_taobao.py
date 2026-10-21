"""逛淘宝：狗蛋等座位和小克 / Lucien 一样能去逛（只搜索、收藏、写小感想），回执发到 TA 该去的房间。"""

import asyncio
import json
from unittest.mock import AsyncMock

import aiosqlite
import pytest

import seat_chat


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "t.db"

    async def setup():
        async with aiosqlite.connect(path) as conn:
            await conn.executescript("""
                CREATE TABLE conversations(id TEXT PRIMARY KEY,updated_at REAL);
                CREATE TABLE chatroom_rooms(id TEXT PRIMARY KEY,type TEXT,actor TEXT DEFAULT '',updated_at REAL);
                CREATE TABLE messages(id TEXT PRIMARY KEY,conv_id TEXT,role TEXT,content TEXT,created_at REAL,attachments TEXT);
                CREATE TABLE chatroom_messages(id TEXT PRIMARY KEY,room_id TEXT,sender TEXT,content TEXT,created_at REAL,attachments TEXT);
                CREATE TABLE sync_events(seq INTEGER PRIMARY KEY,event_type TEXT,entity_type TEXT,entity_id TEXT,payload TEXT,created_at REAL);
                INSERT INTO chatroom_rooms VALUES('g1','group','',1),('s3','seat_1v1','ai3',1),('s4','seat_1v1','ai4',1);
            """)
            await conn.commit()
    asyncio.run(setup())
    connect = lambda: aiosqlite.connect(path)  # noqa: E731
    import database
    import taobao_notifications
    monkeypatch.setattr(database, "get_db", connect)
    monkeypatch.setattr(taobao_notifications, "get_db", connect)
    monkeypatch.setattr(taobao_notifications, "get_chatroom_names", lambda: ("小猫", "小克", "Lucien"))
    monkeypatch.setattr(taobao_notifications.manager, "broadcast", AsyncMock())
    import actors
    monkeypatch.setattr(actors, "display_name", lambda a: "狗蛋")
    import routes.chatroom as chatroom_routes
    monkeypatch.setattr(chatroom_routes, "_get_or_create_seat_room", AsyncMock(return_value="s3"))

    def said(room_id, ts):
        async def add():
            async with aiosqlite.connect(path) as conn:
                await conn.execute("INSERT INTO chatroom_messages VALUES (?,?,?,?,?,?)", (f"u{ts}", room_id, "user", "x", ts, "[]"))
                await conn.commit()
        asyncio.run(add())

    def rows(room_id):
        async def go():
            async with aiosqlite.connect(path) as conn:
                return await (await conn.execute(
                    "SELECT sender, content, attachments FROM chatroom_messages WHERE room_id=? AND sender='system'",
                    (room_id,))).fetchall()
        return asyncio.run(go())
    return said, rows


ITEMS = [{"item_id": "123456", "title": "毛绒小狗", "image": "https://img.alicdn.com/a.jpg"}]


@pytest.mark.parametrize("history, room", [([], "s3"), ([("g1", 5)], "g1"), ([("g1", 5), ("s3", 6)], "s3"),
                                           ([("s4", 9)], "s3")])
def test_seat_receipt_goes_where_she_last_talked(db, history, room):
    said, rows = db
    for room_id, ts in history:
        said(room_id, ts)
    from taobao_notifications import notify_shopping_trip
    message = asyncio.run(notify_shopping_trip("ai3", "trip1", ITEMS))
    assert message["room_id"] == room and "狗蛋 逛淘宝" in message["content"]
    (sender, content, attachments), = rows(room)
    card = json.loads(attachments)[0]
    assert card["actor"] == "ai3" and card["type"] == "taobao_trip"
    assert asyncio.run(notify_shopping_trip("ai3", "trip1", ITEMS)) is None  # 同一次逛街只发一张


def test_seat_can_shop_only_when_ready(monkeypatch):
    import taobao_shopping
    monkeypatch.setattr(seat_chat, "check_ready", lambda a: ({}, "m"))
    assert taobao_shopping.actor_id("ai3") == "ai3"
    monkeypatch.setattr(seat_chat, "check_ready", lambda a: (_ for _ in ()).throw(seat_chat.SeatNotReady("TA 还没选模型")))
    with pytest.raises(ValueError, match="还没选模型"):
        taobao_shopping.actor_id("ai3")
    with pytest.raises(ValueError):
        taobao_shopping.actor_id("someone")


def test_seat_autonomy_can_pick_taobao():
    import autonomy
    assert "taobao_roam" in autonomy.SEAT_ACTIONS


def test_shopping_page_lists_enabled_seats(monkeypatch):
    import actors
    import routes.taobao as taobao_routes
    monkeypatch.setattr(actors, "list_actors", lambda include_disabled=True: [
        {"id": "aion", "name": "小克"}, {"id": "ai3", "name": "狗蛋", "avatar": "/skin/ai3.png"}])
    import chatroom
    monkeypatch.setattr(chatroom, "get_chatroom_names", lambda: ("小猫", "小克", "Lucien"))
    store = AsyncMock()
    store.list_items.return_value = []
    store.settings.return_value = {"transport": "native_bridge", "autonomy_enabled": True}
    store.list_trips.return_value = []
    monkeypatch.setattr(taobao_routes, "get_store", AsyncMock(return_value=store))
    state = asyncio.run(taobao_routes.state())
    assert state["names"] == {"aion": "小克", "connor": "Lucien", "ai3": "狗蛋"}
    assert state["avatars"]["ai3"] == "/skin/ai3.png"
    assert taobao_routes.ActorBody(actor="ai3").actor == "ai3"
