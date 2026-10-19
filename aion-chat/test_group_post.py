"""狗蛋补能力第一批：私聊里去群里说、座位悄悄话、座位私聊 + 群聊合并时间线。"""

import asyncio
from unittest.mock import AsyncMock

import aiosqlite
import pytest

import database
import group_post
import routes.chatroom as chatroom_routes
import seat_chat
from safe_live_stream import KnownCommandStreamFilter


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "chat.db"

    async def setup():
        async with aiosqlite.connect(path) as conn:
            await conn.execute("CREATE TABLE chatroom_rooms (id TEXT PRIMARY KEY, title TEXT, type TEXT, actor TEXT DEFAULT '', "
                               "created_at REAL, updated_at REAL)")
            await conn.execute("CREATE TABLE chatroom_messages (id TEXT PRIMARY KEY, room_id TEXT, sender TEXT, content TEXT, "
                               "created_at REAL, attachments TEXT)")
            rooms = [("g_old", "group", "", 1), ("g_new", "group", "", 5), ("billiards_x", "group", "", 9),
                     ("s3", "seat_1v1", "ai3", 2), ("s4", "seat_1v1", "ai4", 2), ("c1", "connor_1v1", "", 2)]
            for rid, kind, actor, updated in rooms:
                await conn.execute("INSERT INTO chatroom_rooms VALUES (?,?,?,?,?,?)", (rid, rid, kind, actor, 0, updated))
            msgs = [("m1", "s3", "user", "私聊里说的心事", 10), ("m2", "g_new", "aion", "群里小克说话", 11),
                    ("m3", "s4", "user", "给别人的私聊", 12), ("m4", "c1", "user", "Lucien 的私聊", 13),
                    ("m5", "g_old", "user", "旧群里小猫说话", 14), ("m6", "s3", "ai3", "狗蛋私聊回复", 15),
                    ("m7", "billiards_x", "user", "台球房", 16), ("m8", "g_new", "system", "系统消息", 17),
                    ("m9", "s3", "user", "最新一句", 18)]
            for mid, room, sender, content, ts in msgs:
                await conn.execute("INSERT INTO chatroom_messages VALUES (?,?,?,?,?,?)", (mid, room, sender, content, ts, "[]"))
            await conn.commit()
    asyncio.run(setup())
    monkeypatch.setattr(database, "get_db", lambda: aiosqlite.connect(path))
    return path


def test_seat_timeline_merges_own_private_and_all_family_groups(db):
    merged = asyncio.run(seat_chat.merged_messages("ai3", 30))
    assert [(m["content"], m["source"]) for m in merged] == [
        ("私聊里说的心事", "private"), ("群里小克说话", "group"), ("旧群里小猫说话", "group"),
        ("狗蛋私聊回复", "private"), ("最新一句", "private")]  # 不含别的座位、Lucien 私聊、台球房、系统消息


def test_seat_history_labels_group_and_private_lines():
    msgs = [{"sender": "user", "content": "私聊", "created_at": 1, "source": "private"},
            {"sender": "aion", "content": "群里", "created_at": 2, "source": "group"},
            {"sender": "ai3", "content": "我说", "created_at": 3, "source": "group"},
            {"sender": "user", "content": "又私聊", "created_at": 4, "source": "private"}]
    out = seat_chat._history("ai3", msgs, 30, user_name="小猫")
    assert out[0]["role"] == "user" and "私聊" in out[0]["content"] and "[私聊]" in out[0]["content"]
    assert "【" in out[0]["content"] and "群里" in out[0]["content"] and "[群聊]" in out[0]["content"]
    assert out[1] == {"role": "assistant", "content": "我说"}
    assert "又私聊" in out[2]["content"] and "[私聊]" in out[2]["content"]


def test_group_post_goes_to_the_latest_family_group_and_is_stripped(db, monkeypatch):
    saved = []

    async def save(room_id, sender, content, msg_id=None, **kw):
        saved.append((room_id, sender, content))
    monkeypatch.setattr(chatroom_routes, "_save_msg", save)
    text = asyncio.run(group_post.process("好，我去说。[去群里：今晚一起看电影吗][去群里：第二条]", "ai3"))
    assert text == "好，我去说。"
    assert saved == [("g_new", "ai3", "今晚一起看电影吗")]  # 只发第一条，发到最近的家里群（不选台球房）


def test_group_post_without_a_family_group_only_strips(tmp_path, monkeypatch):
    monkeypatch.setattr(group_post, "_family_group_room", AsyncMock(return_value=""))
    save = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_save_msg", save)
    assert asyncio.run(group_post.process("嗯[去群里：x]", "aion")) == "嗯"
    save.assert_not_called()


def test_group_post_rule_keeps_private_matters_out():
    text = group_post.ability_text("小猫")
    assert "[去群里：内容]" in text and "私事" in text and "明确让你去说" in text and "拿不准就不说" in text


def test_stream_hides_the_group_post_command():
    f = KnownCommandStreamFilter()
    shown = "".join(f.feed(chunk) for chunk in ("好的", "[去群", "里：今晚看电影", "]", "就这样"))
    assert "去群里" not in shown and "今晚看电影" not in shown and "好的" in shown


def _stream(*chunks):
    async def gen(*args, **kwargs):
        for chunk in chunks:
            yield chunk
    return gen


def _run_seat(monkeypatch, text, group):
    monkeypatch.setattr(seat_chat, "check_ready", lambda a: ({"name": "狗蛋"}, "m"))
    monkeypatch.setattr(seat_chat, "merged_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(seat_chat, "build_context", AsyncMock(return_value=[{"role": "user", "content": "x"}]))
    monkeypatch.setattr(chatroom_routes, "stream_ai", _stream(text))
    monkeypatch.setattr(chatroom_routes, "process_note_commands", AsyncMock(side_effect=lambda t, a: t))
    saved = []

    async def save(room_id, sender, content, msg_id=None, **kw):
        saved.append((room_id, sender, content))
        return {"id": msg_id}
    monkeypatch.setattr(chatroom_routes, "_save_msg", save)
    whisper = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_send_private_whisper", whisper)
    post = AsyncMock(side_effect=lambda t, a: (group_post.GROUP_POST_CMD_PATTERN.sub("", t).strip(),
                                               bool(group_post.GROUP_POST_CMD_PATTERN.search(t))))
    monkeypatch.setattr(group_post, "process_with_result", post)
    sys_msgs = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_chatroom_sys_msg", sys_msgs)
    failure = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_save_chatroom_reply_failure", failure)
    import config
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "m": {"provider": "custom_openai", "model": "m"}})

    q = asyncio.Queue()

    async def run2():
        await chatroom_routes._generate_seat_reply("room", {"actor": "ai3"}, [], q, 30, actor="ai3", group=group)
    asyncio.run(run2())
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    _run_seat.last = {"sys": sys_msgs, "failure": failure, "events": events}
    return saved, whisper, post


def test_seat_can_whisper_from_the_group(monkeypatch):
    saved, whisper, post = _run_seat(monkeypatch, "大家好[悄悄话：只跟你说]", group=True)
    whisper.assert_awaited_once_with("ai3", "只跟你说")
    post.assert_not_called()
    assert saved[-1][2] == "大家好"


def test_seat_can_post_to_the_group_from_private(monkeypatch):
    saved, whisper, post = _run_seat(monkeypatch, "好[去群里：大家晚安]", group=False)
    post.assert_awaited_once()
    whisper.assert_not_called()
    assert saved[-1][2] == "好"


def test_seat_whisper_is_delivered_to_the_seats_own_private_room(monkeypatch):
    rooms = AsyncMock(return_value="s3")
    save = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_get_or_create_seat_room", rooms)
    monkeypatch.setattr(chatroom_routes, "_save_msg", save)
    asyncio.run(chatroom_routes._send_private_whisper("ai3", " 悄悄说 "))
    rooms.assert_awaited_once_with("ai3")
    assert save.await_args.args[:3] == ("s3", "ai3", "悄悄说")


def test_seats_see_the_new_instructions():
    lines = seat_chat._ability_lines("小猫", group=True)
    assert any("[悄悄话" in line for line in lines) and not any("[去群里" in line for line in lines)
    lines = seat_chat._ability_lines("小猫", group=False)
    assert any("[去群里" in line for line in lines) and not any("[悄悄话" in line for line in lines)


def test_private_chats_of_xiaoke_and_lucien_offer_group_post():
    import inspect
    import chatroom
    import routes.chat as chat_routes
    assert "include_group_post=True" in inspect.getsource(chat_routes._insert_private_ability_block)
    assert "include_group_post=True" in inspect.getsource(chatroom.build_connor_1v1_context)
    assert inspect.getsource(chat_routes).count('group_post.process(full_text, "aion")') == 3


def test_pure_command_reply_is_not_recorded_as_a_failure(monkeypatch):
    """Codex L2：只发了「去群里 / 悄悄话」没说别的，不记成失败，留一句说明并正常结束。"""
    saved, whisper, post = _run_seat(monkeypatch, "[去群里：大家晚安]", group=False)
    last = _run_seat.last
    last["failure"].assert_not_called()
    assert "去群里说了一句" in last["sys"].await_args.args[1]
    assert last["events"][-1] == {"type": "seat_done", "actor": "ai3", "message": None}
    assert saved == []
    saved, whisper, post = _run_seat(monkeypatch, "[悄悄话：只跟你说]", group=True)
    _run_seat.last["failure"].assert_not_called()
    assert "悄悄话" in _run_seat.last["sys"].await_args.args[1]


def test_switching_group_post_off_stops_sending_everywhere(monkeypatch):
    """Codex M2：开关关着时，小克 / Lucien / 座位的指令都只去掉、不发。"""
    import capabilities
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: name != "group_post")
    save = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_save_msg", save)
    monkeypatch.setattr(group_post, "_family_group_room", AsyncMock(return_value="g"))
    for actor in ("aion", "connor", "ai3"):
        assert asyncio.run(group_post.process_with_result("嗯[去群里：x]", actor)) == ("嗯", False)
    save.assert_not_called()


def test_failing_room_lookup_never_breaks_the_private_reply(monkeypatch):
    """Codex L1：查家里群出错时，私聊这条回复照常（指令去掉）。"""
    monkeypatch.setattr(group_post, "_family_group_room", AsyncMock(side_effect=RuntimeError("db")))
    assert asyncio.run(group_post.process_with_result("好[去群里：x]", "aion")) == ("好", False)


def test_every_group_context_carries_the_private_matters_rule(monkeypatch):
    """Codex M1：群里看得到私聊记录，所以每个群聊说明都要带「私聊私事不在群里说」。"""
    import inspect
    import chatroom
    rule = group_post.group_privacy_rule("小猫")
    assert "亲密关系" in rule and "身体" in rule and "私人情感" in rule and "她自己在群里说了" in rule and "自己判断" in rule
    for name in ("build_aion_group_context", "build_connor_group_context"):
        assert "group_privacy_rule(user_name)" in inspect.getsource(getattr(chatroom, name))
    import memory_hub_bridge
    monkeypatch.setattr(memory_hub_bridge, "context_block", AsyncMock(return_value=""))
    monkeypatch.setattr(seat_chat, "_persona_text", lambda a: "人设")
    monkeypatch.setattr(seat_chat.actors, "get_actor", lambda a: {"name": "狗蛋"})
    msgs = [{"sender": "user", "content": "PRIVATE_SENTINEL", "created_at": 1, "source": "private"}]
    built = asyncio.run(seat_chat.build_context("ai3", msgs, group=True))
    text = "\n".join(m["content"] for m in built)
    assert "PRIVATE_SENTINEL" in text and "亲密关系" in text
