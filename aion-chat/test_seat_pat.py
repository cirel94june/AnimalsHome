"""狗蛋补能力第三批：座位的拍一拍——能发起、能被拍；私聊只限小猫和这位座位，群里是家里人。"""

import asyncio
from unittest.mock import AsyncMock

import aiosqlite
import pytest
from fastapi import HTTPException

import capabilities
import routes.chatroom as chatroom_routes


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "p.db"

    async def setup():
        async with aiosqlite.connect(path) as conn:
            await conn.execute("CREATE TABLE conversations (id TEXT PRIMARY KEY, updated_at REAL)")
            await conn.execute("CREATE TABLE chatroom_rooms (id TEXT PRIMARY KEY, type TEXT, actor TEXT DEFAULT '', updated_at REAL)")
            await conn.execute("CREATE TABLE messages (id TEXT, conv_id TEXT, role TEXT, content TEXT, created_at REAL, attachments TEXT)")
            await conn.execute("CREATE TABLE chatroom_messages (id TEXT, room_id TEXT, sender TEXT, content TEXT, created_at REAL, attachments TEXT)")
            for rid, kind, actor in (("g1", "group", ""), ("s3", "seat_1v1", "ai3"), ("s4", "seat_1v1", "ai4"), ("c1", "connor_1v1", "")):
                await conn.execute("INSERT INTO chatroom_rooms VALUES (?,?,?,0)", (rid, kind, actor))
            await conn.commit()
    asyncio.run(setup())
    monkeypatch.setattr(capabilities, "get_db", lambda: aiosqlite.connect(path))
    monkeypatch.setattr(capabilities.manager, "broadcast", AsyncMock())
    monkeypatch.setattr(capabilities, "_pat_names", lambda: {"user": "小猫", "aion": "小克", "connor": "Lucien",
                                                            "ai3": "狗蛋", "ai4": "四号", "ai5": "ai5", "ai6": "ai6"})
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: True)
    return path


def _text(room, text):
    return capabilities.PatTextRequest(scope="chatroom", source_id=room, text=text)


def test_seat_pats_in_the_family_group_and_its_own_room(db):
    msg = asyncio.run(capabilities.save_pat(_text("g1", "拍了拍「小克」的头"), actor="ai3"))
    assert msg["content"] == "「狗蛋」拍了拍「小克」的头" and msg["attachments"][0]["actor"] == "ai3"
    assert asyncio.run(capabilities.save_pat(_text("s3", "揉了揉「小猫」"), actor="ai3"))["room_id"] == "s3"
    for room in ("s4", "c1"):  # 别的座位私聊、Lucien 私聊里不能拍
        with pytest.raises(HTTPException):
            asyncio.run(capabilities.save_pat(_text(room, "x"), actor="ai3"))


def test_user_can_double_click_a_seat_avatar(db):
    req = capabilities.PatRequest(scope="chatroom", source_id="g1", target="ai3")
    assert asyncio.run(capabilities.save_pat(req))["content"] == "「小猫」拍了拍「狗蛋」"
    req = capabilities.PatRequest(scope="chatroom", source_id="s3", target="ai3")
    assert asyncio.run(capabilities.save_pat(req))
    with pytest.raises(HTTPException):  # 狗蛋私聊里没有小克
        asyncio.run(capabilities.save_pat(capabilities.PatRequest(scope="chatroom", source_id="s3", target="aion")))


def test_seat_pat_command_is_processed_and_shown_in_history(db, monkeypatch):
    monkeypatch.setattr(chatroom_routes, "is_capability_enabled", lambda name: True)
    q = asyncio.Queue()
    text, triggered = asyncio.run(chatroom_routes._process_seat_commands("嘿[PAT:戳了戳「小猫」的脸]", "s3", "ai3", "m1", q))
    assert text == "嘿" and triggered["pat"]
    event = q.get_nowait()
    assert event["type"] == "system_msg" and event["message"]["content"] == "「狗蛋」戳了戳「小猫」的脸"
    assert capabilities.pat_history_command(event["message"], event["message"]["attachments"]) == ("ai3", "[PAT:戳了戳「小猫」的脸]")


def test_seat_prompt_offers_pat(monkeypatch):
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: name == "pat")
    assert any(t.startswith("[PAT:") for t in capabilities.seat_ability_texts("小猫"))


def test_ai_free_text_pat_cannot_reach_someone_not_in_the_chat(db):
    """Codex M2：AI 写的拍拍里点到的家里人必须在当前聊天里。"""
    with pytest.raises(HTTPException):  # 狗蛋私聊里拍四号
        asyncio.run(capabilities.save_pat(_text("s3", "拍了拍「四号」的头"), actor="ai3"))
    with pytest.raises(HTTPException):  # Lucien 私聊里拍小克
        asyncio.run(capabilities.save_pat(_text("c1", "拍了拍「小克」"), actor="connor"))
    assert asyncio.run(capabilities.save_pat(_text("g1", "拍了拍「四号」的头"), actor="ai3"))  # 群里可以
    assert asyncio.run(capabilities.save_pat(_text("s3", "揉了揉「狗蛋」自己的「小熊玩偶」"), actor="ai3"))  # 物件放行


def test_double_click_context_knows_seat_names():
    js = open("static/chatroom.js", encoding="utf-8").read()
    ctx = js[js.index("window.AionPat?.bind({"):js.index("function applyChatroomNames")]
    assert "Object.keys(crSeats).map(id => [id, crName(id)])" in ctx
