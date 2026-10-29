"""2026-10-24 小猫：每位 AI 的私聊都能开多个窗口。她选了折中：窗口只是帮她整理，TA 看得到和她所有私聊窗口
里最近的聊天（加群聊），不会断片；更早的可以用 [MEMORY_SEARCH:…] 搜到。座位 3～6 和小克、Lucien 一样。"""

import asyncio
import re
import sqlite3
from pathlib import Path

import pytest

import active_memory_search as ams
import context_builder
import database
import seat_chat


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "chat.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    asyncio.run(database.init_db())
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            INSERT INTO chatroom_rooms (id,title,type,actor,created_at,updated_at) VALUES
              ('g1','家','group','',1,1),
              ('c1','Lucien 窗口一','connor_1v1','',1,1), ('c2','Lucien 窗口二','connor_1v1','',2,2),
              ('s1','狗蛋 窗口一','seat_1v1','ai3',1,1), ('s2','狗蛋 窗口二','seat_1v1','ai3',2,2),
              ('s9','别的座位','seat_1v1','ai4',3,3);
            INSERT INTO chatroom_messages (id,room_id,sender,content,created_at) VALUES
              ('m1','g1','user','群里说今天下雨',10),
              ('m2','c1','user','窗口一聊猫粮',11), ('m3','c2','user','窗口二聊电影',12),
              ('m4','s1','user','和狗蛋聊过海边旅行',13), ('m5','s2','user','狗蛋窗口二聊作业',14),
              ('m6','s9','user','别的座位说海边旅行',15);
        """)
    return path


def _contents(rows):
    return sorted(r["content"] for r in rows)


def test_lucien_sees_all_his_windows(db):
    rows = asyncio.run(context_builder.fetch_merged_timeline("connor", 50))
    assert _contents(rows) == sorted(["群里说今天下雨", "窗口一聊猫粮", "窗口二聊电影"])


def test_seat_sees_all_its_windows_but_not_other_seats(db):
    rows = asyncio.run(seat_chat.merged_messages("ai3", 50))
    assert _contents(rows) == sorted(["和狗蛋聊过海边旅行", "狗蛋窗口二聊作业", "群里说今天下雨"])


def test_xiaoke_private_chat_reads_all_his_windows():
    """小克私聊页、闹铃、监控、定位取上下文时不再按当前窗口过滤。"""
    from pathlib import Path
    here = Path(__file__).parent
    for name in ("routes/chat.py", "schedule.py", "camera.py", "location.py"):
        text = (here / name).read_text(encoding="utf-8")
        assert 'fetch_merged_timeline("aion"' in text
        assert not re.search(r'fetch_merged_timeline\("aion",[^)]*conv_id=', text), name


def test_seat_can_search_its_other_windows_but_not_other_seats(db):
    request = ams.MemorySearchRequest("海边旅行")  # 没写 history：座位没有本地记忆库，一律搜聊天记录
    results = asyncio.run(ams.search_actor_memories("ai3", [request]))
    texts = " ".join(r.content for r in results)
    assert "和狗蛋聊过海边旅行" in texts and "别的座位" not in texts
    opened = asyncio.run(ams.open_memory_source("ai3", "chatroom:m4", "海边"))
    assert opened and "海边旅行" in " ".join(opened[0].sources)
    assert asyncio.run(ams.open_memory_source("ai3", "chatroom:m6")) == []  # 别的座位的私聊打不开


def test_lucien_history_search_covers_all_windows(db):
    results = asyncio.run(ams.search_actor_memories("connor", [ams.MemorySearchRequest("猫粮", history=True)]))
    assert any("窗口一聊猫粮" in r.content for r in results)


def test_unknown_actor_still_rejected(db):
    with pytest.raises(ValueError):
        asyncio.run(ams.search_actor_memories("ai9", [ams.MemorySearchRequest("x")]))


def test_seat_reply_with_search_command_triggers_search(monkeypatch):
    import routes.chatroom as chatroom_routes
    notices = []

    async def sys_msg(room_id, text, _q, after_msg_id=None):
        notices.append(text)
        return {"id": "sys1"}
    monkeypatch.setattr(chatroom_routes, "_chatroom_sys_msg", sys_msg)
    monkeypatch.setattr(chatroom_routes, "is_capability_enabled", lambda name: name == "memory_search")
    monkeypatch.setattr(chatroom_routes, "_name_for_identity", lambda who: "狗蛋")
    text, triggered = asyncio.run(chatroom_routes._process_seat_commands(
        "我翻翻看[MEMORY_SEARCH:海边旅行]", "s2", "ai3", "msg1", asyncio.Queue(), user_text="我们上次说去哪"))
    assert text == "我翻翻看"
    payload = triggered["memory_search"]
    assert payload["requests"][0].query == "海边旅行" and payload["original_question"] == "我们上次说去哪"
    assert notices and "狗蛋" in notices[0]

    monkeypatch.setattr(chatroom_routes, "is_capability_enabled", lambda name: False)  # 开关关着：不搜
    text, triggered = asyncio.run(chatroom_routes._process_seat_commands(
        "嗯[MEMORY_SEARCH:海边旅行]", "s2", "ai3", "msg2", asyncio.Queue()))
    assert "memory_search" not in triggered


def test_seat_goes_to_the_window_where_she_last_spoke(db):
    """§72 修复审 M3：她最后在窗口一说话，窗口二后来被后台消息 / 改名更新了，也要去窗口一。"""
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            INSERT INTO chatroom_messages (id,room_id,sender,content,created_at) VALUES ('m9','s1','user','在窗口一说的',20);
            UPDATE chatroom_rooms SET updated_at=99 WHERE id='s2';
        """)
    assert asyncio.run(seat_chat.seat_target_room("ai3")) == ("s1", "seat_1v1")


def test_lucien_goes_to_the_window_where_she_last_spoke(db):
    import routes.chatroom as chatroom_routes
    with sqlite3.connect(db) as conn:
        conn.executescript("UPDATE chatroom_rooms SET updated_at=99 WHERE id='c2';"
                           "INSERT INTO chatroom_messages (id,room_id,sender,content,created_at) VALUES ('m8','c1','user','x',30);")
    assert asyncio.run(chatroom_routes._get_or_create_connor_private_room()) == "c1"


def test_standalone_contacts_new_xiaoke_window_opens_a_new_one():
    """§72 修复审 L3：单独打开的入口页点「和小克开新窗口」→ /chat?new=1 要新建，不回旧窗口。"""
    from pathlib import Path
    text = (Path(__file__).parent / "static" / "chat.js").read_text(encoding="utf-8")
    new_branch = text.index("initParams.get('new') === '1'")
    assert new_branch < text.index("lastId && conversations.find(c => c.id === lastId)")
    assert "await newConversation()" in text[new_branch:new_branch + 400]


def test_tool_followups_read_across_windows(db):
    """§74 R3：搜索 / 看监控 / 周边等追加回复也读本人所有私聊窗口最近的聊天；群就是这个群。"""
    import routes.chatroom as chatroom_routes
    seat = asyncio.run(chatroom_routes._load_followup_messages("s2", limit=10))
    assert {m["content"] for m in seat} == {"和狗蛋聊过海边旅行", "狗蛋窗口二聊作业"}  # 不含别的座位、不含群
    lucien = asyncio.run(chatroom_routes._load_followup_messages("c1", limit=10))
    assert {m["content"] for m in lucien} == {"窗口一聊猫粮", "窗口二聊电影"}
    group = asyncio.run(chatroom_routes._load_followup_messages("g1", limit=10))
    assert [m["content"] for m in group] == ["群里说今天下雨"]


def test_xiaoke_private_followups_read_across_windows(db):
    import routes.chat as chat_routes
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            INSERT INTO conversations (id,title,model,created_at,updated_at) VALUES ('a','A','',1,1), ('b','B','',2,2);
            INSERT INTO messages (id,conv_id,role,content,created_at) VALUES
              ('p1','b','user','在窗口B说不吃香菜',1), ('p2','a','user','窗口A问晚饭吃什么',2);
        """)
    rows = asyncio.run(chat_routes._private_memory_recent_messages("a"))
    assert [r["content"] for r in rows] == ["在窗口B说不吃香菜", "窗口A问晚饭吃什么"]
    text = (Path(__file__).parent / "routes" / "chat.py").read_text(encoding="utf-8")
    assert "WHERE conv_id=? AND role IN ('user','assistant') ORDER BY created_at DESC LIMIT 6" not in text


def test_home_group_entry_creates_at_most_one_group(db):
    """§85 M2：主页「群聊」入口建「家」时，服务端在同一事务里再看一次：已经有群就返回那个；同时来两次也只建一个。"""
    import routes.chatroom as chatroom_routes
    from unittest.mock import AsyncMock, patch
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM chatroom_rooms WHERE type='group'")
        conn.commit()
    body = chatroom_routes.RoomCreate(title="家", type="group", ensure_group=True)

    async def twice():
        with patch.object(chatroom_routes.manager, "broadcast", new=AsyncMock()):
            return await asyncio.gather(chatroom_routes.create_room(body), chatroom_routes.create_room(body))
    a, b = asyncio.run(twice())
    assert a["id"] == b["id"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chatroom_rooms WHERE type='group'").fetchone()[0] == 1


def test_frontend_single_flight_guards_in_source():
    """§85 M2 / M3：群聊入口先确实读到房间列表、同一时间只开一次；没开对话就发送时先占住「正在发送」、开窗口只开一次。"""
    here = Path(__file__).parent
    room_js = (here / "static" / "chatroom.js").read_text(encoding="utf-8")
    once = room_js[room_js.index("async function crOpenLatestGroupOnce"):]
    assert "_crOpeningGroup" in room_js and once.index("await loadRooms()") < once.index("ensure_group: true")
    chat_js = (here / "static" / "chat.js").read_text(encoding="utf-8")
    send_part = chat_js[chat_js.index("async function send()"):]
    assert send_part.index("sending = true;") < send_part.index("await newConversation()")
    assert "_creatingConversation" in chat_js and "function newConversation()" in chat_js


def _seat_rooms(db, actor):
    with sqlite3.connect(db) as conn:
        return [r[0] for r in conn.execute(
            "SELECT id FROM chatroom_rooms WHERE type='seat_1v1' AND actor=?", (actor,))]


@pytest.fixture
def seat_ready(monkeypatch):
    import routes.chatroom as chatroom_routes
    from unittest.mock import AsyncMock
    monkeypatch.setattr(chatroom_routes.actors_registry, "get_actor",
                        lambda actor: {"id": actor, "name": "狗蛋", "enabled": True})
    monkeypatch.setattr(chatroom_routes.manager, "broadcast", AsyncMock())
    monkeypatch.setattr(seat_chat, "check_ready",
                        lambda actor: (None, "m") if actor == "ai5" else (_ for _ in ()).throw(seat_chat.SeatNotReady("x")))
    return chatroom_routes


def test_first_seat_window_is_created_once_even_when_everyone_comes_at_once(db, seat_ready):
    """归巢复审 M2：归巢打包和聊天室同时给座位建第一个私聊窗口，只建一个。"""
    from routes.homecoming import _ensure_seat_rooms

    async def crowd():
        jobs = [seat_ready._get_or_create_seat_room("ai5") for _ in range(20)]
        jobs += [_ensure_seat_rooms() for _ in range(4)]
        return await asyncio.gather(*jobs)
    for _ in range(5):  # 并发撞不撞看运气，多来几轮
        with sqlite3.connect(db) as conn:
            conn.execute("DELETE FROM chatroom_rooms WHERE actor='ai5'")
        ids = asyncio.run(crowd())
        assert len(_seat_rooms(db, "ai5")) == 1
        assert set(ids[:20]) == set(_seat_rooms(db, "ai5"))


def test_existing_window_is_reused_and_new_window_still_opens_more(db, seat_ready):
    assert asyncio.run(seat_ready._get_or_create_seat_room("ai3")) == "s2"  # 已有：回最近那个
    assert sorted(_seat_rooms(db, "ai3")) == ["s1", "s2"]
    body = seat_ready.RoomCreate(title="", type="seat_1v1", actor="ai3", new_window=True)

    async def two_new():
        return await asyncio.gather(seat_ready.create_room(body), seat_ready.create_room(body))
    a, b = asyncio.run(two_new())
    assert a["id"] != b["id"] and len(_seat_rooms(db, "ai3")) == 4  # 手动开新窗口照旧可以好几个
