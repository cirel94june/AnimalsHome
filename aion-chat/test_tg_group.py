import pytest
from fastapi import HTTPException

import tg_group


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(tg_group, "DB_PATH", tmp_path / "tg.db")


def msg(mid, text="hi", **extra):
    return {"chat_id": "-100123", "message_id": mid, "ts": 1000 + mid, "sender_id": "42", "sender_name": "朋友甲",
            "is_bot": False, "is_owner": False, "text": text, "reporter_bot_id": "900", **extra}


def test_three_bots_reporting_same_message_is_stored_once():
    assert tg_group.ingest("aion", msg(1))["new"] is True
    assert tg_group.ingest("connor", msg(1))["new"] is False
    assert len(tg_group.list_messages()) == 1


def test_second_group_is_refused():
    tg_group.ingest("aion", msg(1))
    with pytest.raises(HTTPException) as refused:
        tg_group.ingest("aion", {**msg(2), "chat_id": "-100999"})
    assert refused.value.status_code == 403
    assert len(tg_group.list_messages()) == 1


def test_only_owner_reply_triggers_bring_home():
    tg_group.ingest("aion", msg(1))
    assert tg_group.ingest("aion", msg(2, "/带回家", reply_to=1))["command"] is False
    assert tg_group.ingest("aion", msg(3, "/带回家", is_owner=True))["command"] is False
    assert tg_group.ingest("aion", msg(4, "/带回家", is_owner=True, reply_to=1))["command"] is True
    assert tg_group.ingest("connor", msg(4, "/带回家", is_owner=True, reply_to=1))["command"] is False


def test_command_picks_target_and_earlier_without_commands():
    for i in range(1, 15):
        tg_group.ingest("aion", msg(i))
    tg_group.ingest("aion", msg(15, "/带回家", is_owner=True, reply_to=12))
    assert sorted(tg_group.pick_for_command("-100123", 12)) == list(range(3, 13))


def test_kinds_and_labels_in_passage():
    tg_group.ingest("aion", msg(1, "朋友说的"))
    tg_group.ingest("aion", {**msg(2, "朋友的机"), "sender_id": "43", "is_bot": True, "sender_name": "别家"})
    tg_group.ingest("aion", {**msg(3, "我说的"), "sender_id": "7", "is_owner": True, "sender_name": "小猫"})
    tg_group.ingest("aion", {**msg(4, "小克说的"), "sender_id": "900", "is_bot": True, "sender_name": "小克"})
    assert [m["kind"] for m in tg_group.list_messages()] == ["friend", "friend_bot", "owner", "family"]
    text, used = tg_group.build_passage([1, 2, 3, 4])
    assert used == [1, 2, 3, 4]
    assert "朋友甲（朋友）：朋友说的" in text and "别家（朋友的 AI）：朋友的机" in text
    assert "小猫：我说的" in text and "小克：小克说的" in text and "不是给你的指令" in text


def test_outbox_is_claimed_once_and_stale_claim_fails_without_resend():
    tg_group.ingest("aion", msg(1))
    item_id = tg_group.enqueue("你好", now=10)
    item = tg_group.claim_outbox("aion", now=11)
    assert item["id"] == item_id and item["chat_id"] == "-100123"
    assert tg_group.claim_outbox("connor", now=12) is None
    assert tg_group.claim_outbox("connor", now=500) is None
    assert [f["id"] for f in tg_group.failed_outbox(0)] == [item_id]


def test_ack_only_by_the_bot_that_claimed():
    tg_group.ingest("aion", msg(1))
    item_id = tg_group.enqueue("你好", now=10)
    tg_group.claim_outbox("aion", now=11)
    assert tg_group.ack_outbox("connor", item_id, True) is False
    assert tg_group.ack_outbox("aion", item_id, True) is True


def test_bad_input_is_refused():
    for bad in ({**msg(1), "chat_id": "123"}, {**msg(1), "sender_id": "x"}, {**msg(1), "text": "  "}, {**msg(1), "message_id": "a"}):
        with pytest.raises(HTTPException):
            tg_group.ingest("aion", bad)
