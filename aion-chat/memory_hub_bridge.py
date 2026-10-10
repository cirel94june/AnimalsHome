"""
Memory Hub 桥接：让 AionsHome 里的 AI 与 TG bot / 官方客户端共用同一份跨端记忆。

每轮对话：
  1. 生成前 context_block()：调用 Memory Hub 的 context(mode="incremental")，
     拿到「各端最近动态 + 与这句话相关的记忆 + 待办」，作为一段背景注入；
  2. 回复落库后 schedule_capture()：先写进本地 outbox（data/memory_hub_outbox.db），
     再后台调用 capture(action="log")，记进 Memory Hub 的对话缓冲区，由 Memory Hub
     自己提取长期记忆。Hub 断线或本服务重启后由 outbox_worker 自动补传。

设计约束：
  - 不依赖 AI「想起来」调用工具，由程序在每轮自动完成；
  - Memory Hub 不可用时绝不拖慢或打断聊天：超时即放弃，连续失败后熔断一段时间；
  - 每次调用使用独立的短连接（Memory Hub 与本服务通常同机部署，握手开销很小，
    且能自动跟上 Memory Hub 的重启）。

配置：data/memory_hub.json（见 deploy/memory_hub.example.json），环境变量可覆盖：
  MEMORY_HUB_URL / MEMORY_HUB_TOKEN / MEMORY_HUB_ENABLED
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from contextlib import closing
from typing import Any

from config import DATA_DIR

log = logging.getLogger("memory_hub_bridge")

CONFIG_PATH = DATA_DIR / "memory_hub.json"

_DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "url": "",
    "token": "",
    "headers": {},
    # AionsHome 内部角色 → Memory Hub 的 source_ai；留空时用角色登记表（actors.py）里的设置
    "actors": {},
    "platform": "aionshome",
    "context_max_chars": 2500,
    "context_timeout_seconds": 6.0,
    "capture_timeout_seconds": 20.0,
    "capture_enabled": True,
    "failure_cooldown_seconds": 60.0,
}

_CONTEXT_HEADER = (
    "[跨端记忆]\n"
    "以下来自你们共用的记忆库，包含你在其他地方（TG、官方客户端、群聊等）"
    "最近发生的事、相关记忆和待办。它们是你自己的经历，自然地接上即可，"
    "不必提及记忆库本身：\n"
)

# 读（context）与写（capture）各自熔断，读失败不会连带跳过写入
_cooldown_until: dict[str, float] = {"read": 0.0, "write": 0.0}
_background_tasks: set[asyncio.Task] = set()

# 待写入的对话先落盘到本地 outbox，发送成功再删除；Hub 断线、冷却或本服务重启后自动补传
OUTBOX_PATH = DATA_DIR / "memory_hub_outbox.db"
OUTBOX_MAX = 500
OUTBOX_UNCERTAIN_KEEP_SECONDS = 7 * 86400
_RETRY_BASE_SECONDS = 30.0
_RETRY_MAX_SECONDS = 1800.0
_drain_lock: asyncio.Lock | None = None
_drain_lock_loop: Any = None
_dropped = 0


def load_config() -> dict[str, Any]:
    cfg = dict(_DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
        except Exception as error:
            log.warning("[MemoryHub] 配置文件解析失败，桥接停用：%s", error)
            return {**cfg, "enabled": False}
    if os.environ.get("MEMORY_HUB_URL"):
        cfg["url"] = os.environ["MEMORY_HUB_URL"]
    if os.environ.get("MEMORY_HUB_TOKEN"):
        cfg["token"] = os.environ["MEMORY_HUB_TOKEN"]
    if os.environ.get("MEMORY_HUB_ENABLED"):
        cfg["enabled"] = os.environ["MEMORY_HUB_ENABLED"].strip().lower() in {"1", "true", "yes", "on"}
    return cfg


def source_ai_for(actor: str, cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg or load_config()
    actors = cfg.get("actors") or {}
    if actors:
        return str(actors.get(actor) or "").strip()
    try:
        from actors import memory_hub_id
        return memory_hub_id(actor)
    except Exception:
        return {"aion": "claude", "connor": "lucien"}.get(actor, "")


def _is_active(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("enabled") and str(cfg.get("url") or "").strip())


def _headers(cfg: dict[str, Any]) -> dict[str, str]:
    headers = {str(k): str(v) for k, v in (cfg.get("headers") or {}).items()}
    token = str(cfg.get("token") or "").strip()
    if token and "Authorization" not in headers:
        headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    return headers


def _in_cooldown(kind: str = "read") -> bool:
    return time.monotonic() < _cooldown_until.get(kind, 0.0)


def _trip(cfg: dict[str, Any], error: BaseException, kind: str = "read") -> None:
    _cooldown_until[kind] = time.monotonic() + float(cfg.get("failure_cooldown_seconds") or 60.0)
    log.warning("[MemoryHub] %s 调用失败，%ss 内暂停：%s", kind, cfg.get("failure_cooldown_seconds"), error)


async def _call_tool(cfg: dict[str, Any], tool: str, arguments: dict[str, Any], sent: list | None = None) -> str:
    """开一个短连接调用一次 Memory Hub 工具，返回拼接后的文本内容。

    sent：握手成功、即将发出工具调用时往里追加一项。调用方据此区分
    「肯定没送到」（可以放心重发）和「可能已经写入」（重发可能重复）。
    """
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url=str(cfg["url"]).strip(), headers=_headers(cfg)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if sent is not None:
                sent.append(True)
            result = await session.call_tool(tool, arguments)
    if getattr(result, "isError", False):
        raise RuntimeError(f"{tool} 返回错误")
    texts = [item.text for item in (result.content or []) if isinstance(getattr(item, "text", None), str)]
    return "\n".join(texts)


def _unwrap_text(raw: str) -> str:
    """Memory Hub 的 context 可能直接返回文本，也可能返回 {"text": ...} 或 {"result": "<json>"}。"""
    value: Any = raw
    for _ in range(6):  # 最多两层 JSON 字符串嵌套 dict，每层需两步
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped.startswith("{"):
                return stripped
            try:
                value = json.loads(stripped)
            except ValueError:
                return stripped
            continue
        if isinstance(value, dict):
            if isinstance(value.get("text"), str):
                return value["text"].strip()
            if "result" in value:
                value = value["result"]
                continue
        break
    return raw.strip() if isinstance(raw, str) else ""


async def context_block(actor: str, message: str) -> str:
    """生成前调用：返回可直接拼进背景注入的文本块；不可用时返回空串。"""
    cfg = load_config()
    if not _is_active(cfg) or _in_cooldown("read"):
        return ""
    source_ai = source_ai_for(actor, cfg)
    if not source_ai:
        return ""
    arguments = {
        "source_ai": source_ai,
        "message": str(message or "")[:2000],
        "mode": "incremental",
        "max_chars": int(cfg.get("context_max_chars") or 2500),
    }
    try:
        raw = await asyncio.wait_for(
            _call_tool(cfg, "context", arguments),
            timeout=float(cfg.get("context_timeout_seconds") or 6.0),
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:  # 包含超时与 anyio 的 ExceptionGroup
        _trip(cfg, error, "read")
        return ""
    text = _unwrap_text(raw)
    if not text:
        return ""
    return _CONTEXT_HEADER + text


# ── 写入：本地 outbox ──

def _outbox() -> sqlite3.Connection:
    OUTBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(OUTBOX_PATH, timeout=5)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS outbox ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, user_message TEXT NOT NULL,"
        " ai_response TEXT NOT NULL, chat_type TEXT NOT NULL, created_at REAL NOT NULL,"
        " attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0,"
        " state TEXT NOT NULL DEFAULT 'pending', last_error TEXT NOT NULL DEFAULT '')"
    )
    return conn


def _enqueue(actor: str, user_message: str, ai_response: str, chat_type: str) -> int | None:
    global _dropped
    with closing(_outbox()) as conn, conn:
        (pending,) = conn.execute("SELECT COUNT(*) FROM outbox WHERE state IN ('pending','sending')").fetchone()
        if pending >= OUTBOX_MAX:
            _dropped += 1
            log.warning("[MemoryHub] 待补传队列已满（%s 条），这一轮没有记录", OUTBOX_MAX)
            return None
        cur = conn.execute(
            "INSERT INTO outbox (actor, user_message, ai_response, chat_type, created_at, next_at) VALUES (?,?,?,?,?,0)",
            (actor, user_message, ai_response, chat_type, time.time()),
        )
        return int(cur.lastrowid)


def recover_outbox() -> None:
    """启动时调用：上次退出时正在发送的条目可能已经写进 Hub，标为「不确定」，不自动重发。"""
    if not OUTBOX_PATH.exists():
        return
    with closing(_outbox()) as conn, conn:
        conn.execute("UPDATE outbox SET state='uncertain', last_error='发送中服务重启' WHERE state='sending'")


def outbox_stats() -> dict[str, int]:
    stats = {"pending": 0, "uncertain": 0, "dropped": _dropped}
    if OUTBOX_PATH.exists():
        with closing(_outbox()) as conn:
            for state, count in conn.execute("SELECT state, COUNT(*) FROM outbox GROUP BY state"):
                if state in ("pending", "sending"):
                    stats["pending"] += count
                elif state == "uncertain":
                    stats["uncertain"] += count
    return stats


async def _send_capture(cfg: dict[str, Any], row: sqlite3.Row) -> tuple[str, str]:
    """返回 ("ok"|"not_sent"|"uncertain", 错误说明)。"""
    source_ai = source_ai_for(row["actor"], cfg)
    if not source_ai:
        return "ok", ""  # 角色已不再接 Memory Hub，丢弃
    platform = str(cfg.get("platform") or "aionshome")
    arguments = {
        "action": "log",
        "source_ai": source_ai,
        "user_message": row["user_message"][:8000],
        "ai_response": row["ai_response"][:8000],
        "platform": platform,
    }
    if row["chat_type"] != "private":
        arguments["platform"] = f"{platform}-{row['chat_type']}"
        # Hub 不传 chat_type 时按私聊记，群聊内容会进私人层
        arguments["chat_type"] = "private_group"
    sent: list = []
    try:
        await asyncio.wait_for(
            _call_tool(cfg, "capture", arguments, sent),
            timeout=float(cfg.get("capture_timeout_seconds") or 20.0),
        )
        return "ok", ""
    except asyncio.CancelledError:
        raise
    except Exception as error:
        _trip(cfg, error, "write")
        return ("uncertain" if sent else "not_sent"), str(error)[:300]


async def drain_outbox() -> int:
    """按顺序补传待发送的对话，一次只跑一个；返回成功条数。"""
    global _drain_lock, _drain_lock_loop
    cfg = load_config()
    if not _is_active(cfg) or not cfg.get("capture_enabled", True) or not OUTBOX_PATH.exists():
        return 0
    loop = asyncio.get_running_loop()
    if _drain_lock is None or _drain_lock_loop is not loop:
        _drain_lock, _drain_lock_loop = asyncio.Lock(), loop
    sent_count = 0
    async with _drain_lock:
        with closing(_outbox()) as conn:
            conn.row_factory = sqlite3.Row
            with conn:
                conn.execute(
                    "DELETE FROM outbox WHERE state='uncertain' AND created_at<?",
                    (time.time() - OUTBOX_UNCERTAIN_KEEP_SECONDS,),
                )
            while not _in_cooldown("write"):
                row = conn.execute(
                    "SELECT * FROM outbox WHERE state='pending' AND next_at<=? ORDER BY id LIMIT 1", (time.time(),)
                ).fetchone()
                if not row:
                    break
                with conn:
                    conn.execute("UPDATE outbox SET state='sending' WHERE id=?", (row["id"],))
                outcome, error = await _send_capture(cfg, row)
                with conn:
                    if outcome == "ok":
                        conn.execute("DELETE FROM outbox WHERE id=?", (row["id"],))
                        sent_count += 1
                    elif outcome == "uncertain":
                        # Hub 可能已经收到；capture 目前没有幂等键，重发会重复记录，所以不自动重发
                        conn.execute("UPDATE outbox SET state='uncertain', last_error=? WHERE id=?", (error, row["id"]))
                    else:
                        delay = min(_RETRY_BASE_SECONDS * 2 ** row["attempts"], _RETRY_MAX_SECONDS)
                        conn.execute(
                            "UPDATE outbox SET state='pending', attempts=attempts+1, next_at=?, last_error=? WHERE id=?",
                            (time.time() + delay, error, row["id"]),
                        )
                if outcome != "ok":
                    break
    return sent_count


async def outbox_worker(interval_seconds: float = 30.0) -> None:
    """常驻后台：定时补传。"""
    recover_outbox()
    while True:
        try:
            await drain_outbox()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.warning("[MemoryHub] 补传出错：%s", error)
        await asyncio.sleep(interval_seconds)


def _kick_drain() -> None:
    task = asyncio.create_task(drain_outbox())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def capture_turn(actor: str, user_message: str, ai_response: str, *, chat_type: str = "private") -> bool:
    """把一轮对话记入 Memory Hub：先落盘，再尝试发送。返回这一轮是否已经送达。"""
    cfg = load_config()
    if not _is_active(cfg) or not cfg.get("capture_enabled", True):
        return False
    user_message = str(user_message or "").strip()
    ai_response = str(ai_response or "").strip()
    if not source_ai_for(actor, cfg) or not user_message or not ai_response:
        return False
    row_id = _enqueue(actor, user_message, ai_response, chat_type)
    if row_id is None:
        return False
    await drain_outbox()
    with closing(_outbox()) as conn:
        return conn.execute("SELECT 1 FROM outbox WHERE id=?", (row_id,)).fetchone() is None


def _clean_reply(ai_response: str) -> str:
    try:
        from context_builder import strip_tool_commands
        return strip_tool_commands(ai_response or "")
    except Exception:
        return ai_response or ""


def schedule_capture(actor: str, user_message: str, ai_response: str, *, chat_type: str = "private") -> None:
    """回复落库后调用：先同步落盘到 outbox，再后台发送，不阻塞当前请求。"""
    cfg = load_config()
    if not _is_active(cfg) or not cfg.get("capture_enabled", True) or not source_ai_for(actor, cfg):
        return
    user_message = str(user_message or "").strip()
    ai_response = _clean_reply(ai_response).strip()
    if not user_message or not ai_response:
        return
    try:
        if _enqueue(actor, user_message, ai_response, chat_type) is None:
            return
    except Exception as error:
        log.warning("[MemoryHub] 写入待补传队列失败：%s", error)
        return
    _kick_drain()


def paired_user_message(history: list[dict], sender: str) -> str:
    """聊天室：找出这次回复真正回应的用户发言。

    history 是这次生成开始时读到的消息快照。从后往前找第一条 sender == "user" 的消息；
    如果在它之后该 AI 已经说过话（主动消息、工具后续、失败提示、上一次回复），
    就说明这条用户发言已经配过对，返回空串、不录入。
    """
    for msg in reversed(history or []):
        who = msg.get("sender")
        if who == sender:
            return ""
        if who == "user":
            text = str(msg.get("content") or "").strip()
            return text or ("[图片]" if msg.get("attachments") else "")
    return ""


def schedule_chatroom_capture(sender: str, user_message: str, ai_response: str, room_type: str) -> None:
    """聊天室里一次正常回复完成后调用（失败提示、主动消息、工具后续不要调用）。"""
    schedule_capture(sender, user_message, ai_response, chat_type="group" if room_type == "group" else "private")
