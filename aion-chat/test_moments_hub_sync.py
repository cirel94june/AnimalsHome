import asyncio
import json
import sqlite3
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import memory_hub_bridge as bridge
import moments_hub_sync as sync

HUB = "http://127.0.0.1:9/mcp"


class _Resp:
    def __init__(self, status):
        self.status_code = status
        self.text = "{}"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "CONFIG_PATH", tmp_path / "memory_hub.json")
    monkeypatch.setattr(bridge, "OUTBOX_PATH", tmp_path / "outbox.db")
    monkeypatch.setattr(bridge, "_cooldown_until", {"read": 0.0, "write": 0.0})
    monkeypatch.setattr(bridge, "_consecutive_failures", {})
    monkeypatch.setattr(bridge, "_dropped", 0)
    monkeypatch.setenv("MEMORY_HUB_OWNER_SECRET", "owner-secret")
    for key in ("MEMORY_HUB_URL", "MEMORY_HUB_TOKEN", "MEMORY_HUB_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    cfg = {"enabled": True, "url": HUB, "token": "shared-token",
           "actors": {"aion": "claude", "connor": "lucien", "ai3": "jasper"}}
    (tmp_path / "memory_hub.json").write_text(json.dumps(cfg), encoding="utf-8")

    db_path = tmp_path / "chat.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE moments (id TEXT PRIMARY KEY, author TEXT, content TEXT, attachments TEXT DEFAULT '[]',"
                     " source_conv TEXT, source_msg_id TEXT, expect_reply INTEGER DEFAULT 0, created_at REAL)")
        conn.execute("CREATE TABLE moment_comments (id TEXT PRIMARY KEY, moment_id TEXT, author TEXT, content TEXT,"
                     " reply_to_id TEXT, created_at REAL)")

    @asynccontextmanager
    async def fake_db():
        async with aiosqlite.connect(db_path) as db:
            yield db
    monkeypatch.setattr(sync, "get_db", fake_db)

    state = {"status": 200, "posts": []}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            state["posts"].append((url, json, headers))
            return _Resp(state["status"])

    monkeypatch.setattr(bridge, "_http_client", FakeClient)

    def add_moment(mid, author, content, at, attachments="[]"):
        with sqlite3.connect(db_path) as conn:
            conn.execute("INSERT INTO moments (id, author, content, attachments, created_at) VALUES (?,?,?,?,?)",
                         (mid, author, content, attachments, at))

    def add_comment(cid, mid, author, content, at, reply_to=None):
        with sqlite3.connect(db_path) as conn:
            conn.execute("INSERT INTO moment_comments VALUES (?,?,?,?,?,?)", (cid, mid, author, content, reply_to, at))

    state.update(add_moment=add_moment, add_comment=add_comment, cfg_path=tmp_path / "memory_hub.json")
    return state


def _run(now):
    async def go():
        queued = await sync.sync_new(now=now)
        await bridge.drain_outbox()
        return queued
    return asyncio.run(go())


def _sent(env):
    return [(body, headers["Authorization"]) for _, body, headers in env["posts"]]


def test_first_run_only_sets_start_point(env):
    env["add_moment"]("m_old", "user", "以前的朋友圈", 100.0)
    assert _run(1000.0) == 0
    assert env["posts"] == []
    assert _run(1001.0) == 0  # 旧的不补送


def test_owner_post_ai_comment_and_owner_reply(env):
    _run(1000.0)
    env["add_moment"]("m1", "user", "今天去看海了", 1001.0)
    env["add_comment"]("c1", "m1", "connor", "海风大吗", 1002.0)
    env["add_comment"]("c2", "m1", "user", "超级大", 1003.0, reply_to="c1")
    assert _run(1010.0) == 3
    (post, k1), (ai, k2), (reply, k3) = _sent(env)
    assert env["posts"][0][0] == "http://127.0.0.1:9/api/capture/log"

    assert k1 == "Bearer owner-secret" and post == {
        "user_message": "今天去看海了", "ai_response": "", "ai_id": "claude",
        "platform": "aionshome-moments", "chat_type": "private_group", "chat_id": "moment:m1",
        "event_id": "aionshome-moments-m1-post", "sender_type": "owner"}
    assert k2 == "Bearer shared-token" and ai == {
        "user_message": "", "ai_response": "海风大吗", "ai_id": "lucien",
        "platform": "aionshome-moments", "chat_type": "private_group", "chat_id": "moment:m1",
        "event_id": "aionshome-moments-m1-c1", "reply_to_id": "aionshome-moments-m1-post"}
    assert k3 == "Bearer owner-secret" and reply["sender_type"] == "owner"
    assert reply["user_message"] == "超级大" and reply["ai_id"] == "claude"
    assert reply["reply_to_id"] == "aionshome-moments-m1-c1" and reply["event_id"] == "aionshome-moments-m1-c2"
    assert all("sender_id" not in body for body, _ in _sent(env))

    assert _run(1020.0) == 0 and len(env["posts"]) == 3  # 不重复送


def test_owner_comment_on_seat_moment_uses_that_ai_and_unlinked_seats_are_skipped(env):
    _run(1000.0)
    env["add_moment"]("m2", "ai3", "", 1001.0, attachments='["/a.png"]')
    env["add_comment"]("c3", "m2", "user", "好看", 1002.0)
    env["add_moment"]("m3", "ai4", "空位写的", 1003.0)
    env["add_comment"]("c4", "m3", "user", "嗯", 1004.0)
    assert _run(1010.0) == 2
    (post, k1), (comment, k2) = _sent(env)
    assert post["ai_id"] == "jasper" and post["ai_response"] == "[图片]" and k1 == "Bearer shared-token"
    assert comment["ai_id"] == "jasper" and comment["sender_type"] == "owner"


def test_hub_error_resends_the_identical_request(env):
    _run(1000.0)
    env["add_moment"]("m4", "aion", "想你", 1001.0)
    env["status"] = 500
    _run(1010.0)
    env["status"] = 200
    bridge._cooldown_until["write"] = 0.0
    with sqlite3.connect(bridge.OUTBOX_PATH) as conn:
        conn.execute("UPDATE outbox SET next_at=0")
    asyncio.run(bridge.drain_outbox())
    assert len(env["posts"]) == 2 and env["posts"][0][1] == env["posts"][1][1]
    with sqlite3.connect(bridge.OUTBOX_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0


def test_without_owner_key_her_lines_still_go_with_shared_key(env, monkeypatch):
    monkeypatch.delenv("MEMORY_HUB_OWNER_SECRET")
    _run(1000.0)
    env["add_moment"]("m5", "user", "晚安", 1001.0)
    _run(1010.0)
    (body, key), = _sent(env)
    assert key == "Bearer shared-token" and "sender_type" not in body and body["user_message"] == "晚安"


def test_switch_off_sends_nothing(env):
    cfg = json.loads(env["cfg_path"].read_text(encoding="utf-8"))
    env["cfg_path"].write_text(json.dumps({**cfg, "moments_enabled": False}), encoding="utf-8")
    _run(1000.0)
    env["add_moment"]("m6", "user", "不送", 1001.0)
    assert _run(1010.0) == 0 and env["posts"] == []


def test_late_row_inside_lookback_is_not_missed(env):
    _run(1000.0)
    env["add_moment"]("m7", "user", "先扫到的", 1100.0)
    _run(1110.0)
    env["add_moment"]("m8", "user", "时间稍早但后写进库", 1090.0)
    assert _run(1120.0) == 1
    assert _sent(env)[-1][0]["event_id"] == "aionshome-moments-m8-post"


def test_moments_written_before_switching_on_are_never_sent(env):
    """狐狸 §154 M1：1000 时开启，900 和 1000 那两条都在开启前（含同一时刻），不能被回看补送。"""
    env["add_moment"]("m_old", "user", "开启前的", 900.0)
    env["add_moment"]("m_same", "user", "开启那一刻的", 1000.0)
    _run(1000.0)
    env["add_moment"]("m_new", "user", "开启后的", 1001.0)
    assert _run(1005.0) == 1
    assert [body["event_id"] for body, _ in _sent(env)] == ["aionshome-moments-m_new-post"]
    assert _run(1700.0) == 0 and len(env["posts"]) == 1


def test_switching_off_pauses_queued_moments_but_not_chat(env, monkeypatch):
    """狐狸 §154 M2：排队后关掉开关，朋友圈原地暂停；同一位 AI 的聊天照常发；重新打开后接着发。"""
    monkeypatch.setattr(bridge, "_kick_drain", lambda: None)  # 只排队，先不立刻发
    _run(1000.0)
    env["add_moment"]("m9", "aion", "排队中的", 1001.0)

    async def queue_only():
        return await sync.sync_new(now=1010.0)
    assert asyncio.run(queue_only()) == 1  # 只排队，还没发

    cfg = json.loads(env["cfg_path"].read_text(encoding="utf-8"))
    env["cfg_path"].write_text(json.dumps({**cfg, "moments_enabled": False}), encoding="utf-8")
    bridge._enqueue("aion", "在吗", "在呢", "group", chat_id="cr_1", from_owner=True)
    asyncio.run(bridge.drain_outbox())
    assert [body.get("platform") for _, body, _ in env["posts"]] == ["aionshome-group"]  # 聊天发了，朋友圈没发
    with sqlite3.connect(bridge.OUTBOX_PATH) as conn:
        assert conn.execute("SELECT state FROM outbox WHERE kind='moment'").fetchall() == [("pending",)]

    env["cfg_path"].write_text(json.dumps(cfg), encoding="utf-8")
    asyncio.run(bridge.drain_outbox())
    assert env["posts"][-1][1]["event_id"] == "aionshome-moments-m9-post"


def test_queue_and_dedupe_record_commit_together(env, monkeypatch):
    """狐狸 §154 M3：登记去重失败时，入队也一起撤销；改了身份再扫，同一件事只排一次。"""
    monkeypatch.setattr(bridge, "_kick_drain", lambda: None)  # 只看队列，不发
    _run(1000.0)
    env["add_moment"]("m10", "connor", "Lucien 发的", 1001.0)
    with sqlite3.connect(bridge.OUTBOX_PATH) as conn:
        conn.execute("CREATE TRIGGER boom BEFORE INSERT ON moment_hub_synced BEGIN SELECT RAISE(ABORT, 'disk'); END")
    with pytest.raises(sqlite3.DatabaseError):
        asyncio.run(sync.sync_new(now=1010.0))
    with sqlite3.connect(bridge.OUTBOX_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
        conn.execute("DROP TRIGGER boom")
    cfg = json.loads(env["cfg_path"].read_text(encoding="utf-8"))
    cfg["actors"]["connor"] = "lucien2"
    env["cfg_path"].write_text(json.dumps(cfg), encoding="utf-8")
    asyncio.run(sync.sync_new(now=1020.0))
    asyncio.run(sync.sync_new(now=1030.0))
    with sqlite3.connect(bridge.OUTBOX_PATH) as conn:
        assert conn.execute("SELECT source_ai FROM outbox").fetchall() == [("lucien2",)]
