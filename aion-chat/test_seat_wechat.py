"""④ 微信：狗蛋等座位和小克 / Lucien 一样能发微信消息；微信模式也转发 TA 的话。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

import capabilities
import routes.chatroom as chatroom_routes
import wechat_bridge
import wechat_mode_dispatcher


@pytest.fixture
def room(monkeypatch):
    notes = AsyncMock(return_value={"id": "sys1"})
    monkeypatch.setattr(chatroom_routes, "_chatroom_sys_msg", notes)
    monkeypatch.setattr(chatroom_routes, "_name_for_identity", lambda a: "狗蛋")
    sent = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "dispatch_wechat_message", sent)
    routes_seen = []
    monkeypatch.setattr(chatroom_routes, "record_wechat_route", routes_seen.append)
    import actors
    monkeypatch.setattr(actors, "display_name", lambda a: "狗蛋")
    from config import SETTINGS
    monkeypatch.setitem(SETTINGS, "wechat_modes", {})
    return notes, sent, routes_seen


def _run(text):
    return asyncio.run(chatroom_routes._process_seat_commands(text, "s3", "ai3", "m1", asyncio.Queue()))


def test_seat_sends_wechat_under_its_own_name(room, monkeypatch):
    notes, sent, routes_seen = room
    monkeypatch.setattr(chatroom_routes, "is_capability_enabled", lambda name: True)
    text, triggered = _run("你去哪了[微信消息：快回来吃饭]")
    assert text == "你去哪了" and triggered["wechat_messages"] == ["快回来吃饭"]
    kwargs = sent.await_args.kwargs
    assert kwargs["content"] == "狗蛋：快回来吃饭" and kwargs["sender"] == "ai3" and kwargs["source_id"] == "s3"
    assert routes_seen[0]["source_id"] == "s3"  # 她从微信回话会回到狗蛋这里
    assert "狗蛋：快回来吃饭" in notes.await_args.args[1]


def test_switch_off_only_strips(room, monkeypatch):
    notes, sent, _ = room
    monkeypatch.setattr(chatroom_routes, "is_capability_enabled", lambda name: name != "wechat_message")
    text, triggered = _run("嗯[微信消息：快回来]")
    assert text == "嗯" and "wechat_messages" not in triggered
    sent.assert_not_called()


def test_prompt_offers_wechat_to_seats(monkeypatch):
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: name == "wechat_message")
    assert any(t.startswith("[微信消息：内容]") for t in capabilities.seat_ability_texts("小猫"))


def test_wechat_mode_forwards_seat_messages_with_its_name(monkeypatch):
    event = {"type": "chatroom_msg_created", "data": {"id": "m1", "room_id": "s3", "sender": "ai3"}}
    assert wechat_mode_dispatcher.extract_wechat_event_route(event) == ("chatroom", "s3", "m1", "ai3")
    import actors
    monkeypatch.setattr(actors, "display_name", lambda a: "狗蛋")
    import chatroom
    monkeypatch.setattr(chatroom, "get_chatroom_names", lambda: ("小猫", "小克", "Lucien"))
    import database
    monkeypatch.setattr(database, "get_db", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    _, name = asyncio.run(wechat_mode_dispatcher._default_identity_resolver("chatroom", "s3", "ai3"))
    assert name == "狗蛋"
    assert wechat_bridge.resolve_wechat_sender_label("chatroom", "ai3") == "狗蛋"
