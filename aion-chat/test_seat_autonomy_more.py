"""自主活动对齐：狗蛋等座位也能和家人聊聊、看家庭动态、翻旧记忆（自己的日记）、去小红书巡游。"""

import asyncio
from unittest.mock import AsyncMock

import aiosqlite
import pytest

import autonomy
import seat_chat


@pytest.fixture
def family(monkeypatch):
    ready = {"ai3", "ai4"}

    def check_ready(seat):
        if seat not in ready:
            raise seat_chat.SeatNotReady("off")
        return {}, "m"
    monkeypatch.setattr(seat_chat, "check_ready", check_ready)
    names = {"aion": "小克", "connor": "Lucien", "ai3": "狗蛋", "ai4": "小四"}
    monkeypatch.setattr(autonomy, "_actor_label", lambda a: names.get(a, a))
    return ready


def test_seats_get_the_new_activities():
    assert {"role_chat", "home_dynamics", "memory_browse", "xhs_roam"} <= autonomy.SEAT_ACTIONS
    assert not {"album_browse", "friend_visit", "board_check", "board_visit", "billiards_play",
                "seeky_interaction"} & autonomy.SEAT_ACTIONS  # 小猫暂缓 / 不要的


def test_partner_choice(family, monkeypatch):
    assert autonomy._family_partners("ai3") == ["aion", "connor", "ai4"]
    assert autonomy._pick_partner("ai3", "小克") == "aion"
    assert autonomy._pick_partner("ai3", "ai4") == "ai4"
    assert autonomy._pick_partner("ai3", "ai5") in ("aion", "connor", "ai4")  # 没准备好的不找
    assert autonomy._pick_partner("aion") == "connor" and autonomy._pick_partner("connor") == "aion"  # 原来的默认
    assert autonomy._pick_partner("aion", "狗蛋") == "ai3"  # 小克也能点名找狗蛋
    monkeypatch.setattr(autonomy.random, "choice", lambda seq: seq[-1])
    assert autonomy._pick_partner("ai3") == "ai4"


@pytest.mark.parametrize("target, replier", [("aion", "_reply_aion"), ("ai4", "_generate_seat_reply")])
def test_role_chat_reply_goes_through_the_partners_own_path(family, monkeypatch, target, replier):
    import routes.chatroom as chatroom_routes
    monkeypatch.setattr(autonomy, "_latest_group_room_id", AsyncMock(return_value="g1"))
    monkeypatch.setattr(autonomy, "_save_autonomy_chatroom_message", AsyncMock())
    monkeypatch.setattr(chatroom_routes, "_load_room_and_messages", AsyncMock(return_value=({"context_minutes": 30}, [])))
    monkeypatch.setattr(autonomy, "_aion_model", AsyncMock(return_value="m"))
    monkeypatch.setattr(autonomy, "append_idle_event", AsyncMock(return_value={"id": "e"}))
    calls = {name: AsyncMock() for name in ("_reply_aion", "_reply_connor", "_generate_seat_reply")}
    for name, mock in calls.items():
        monkeypatch.setattr(chatroom_routes, name, mock)
    asyncio.run(autonomy._run_role_chat("ai3", {"message": "在干嘛", "target": target}))
    assert calls[replier].await_count == 1
    if replier == "_generate_seat_reply":
        assert calls[replier].await_args.kwargs == {"actor": "ai4", "group": True}
    assert sum(m.await_count for m in calls.values()) == 1


def test_seat_memories_are_its_own_diaries(tmp_path, monkeypatch):
    path = tmp_path / "m.db"

    async def setup():
        async with aiosqlite.connect(path) as conn:
            await conn.execute("CREATE TABLE diary_entries (id TEXT, author TEXT, title TEXT, content TEXT, mood TEXT, "
                               "source_type TEXT, source_ref TEXT, source_start_ts REAL, source_end_ts REAL, created_at REAL)")
            for i, author in enumerate(("ai3", "ai4", "aion", "ai3")):
                await conn.execute("INSERT INTO diary_entries (id, author, content, created_at) VALUES (?,?,?,?)",
                                   (f"d{i}", author, f"{author} 的日记 {i}", 1000 + i))
            await conn.commit()
    asyncio.run(setup())
    monkeypatch.setattr(autonomy, "get_db", lambda: aiosqlite.connect(path))
    rows = asyncio.run(autonomy._memory_rows("ai3"))
    assert [(r["id"], r["content"], r["type"]) for r in rows] == [
        ("diary:d0", "ai3 的日记 0", "diary"), ("diary:d3", "ai3 的日记 3", "diary")]


def test_seat_private_message_goes_where_she_last_talked(monkeypatch):
    monkeypatch.setattr(seat_chat, "seat_target_room", AsyncMock(return_value=("g1", "group")))
    saved = AsyncMock(return_value={"id": "x"})
    monkeypatch.setattr(autonomy, "_save_autonomy_chatroom_message", saved)
    asyncio.run(autonomy._save_private_message("ai3", "你好"))
    assert saved.await_args.args[:3] == ("g1", "ai3", "你好")
    import routes.chatroom as chatroom_routes
    monkeypatch.setattr(chatroom_routes, "_get_or_create_seat_room", AsyncMock(return_value="s3"))
    asyncio.run(autonomy._save_private_message("ai3", "悄悄说", force_private=True))
    assert saved.await_args.args[0] == "s3"


def test_group_tease_targets_whoever_posted(family, monkeypatch):
    import routes.chatroom as chatroom_routes
    monkeypatch.setattr(autonomy, "_latest_group_room_id", AsyncMock(return_value="g1"))
    monkeypatch.setattr(autonomy, "_save_autonomy_chatroom_message", AsyncMock())
    monkeypatch.setattr(chatroom_routes, "_load_room_and_messages", AsyncMock(return_value=({}, [])))
    monkeypatch.setattr(autonomy, "append_idle_event", AsyncMock())
    reply = AsyncMock()
    monkeypatch.setattr(autonomy, "_reply_in_group", reply)
    items = [{"index": 1, "kind": "moment", "author": "ai4", "title": "小四发了条朋友圈"}]
    monkeypatch.setattr(autonomy, "_home_item_by_index", lambda *a, **k: items[0])
    asyncio.run(autonomy._run_home_group_tease("ai3", {"target_index": 1, "group_message": "哈哈"}, items, ""))
    assert reply.await_args.args[0] == "ai4"


# ── 小红书 ──

def test_xhs_knows_seats(family, monkeypatch, tmp_path):
    import xhs_lite
    monkeypatch.setattr(xhs_lite, "CONFIG_PATH", tmp_path / "xhs.json")
    import actors
    monkeypatch.setattr(actors, "display_name", lambda a: {"ai3": "狗蛋", "ai4": "小四"}.get(a, a))
    assert xhs_lite.actor_label("ai3") == "狗蛋"
    labels = xhs_lite.public_config()["actor_labels"]
    assert labels.get("ai3") == "狗蛋" and "ai5" not in labels  # 没准备好的不列
    xhs_lite.save_config({"actors": {"ai3": {"enabled": False}, "someone": {"enabled": True}}})
    cfg = xhs_lite.load_config()
    assert cfg["actors"]["ai3"] == {"enabled": False} and "someone" not in cfg["actors"]
    xhs_lite.save_config({"enabled": True, "auto_enabled": True, "cookie": "c", "target_user_id": "u",
                          "actors": {"ai3": {"enabled": True}}})
    assert xhs_lite.is_ready_for_auto("ai3") is True
    assert xhs_lite.is_ready_for_auto("ai5") is False  # 座位没准备好
    with pytest.raises(RuntimeError, match="还没准备好"):
        asyncio.run(xhs_lite.run_actor_roam("ai5"))
