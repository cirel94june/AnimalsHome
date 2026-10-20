"""狗蛋补能力第三批：座位钱包——各自一本账；小猫在 TA 私聊里转账进 TA 的钱包；TA 转出余额不够就不转。"""

import asyncio
from unittest.mock import AsyncMock

import aiosqlite
import pytest
from fastapi import HTTPException

import routes.chatroom as chatroom_routes
import routes.seat_wallet as seat_wallet


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "w.db"

    async def setup():
        async with aiosqlite.connect(path) as conn:
            await conn.execute("CREATE TABLE bookkeeping (id TEXT PRIMARY KEY, record_type TEXT, amount REAL, "
                               "description TEXT, created_at REAL)")
            await conn.execute("CREATE TABLE chatroom_rooms (id TEXT PRIMARY KEY, type TEXT, actor TEXT DEFAULT '')")
            for rid, kind, actor in (("g1", "group", ""), ("s3", "seat_1v1", "ai3"), ("c1", "connor_1v1", "")):
                await conn.execute("INSERT INTO chatroom_rooms VALUES (?,?,?)", (rid, kind, actor))
            await conn.commit()
    asyncio.run(setup())
    connect = lambda: aiosqlite.connect(path)  # noqa: E731
    monkeypatch.setattr(seat_wallet, "get_db", connect)
    monkeypatch.setattr(chatroom_routes, "get_db", connect)
    import ws
    monkeypatch.setattr(ws.manager, "broadcast", AsyncMock())
    monkeypatch.setattr(chatroom_routes, "_name_for_identity", lambda a: {"ai3": "狗蛋", "ai4": "四号"}.get(a, a))
    return path


def test_each_seat_has_its_own_balance_and_cannot_overspend(db):
    async def go():
        assert not await seat_wallet.pay_user("ai3", 5, "x")  # 空钱包转不出去
        await seat_wallet.credit_from_user("ai3", 20, "小猫给狗蛋")
        await seat_wallet.credit_from_user("ai4", 7, "小猫给四号")
        assert await seat_wallet.pay_user("ai3", 15, "狗蛋给小猫")
        assert not await seat_wallet.pay_user("ai3", 6, "再给")  # 只剩 5
        return await seat_wallet.get_seat_balance("ai3"), await seat_wallet.get_seat_balance("ai4")
    assert asyncio.run(go()) == (5.0, 7.0)


def test_wallet_api_only_for_seats(db):
    assert asyncio.run(seat_wallet.seat_balance("ai3")) == {"balance": 0.0}
    with pytest.raises(HTTPException):
        asyncio.run(seat_wallet.seat_balance("connor"))
    asyncio.run(seat_wallet.credit_from_user("ai3", 3, "a"))
    txs = asyncio.run(seat_wallet.seat_transactions("ai3"))
    assert [t["record_type"] for t in txs] == ["seat_wallet_user:ai3"]


def test_user_transfer_goes_to_the_right_wallet(db):
    names = {"aion": "小克", "connor": "Lucien", "ai3": "狗蛋", "ai4": "四号"}
    route = lambda r, seat="": chatroom_routes._transfer_recipient(r, seat, names)  # noqa: E731
    assert asyncio.run(chatroom_routes._room_seat("s3")) == "ai3"
    assert asyncio.run(chatroom_routes._room_seat("g1")) == ""
    assert route("", "ai3") == "ai3"            # 狗蛋私聊里没点名 → 狗蛋（以前会进 Lucien 的钱包）
    assert route("狗蛋") == "ai3"               # 群里点名狗蛋 → 狗蛋
    assert route("小克", "ai3") == "aion"       # 在狗蛋私聊里点名小克 → 小克
    assert route("") == "connor"                # 群里没点名 → 照旧 Lucien
    assert route("不认识的人") == "connor"


def test_duplicate_names_never_send_money_to_the_wrong_one(db):
    """Codex M3：同名时按按钮带的身份认；手写重名的名字就不转。"""
    names = {"aion": "小克", "connor": "Lucien", "ai3": "宝宝", "ai4": "宝宝", "ai5": "小克"}
    route = lambda r, seat="": chatroom_routes._transfer_recipient(r, seat, names)  # noqa: E731
    assert route("宝宝#ai3") == "ai3" and route("宝宝#ai4") == "ai4"
    assert route("小克#aion") == "aion" and route("小克#ai5") == "ai5"
    assert route("宝宝") == "ambiguous" and route("小克") == "ambiguous"


def test_small_amounts_are_compared_in_cents(db):
    """Codex L1：存 0.30、转 0.10 后还能再转 0.20。"""
    async def go():
        await seat_wallet.credit_from_user("ai3", 0.30, "x")
        assert await seat_wallet.pay_user("ai3", 0.10, "a")
        assert await seat_wallet.pay_user("ai3", 0.20, "b")
        assert not await seat_wallet.pay_user("ai3", 0.01, "c")
    asyncio.run(go())


def test_seat_transfer_command_respects_balance(db, monkeypatch):
    monkeypatch.setattr(chatroom_routes, "is_capability_enabled", lambda name: True)
    notes = AsyncMock(return_value={"id": "sys"})
    monkeypatch.setattr(chatroom_routes, "_chatroom_sys_msg", notes)
    text, triggered = asyncio.run(chatroom_routes._process_seat_commands("给你[转账：10元]", "s3", "ai3", "m1", asyncio.Queue()))
    assert text == "给你" and "transfer" not in triggered and "余额不够" in notes.await_args.args[1]
    asyncio.run(seat_wallet.credit_from_user("ai3", 30, "x"))
    text, triggered = asyncio.run(chatroom_routes._process_seat_commands("给你[转账：10元]", "s3", "ai3", "m2", asyncio.Queue()))
    assert "[转账：10元]" in text and triggered["transfer"]  # 标签留着，前端显示转账卡片
    assert asyncio.run(seat_wallet.get_seat_balance("ai3")) == 20.0


def test_seat_prompt_shows_its_own_balance(db, monkeypatch):
    import capabilities
    import memory_hub_bridge
    import seat_chat
    asyncio.run(seat_wallet.credit_from_user("ai3", 12.5, "x"))
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: name == "transfer")
    monkeypatch.setattr(memory_hub_bridge, "context_block", AsyncMock(return_value=""))
    monkeypatch.setattr(seat_chat, "_persona_text", lambda a: "人设")
    monkeypatch.setattr(seat_chat.actors, "get_actor", lambda a: {"name": "狗蛋"})
    built = asyncio.run(seat_chat.build_context("ai3", [], group=False))
    text = "\n".join(m["content"] for m in built)
    assert "[转账：n元]" in text and "12.50元" in text


def test_page_points_wallet_and_transfer_at_the_seat():
    js = open("static/chatroom.js", encoding="utf-8").read()
    assert "/api/seat-wallet/" in js and "currentRoom?.type === 'seat_1v1'" in js and "seat_wallet_update" in js


def test_group_transfer_dialog_has_a_button_per_enabled_seat():
    js = open("static/chatroom.js", encoding="utf-8").read()
    open_dialog = js[js.index("function crOpenTransferDialog"):js.index("function crSwitchTransferTarget")]
    assert "filter(a => a.enabled)" in open_dialog and "b.dataset.target = a.id" in open_dialog
    assert "querySelectorAll('[data-seat]').forEach(b => b.remove())" in open_dialog  # 每次打开重建，不会越加越多
    switch = js[js.index("function crSwitchTransferTarget"):js.index("function crCloseTransferDialog")]
    assert "crName(target)" in switch and "b.dataset.target === target" in switch
    confirm = js[js.index("function crConfirmTransfer"):js.index("async function crOpenWalletPanel")]
    assert "crName(crTransferTarget)" in confirm  # 选了狗蛋就写成 [转账给狗蛋：n元]
    assert "flex-wrap: wrap" in open("static/chatroom.css", encoding="utf-8").read()


def test_transfer_tag_carries_the_id_but_the_card_hides_it():
    js = open("static/chatroom.js", encoding="utf-8").read()
    assert "#${crTransferTarget}：${n}元]" in js
    render = js[js.index("function renderTransferCards"):js.index("function renderTransferCards") + 600]
    assert ".replace(/#(aion|connor|ai[3-6])$/, '')" in render
