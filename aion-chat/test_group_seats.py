"""群聊里狗蛋等座位也会接话：小克、Lucien 说完后，选好模型的座位按顺序发言，并能看到前面所有人的话。"""

import asyncio

import routes.chatroom as rc


def test_group_replies_include_ready_seats_after_the_pair(monkeypatch):
    order = []
    loads = {"n": 0}

    async def reply_aion(*a, **k):
        order.append("aion")
        return {}

    async def reply_connor(*a, **k):
        order.append("connor")
        return {}

    async def digest(*a, **k):
        return {"first_responder": "aion"}

    async def load(room_id):
        loads["n"] += 1
        return {"id": room_id, "type": "group"}, [{"sender": "user", "content": "hi", "n": loads["n"]}]

    seen = []

    async def seat_reply(room_id, room, msgs, _q, context_limit, *, actor="", group=False):
        order.append(actor)
        seen.append((group, msgs[-1]["n"]))

    monkeypatch.setattr(rc, "_reply_aion", reply_aion)
    monkeypatch.setattr(rc, "_reply_connor", reply_connor)
    monkeypatch.setattr(rc, "instant_digest", digest)
    monkeypatch.setattr(rc, "_load_room_and_messages", load)
    monkeypatch.setattr(rc, "_generate_seat_reply", seat_reply)
    monkeypatch.setattr(rc, "_group_seat_members", lambda: ["ai3", "ai4"])
    monkeypatch.setattr(rc, "load_chatroom_config", lambda: {"reply_order": "aion"})
    monkeypatch.setattr(rc, "get_chatroom_names", lambda: ("小猫", "小克", "Lucien"))

    asyncio.run(rc._generate_group_replies("r1", {}, [{"sender": "user", "content": "hi"}], "m", "c", None, 30))
    assert order == ["aion", "connor", "ai3", "ai4"]
    assert all(group for group, _ in seen)
    assert seen[1][1] > seen[0][1]  # 每位接话前都重新读了消息，能看到前一位刚说的


def test_seats_without_a_model_are_skipped(monkeypatch):
    import seat_chat

    def check(actor):
        if actor != "ai3":
            raise seat_chat.SeatNotReady("没选模型")
        return {}, "m"
    monkeypatch.setattr(seat_chat, "check_ready", check)
    assert rc._group_seat_members() == ["ai3"]
