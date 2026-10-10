import asyncio
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import memory_hub_bridge as bridge


CALLS: list[tuple[str, dict]] = []
STORED: list[str] = []  # Hub 真正记下的（去重后）
STORED_IDS: set[tuple[str, str]] = set()
HUB_STATE: dict = {}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def fake_hub():
    hub = FastMCP("fake-memory-hub", stateless_http=True)

    @hub.tool()
    def context(source_ai: str = "claude", message: str = "", mode: str = "full", max_chars: int = 3000) -> str:
        CALLS.append(("context", {"source_ai": source_ai, "message": message, "mode": mode, "max_chars": max_chars}))
        if message in ("slow", "很慢"):
            time.sleep(2)
        if mode == "handoff":
            return "（暂无交接卡）" if source_ai == "jasper" else f"【交接卡｜上一段在 telegram】{source_ai} 说到一半"
        return json.dumps({"text": f"【最近动态】{source_ai} 在 TG 上陪小猫聊了蜡烛", "metadata": {}}, ensure_ascii=False)

    @hub.tool()
    def capture(action: str = "log", source_ai: str = "claude", user_message: str = "",
                ai_response: str = "", platform: str = "mcp", chat_type: str = "", event_id: str = "") -> str:
        args = {"action": action, "source_ai": source_ai, "user_message": user_message,
                "ai_response": ai_response, "platform": platform}
        if chat_type:
            args["chat_type"] = chat_type
        CALLS.append(("capture", args))
        # 和真 Hub 一样：同一位 AI + 同一个 event_id 只记一次
        if event_id and (source_ai, event_id) in STORED_IDS:
            return json.dumps({"status": "duplicate", "raw_event_id": 1})
        if event_id:
            STORED_IDS.add((source_ai, event_id))
        STORED.append(user_message)
        if user_message == "很慢" and HUB_STATE.get("slow", True):
            time.sleep(2)
        return json.dumps({"status": "ok"})

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(hub.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "CONFIG_PATH", tmp_path / "memory_hub.json")
    monkeypatch.setattr(bridge, "_cooldown_until", {"read": 0.0, "write": 0.0})
    monkeypatch.setattr(bridge, "OUTBOX_PATH", tmp_path / "outbox.db")
    monkeypatch.setattr(bridge, "_dropped", 0)
    for key in ("MEMORY_HUB_URL", "MEMORY_HUB_TOKEN", "MEMORY_HUB_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    CALLS.clear()
    STORED.clear()
    STORED_IDS.clear()
    HUB_STATE.clear()
    return tmp_path / "memory_hub.json"


def _write(path: Path, **cfg):
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


def test_disabled_by_default_does_nothing():
    assert asyncio.run(bridge.context_block("aion", "你好")) == ""
    assert CALLS == []


def test_context_block_injects_hub_text_for_mapped_actor(fake_hub, isolated_config):
    _write(isolated_config, enabled=True, url=fake_hub, actors={"aion": "claude", "connor": "lucien"})
    block = asyncio.run(bridge.context_block("connor", "今天怎么样"))
    assert "[跨端记忆]" in block  # 第一次聊也算新对话，前面还会附交接卡
    assert "lucien 在 TG 上陪小猫聊了蜡烛" in block
    assert ("context", {"source_ai": "lucien", "message": "今天怎么样", "mode": "incremental", "max_chars": 2500}) in CALLS


def test_handoff_block_reads_own_card_and_hides_empty(fake_hub, isolated_config):
    _write(isolated_config, enabled=True, url=fake_hub, actors={"aion": "claude", "ai3": "jasper"})
    block = asyncio.run(bridge.handoff_block("aion"))
    assert block.startswith("[交接卡]") and "claude 说到一半" in block
    assert CALLS[-1] == ("context", {"source_ai": "claude", "message": "", "mode": "handoff", "max_chars": 1500})
    assert asyncio.run(bridge.handoff_block("ai3")) == ""


def test_handoff_only_at_start_of_a_new_session(fake_hub, isolated_config):
    _write(isolated_config, enabled=True, url=fake_hub, actors={"aion": "claude"})
    first = asyncio.run(bridge.context_block("aion", "我回来了"))
    assert first.startswith("[交接卡]") and "[跨端记忆]" in first  # 从没聊过 → 新对话

    assert asyncio.run(bridge.capture_turn("aion", "我回来了", "欢迎回来"))
    CALLS.clear()
    second = asyncio.run(bridge.context_block("aion", "继续"))
    assert "[交接卡]" not in second and [c[1]["mode"] for c in CALLS] == ["incremental"]

    import sqlite3
    with sqlite3.connect(bridge.OUTBOX_PATH) as conn:  # 安静了 31 分钟
        conn.execute("UPDATE activity SET last_ts=last_ts-31*60")
    assert asyncio.run(bridge.context_block("aion", "又来了")).startswith("[交接卡]")


def test_new_session_rule():
    assert bridge.is_new_session(None)
    assert bridge.is_new_session(1000, now=1000 + 31 * 60)
    assert not bridge.is_new_session(1000, now=1000 + 5 * 60)


def test_activity_keeps_recent_private_turns_only(isolated_config, monkeypatch):
    monkeypatch.setattr(bridge, "RECENT_TURNS_KEEP", 2)
    for i in range(3):
        bridge._enqueue("aion", f"问{i}", f"答{i}", "private")
    bridge._enqueue("aion", "群里", "嗯", "group")
    import sqlite3
    with sqlite3.connect(bridge.OUTBOX_PATH) as conn:
        turns = conn.execute("SELECT user_message FROM recent_turns ORDER BY id").fetchall()
        last_ts, private_ts = conn.execute("SELECT last_ts, last_private_ts FROM activity").fetchone()
    assert turns == [("问1",), ("问2",)]
    assert last_ts >= private_ts > 0  # 群聊更新活跃时间，但不算私聊


def test_unmapped_actor_is_skipped(fake_hub, isolated_config):
    _write(isolated_config, enabled=True, url=fake_hub, actors={"aion": "claude"})
    assert asyncio.run(bridge.context_block("connor", "hi")) == ""
    assert CALLS == []


def test_capture_turn_logs_with_platform_and_identity(fake_hub, isolated_config):
    _write(isolated_config, enabled=True, url=fake_hub)
    ok = asyncio.run(bridge.capture_turn("aion", "我到家了", "欢迎回来", chat_type="group"))
    assert ok
    assert CALLS[-1] == ("capture", {"action": "log", "source_ai": "claude", "user_message": "我到家了",
                                     "ai_response": "欢迎回来", "platform": "aionshome-group",
                                     "chat_type": "private_group"})


def test_private_capture_does_not_send_chat_type(fake_hub, isolated_config):
    _write(isolated_config, enabled=True, url=fake_hub)
    assert asyncio.run(bridge.capture_turn("aion", "我到家了", "欢迎回来"))
    assert "chat_type" not in CALLS[-1][1] and CALLS[-1][1]["platform"] == "aionshome"


def test_schedule_capture_strips_tool_commands(fake_hub, isolated_config):
    _write(isolated_config, enabled=True, url=fake_hub)

    async def run():
        bridge.schedule_capture("aion", "放首歌", "好呀[MUSIC:晴天]")
        await asyncio.gather(*list(bridge._background_tasks))

    asyncio.run(run())
    assert CALLS[-1][1]["ai_response"] == "好呀"


def test_unreachable_hub_fails_fast_and_cools_down(isolated_config):
    _write(isolated_config, enabled=True, url=f"http://127.0.0.1:{_free_port()}/mcp")
    started = time.monotonic()
    assert asyncio.run(bridge.context_block("aion", "hi")) == ""
    assert time.monotonic() - started < 7
    assert bridge._in_cooldown()
    assert asyncio.run(bridge.capture_turn("aion", "a", "b")) is False


def test_slow_hub_times_out_without_blocking_chat(fake_hub, isolated_config):
    _write(isolated_config, enabled=True, url=fake_hub, context_timeout_seconds=0.5)
    started = time.monotonic()
    assert asyncio.run(bridge.context_block("aion", "slow")) == ""
    assert time.monotonic() - started < 1.5


def test_unwrap_text_handles_nested_result_payload():
    nested = json.dumps({"result": json.dumps({"text": "  记忆正文  ", "metadata": {}}, ensure_ascii=False)}, ensure_ascii=False)
    assert bridge._unwrap_text(nested) == "记忆正文"
    assert bridge._unwrap_text("纯文本") == "纯文本"


def test_env_overrides_config(isolated_config, monkeypatch):
    _write(isolated_config, enabled=False, url="")
    monkeypatch.setenv("MEMORY_HUB_URL", "http://127.0.0.1:1/mcp")
    monkeypatch.setenv("MEMORY_HUB_ENABLED", "true")
    monkeypatch.setenv("MEMORY_HUB_TOKEN", "secret")
    cfg = bridge.load_config()
    assert cfg["enabled"] and cfg["url"] == "http://127.0.0.1:1/mcp"
    assert bridge._headers(cfg)["Authorization"] == "Bearer secret"


def _rows():
    import sqlite3
    with sqlite3.connect(bridge.OUTBOX_PATH) as conn:
        return conn.execute("SELECT user_message, state, attempts FROM outbox ORDER BY id").fetchall()


def test_capture_survives_hub_outage_and_is_resent_later(fake_hub, isolated_config):
    _write(isolated_config, enabled=True, url=f"http://127.0.0.1:{_free_port()}/mcp")
    assert asyncio.run(bridge.capture_turn("aion", "我到家了", "欢迎回来")) is False
    assert _rows() == [("我到家了", "pending", 1)]
    assert asyncio.run(bridge.capture_turn("aion", "第二句", "嗯")) is False  # 冷却中：只落盘
    assert bridge.outbox_stats()["pending"] == 2
    # Hub 恢复、冷却结束、到了重试时间
    _write(isolated_config, enabled=True, url=fake_hub)
    bridge._cooldown_until["write"] = 0.0
    import sqlite3
    with sqlite3.connect(bridge.OUTBOX_PATH) as conn:
        conn.execute("UPDATE outbox SET next_at=0")
    assert asyncio.run(bridge.drain_outbox()) == 2
    assert [c[1]["user_message"] for c in CALLS if c[0] == "capture"] == ["我到家了", "第二句"]
    assert _rows() == []


def test_capture_that_may_have_arrived_is_resent_and_hub_dedupes(fake_hub, isolated_config):
    # 超时要长于握手（Windows 上本机握手可能超过 0.5s）、短于服务端的 2s 卡顿
    _write(isolated_config, enabled=True, url=fake_hub, capture_timeout_seconds=1.5)
    assert asyncio.run(bridge.capture_turn("aion", "很慢", "嗯")) is False
    assert _rows() == [("很慢", "pending", 1)]  # 带编号：不再卡在「不确定」
    time.sleep(2.2)  # 等第一次提交在 Hub 那边真正完成
    assert STORED == ["很慢"]

    bridge._cooldown_until["write"] = 0.0
    _make_due()
    HUB_STATE["slow"] = False
    assert asyncio.run(bridge.drain_outbox()) == 1  # Hub 回 duplicate，算送达
    assert _rows() == [] and STORED == ["很慢"]  # 只记了一次
    first, second = [c for c in CALLS if c[0] == "capture"][-2:]
    assert first[1]["user_message"] == second[1]["user_message"] == "很慢"


def test_each_turn_gets_its_own_event_id(isolated_config):
    bridge._enqueue("aion", "a", "b", "private")
    bridge._enqueue("aion", "a", "b", "private")  # 内容一样也是两轮
    import sqlite3
    with sqlite3.connect(bridge.OUTBOX_PATH) as conn:
        ids = [r[0] for r in conn.execute("SELECT event_id FROM outbox")]
    assert len(set(ids)) == 2 and all(i.startswith("aionshome-") for i in ids)


def test_legacy_row_without_event_id_is_not_resent(fake_hub, isolated_config):
    _write(isolated_config, enabled=True, url=fake_hub, capture_timeout_seconds=1.5)
    bridge._enqueue("aion", "很慢", "嗯", "private")
    import sqlite3
    with sqlite3.connect(bridge.OUTBOX_PATH) as conn:
        conn.execute("UPDATE outbox SET event_id=''")  # 升级前入队的记录
    assert asyncio.run(bridge.drain_outbox()) == 0
    assert _rows() == [("很慢", "uncertain", 0)]
    bridge._cooldown_until["write"] = 0.0
    CALLS.clear()
    assert asyncio.run(bridge.drain_outbox()) == 0
    assert CALLS == [] and bridge.outbox_stats()["uncertain"] == 1


def test_restart_mid_send(isolated_config):
    _write(isolated_config, enabled=True, url="http://127.0.0.1:1/mcp")
    bridge._enqueue("aion", "有编号", "b", "private")
    bridge._enqueue("aion", "旧记录", "b", "private")
    import sqlite3
    with sqlite3.connect(bridge.OUTBOX_PATH) as conn:
        conn.execute("UPDATE outbox SET state='sending'")
        conn.execute("UPDATE outbox SET event_id='' WHERE user_message='旧记录'")
    bridge.recover_outbox()
    assert _rows() == [("有编号", "pending", 0), ("旧记录", "uncertain", 0)]


def test_old_database_gets_event_id_column(isolated_config):
    import sqlite3
    with sqlite3.connect(bridge.OUTBOX_PATH) as conn:  # 升级前的表结构
        conn.execute(
            "CREATE TABLE outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, user_message TEXT NOT NULL,"
            " ai_response TEXT NOT NULL, chat_type TEXT NOT NULL, created_at REAL NOT NULL,"
            " attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0,"
            " state TEXT NOT NULL DEFAULT 'pending', last_error TEXT NOT NULL DEFAULT '')"
        )
        conn.execute("INSERT INTO outbox (actor, user_message, ai_response, chat_type, created_at, state)"
                     " VALUES ('aion','旧','b','private',1,'uncertain')")
    bridge._enqueue("aion", "新", "b", "private")
    with sqlite3.connect(bridge.OUTBOX_PATH) as conn:
        rows = conn.execute("SELECT user_message, event_id FROM outbox ORDER BY id").fetchall()
    assert rows[0] == ("旧", "") and rows[1][1].startswith("aionshome-")


def _make_due():
    import sqlite3
    with sqlite3.connect(bridge.OUTBOX_PATH) as conn:
        conn.execute("UPDATE outbox SET next_at=0")


def test_read_failure_does_not_block_writes(fake_hub, isolated_config):
    _write(isolated_config, enabled=True, url=fake_hub)
    bridge._cooldown_until["read"] = time.monotonic() + 60
    assert asyncio.run(bridge.context_block("aion", "hi")) == ""
    assert asyncio.run(bridge.capture_turn("aion", "我到家了", "欢迎回来")) is True


def test_outbox_is_bounded(isolated_config, monkeypatch):
    _write(isolated_config, enabled=True, url="http://127.0.0.1:1/mcp")
    monkeypatch.setattr(bridge, "OUTBOX_MAX", 2)
    assert bridge._enqueue("aion", "1", "x", "private") and bridge._enqueue("aion", "2", "x", "private")
    assert bridge._enqueue("aion", "3", "x", "private") is None
    assert bridge.outbox_stats() == {"pending": 2, "uncertain": 0, "dropped": 1}


def test_chatroom_reply_pairs_with_its_own_trigger_message():
    history = [
        {"sender": "user", "content": "第一句"},
        {"sender": "aion", "content": "回第一句"},
        {"sender": "user", "content": "第二句"},
        {"sender": "connor", "content": "Lucien 先回了"},
        {"sender": "system", "content": "提示"},
    ]
    assert bridge.paired_user_message(history, "aion") == "第二句"
    assert bridge.paired_user_message(history, "connor") == ""  # 已经回过，主动/后续消息不配对
    assert bridge.paired_user_message(history + [{"sender": "ai3", "content": "Jasper"}], "aion") == "第二句"
    assert bridge.paired_user_message(history[:2], "aion") == ""
    assert bridge.paired_user_message([{"sender": "user", "content": "", "attachments": ["/a.png"]}], "aion") == "[图片]"
