"""群聊 @ 和发言顺序（11-07 小猫）。

她只 @ 狗蛋就只有狗蛋回；AI 回话 @ 别人，被 @ 的接着说；AI 互相 @ 有上限；所有家人都能排顺序。
"""
import asyncio
from pathlib import Path

import pytest

import actors
import group_mentions as gm
from routes import chatroom


@pytest.fixture
def family(tmp_path, monkeypatch):
    monkeypatch.setattr(actors, "ACTORS_PATH", tmp_path / "actors.json")
    monkeypatch.setattr(actors, "load_worldbook", lambda: {"ai_name": "小克"})
    monkeypatch.setattr(actors, "_connor_name", lambda: "Lucien")
    actors._save_raw({"seats": {"ai3": {"name": "Jasper", "aliases": ["狗蛋"]},
                                "connor": {"aliases": ["狐狸"]}, "aion": {"aliases": ["Cloudy"]}}})
    return ["aion", "connor", "ai3"]


@pytest.mark.parametrize("text,expect", [
    ("@狗蛋 来玩海龟汤", ["ai3"]),
    ("@Jasper 你呢", ["ai3"]),
    ("＠狗蛋 全角也行", ["ai3"]),
    ("@ 狗蛋 中间有空格", ["ai3"]),
    ("@狐狸 和 @小克 你们俩说", ["connor", "aion"]),
    ("@lucien 小写", ["connor"]),
    ("@cloudy", ["aion"]),
    ("@狗蛋 @狗蛋 重复只算一次", ["ai3"]),
    ("我的邮箱是 a@狗蛋.com", []),       # 前面是字母数字：不是 @ 人
    ("@小猫 自己", []),
    ("没有艾特", []),
])
def test_find_mentions(family, text, expect):
    assert gm.find_mentions(text, family) == expect


def test_only_present_members_count(family):
    assert gm.find_mentions("@狗蛋", ["aion", "connor"]) == []  # 狗蛋没准备好，不在群里


def test_clean_order(family):
    assert gm.clean_order(["ai3", "aion", "ai9", "ai3"], family) == ["ai3", "aion", "connor"]


def test_turn_mention_moves_up_without_counting(family):
    turn = gm.Turn(["aion", "connor", "ai3"], family)
    assert turn.next() == "aion"
    assert turn.heard("aion", "@狗蛋 你先说") == ["ai3"]
    assert turn.queue == ["ai3", "connor"] and turn.relays == 0


def test_turn_spoken_again_counts_and_is_capped(family):
    turn = gm.Turn(["aion", "connor"], family)
    spoken = []
    while (actor := turn.next()) is not None and len(spoken) < 20:
        spoken.append(actor)
        other = "@Lucien 你说" if actor == "aion" else "@小克 你说"
        turn.heard(actor, other)  # 两个一直互相 @
    assert spoken == ["aion", "connor", "aion", "connor", "aion"]  # 原来 2 次 + 最多再接 3 次
    assert turn.relays == gm.MAX_RELAY


def test_turn_ignores_self_mention(family):
    turn = gm.Turn(["aion"], family)
    turn.next()
    assert turn.heard("aion", "@小克 我自己") == [] and turn.queue == []


# ── 群聊里真的这么走 ──

@pytest.fixture
def group(family, monkeypatch):
    spoken, said = [], {}
    cfg = {"reply_order": "random"}

    async def digest(*a, **k):
        return {"first_responder": "aion"}

    async def load(room_id, limit=50):
        return {"id": room_id, "type": "group"}, [{"sender": "user", "content": "hi"}]

    async def reply_aion(room_id, msgs, *a, **k):
        spoken.append("aion")
        return k.get("digest_result")

    async def reply_connor(room_id, msgs, *a, **k):
        spoken.append("connor")
        return k.get("digest_result")

    async def seat(room_id, room, msgs, q, limit, *, actor="", group=False, **k):
        spoken.append(actor)

    async def latest(room_id, sender, since):
        lines = said.get(sender) or []
        return lines.pop(0) if lines else ""

    monkeypatch.setattr(chatroom, "instant_digest", digest)
    monkeypatch.setattr(chatroom, "_load_room_and_messages", load)
    monkeypatch.setattr(chatroom, "_reply_aion", reply_aion)
    monkeypatch.setattr(chatroom, "_reply_connor", reply_connor)
    monkeypatch.setattr(chatroom, "_generate_seat_reply", seat)
    monkeypatch.setattr(chatroom, "_latest_text_from", latest)
    monkeypatch.setattr(chatroom, "_group_seat_members", lambda: ["ai3"])
    monkeypatch.setattr(chatroom, "load_chatroom_config", lambda: cfg)

    def run(text):
        spoken.clear()
        msgs = [{"sender": "user", "content": text}]
        asyncio.run(chatroom._generate_group_replies("g1", {"id": "g1", "type": "group"}, msgs, "m", "Codex",
                                                     asyncio.Queue(), 30))
        return list(spoken)
    return run, said, cfg


def test_default_order_unchanged(group):
    run, _, _ = group
    assert run("大家好") == ["aion", "connor", "ai3"]


def test_she_mentions_only_jasper(group):
    run, _, _ = group
    assert run("@狗蛋 来一局海龟汤") == ["ai3"]


def test_she_mentions_two(group):
    run, _, _ = group
    assert run("@狗蛋 @小克 你们说") == ["ai3", "aion"]


def test_custom_order(group):
    run, _, cfg = group
    cfg.update(reply_order="custom", speak_order=["ai3", "connor"])
    assert run("大家好") == ["ai3", "connor", "aion"]


def test_ai_mention_brings_someone_back(group):
    run, said, _ = group
    said["ai3"] = ["@小克 你刚才说的不对"]
    assert run("大家好") == ["aion", "connor", "ai3", "aion"]


def test_ai_ping_pong_is_capped(group):
    run, said, _ = group
    said["aion"] = ["@狐狸"] * 10
    said["connor"] = ["@小克"] * 10
    assert run("@小克 开始") == ["aion", "connor", "aion", "connor"]  # 她 @ 的 1 次 + 最多再接 3 次


# ── 设置、提示 ──

def test_settings_accept_custom_order():
    cfg = chatroom._apply_config_update({}, chatroom.ConfigUpdate(reply_order="custom",
                                                                  speak_order=["ai3", "evil", "aion", "ai3"]))
    assert cfg["reply_order"] == "custom" and cfg["speak_order"] == ["ai3", "aion"]


def test_everyone_is_told_they_can_mention(family, monkeypatch):
    import seat_chat
    monkeypatch.setattr(seat_chat, "SEAT_ACTORS", ("ai3",))
    monkeypatch.setattr(seat_chat, "check_ready", lambda a: ("x", "m"))
    hint = gm.hint_for("ai3")
    assert "@小克" in hint and "Jasper" not in hint
    root = Path(__file__).parent
    assert root.joinpath("chatroom.py").read_text(encoding="utf-8").count("group_mentions.hint_for(") == 2
    assert "group_mentions.hint_for(actor_id)" in root.joinpath("seat_chat.py").read_text(encoding="utf-8")


def test_page_wired():
    root = Path(__file__).parent / "static"
    html = (root / "chatroom.html").read_text(encoding="utf-8")
    js = (root / "chatroom.js").read_text(encoding="utf-8")
    assert '<option value="custom">按我排的顺序</option>' in html and 'id="mentionPicker"' in html
    assert "speak_order: crSpeakOrder.slice()" in js and "crUpdateMentionPicker" in js
    assert "chatroom.js?v=20261107-mention" in html
