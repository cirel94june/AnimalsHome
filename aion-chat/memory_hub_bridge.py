"""
Memory Hub 桥接：让 AionsHome 里的 AI 与 TG bot / 官方客户端共用同一份跨端记忆。

每轮对话：
  1. 生成前 context_block()：调用 Memory Hub 的 context(mode="incremental")，
     拿到「各端最近动态 + 与这句话相关的记忆 + 待办」，作为一段背景注入；
  2. 回复落库后 schedule_capture()：后台调用 capture(action="log")，把这一轮
     记进 Memory Hub 的对话缓冲区，由 Memory Hub 自己提取长期记忆。

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
import time
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

_cooldown_until = 0.0
_background_tasks: set[asyncio.Task] = set()


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


def _in_cooldown() -> bool:
    return time.monotonic() < _cooldown_until


def _trip(cfg: dict[str, Any], error: BaseException) -> None:
    global _cooldown_until
    _cooldown_until = time.monotonic() + float(cfg.get("failure_cooldown_seconds") or 60.0)
    log.warning("[MemoryHub] 调用失败，%ss 内跳过跨端记忆：%s", cfg.get("failure_cooldown_seconds"), error)


async def _call_tool(cfg: dict[str, Any], tool: str, arguments: dict[str, Any]) -> str:
    """开一个短连接调用一次 Memory Hub 工具，返回拼接后的文本内容。"""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url=str(cfg["url"]).strip(), headers=_headers(cfg)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
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
    if not _is_active(cfg) or _in_cooldown():
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
        _trip(cfg, error)
        return ""
    text = _unwrap_text(raw)
    if not text:
        return ""
    return _CONTEXT_HEADER + text


async def capture_turn(actor: str, user_message: str, ai_response: str, *, chat_type: str = "private") -> bool:
    """把一轮对话记入 Memory Hub；失败只记日志。"""
    cfg = load_config()
    if not _is_active(cfg) or not cfg.get("capture_enabled", True) or _in_cooldown():
        return False
    source_ai = source_ai_for(actor, cfg)
    user_message = str(user_message or "").strip()
    ai_response = str(ai_response or "").strip()
    if not source_ai or not user_message or not ai_response:
        return False
    platform = str(cfg.get("platform") or "aionshome")
    if chat_type != "private":
        platform = f"{platform}-{chat_type}"
    arguments = {
        "action": "log",
        "source_ai": source_ai,
        "user_message": user_message[:8000],
        "ai_response": ai_response[:8000],
        "platform": platform,
    }
    try:
        await asyncio.wait_for(
            _call_tool(cfg, "capture", arguments),
            timeout=float(cfg.get("capture_timeout_seconds") or 20.0),
        )
        return True
    except asyncio.CancelledError:
        raise
    except Exception as error:
        _trip(cfg, error)
        return False


def schedule_capture(actor: str, user_message: str, ai_response: str, *, chat_type: str = "private") -> None:
    """回复落库后调用：后台记录，不阻塞当前请求。"""
    if not _is_active(load_config()):
        return
    try:
        from context_builder import strip_tool_commands
        ai_response = strip_tool_commands(ai_response or "")
    except Exception:
        pass
    task = asyncio.create_task(capture_turn(actor, user_message, ai_response, chat_type=chat_type))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _chatroom_capture(room_id: str, sender: str, ai_response: str, created_at: float) -> None:
    try:
        await _chatroom_capture_inner(room_id, sender, ai_response, created_at)
    except Exception as error:
        log.warning("[MemoryHub] 聊天室记录失败：%s", error)


async def _chatroom_capture_inner(room_id: str, sender: str, ai_response: str, created_at: float) -> None:
    import aiosqlite
    from database import get_db

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT type FROM chatroom_rooms WHERE id=?", (room_id,))
        room = await cur.fetchone()
        cur = await db.execute(
            "SELECT content FROM chatroom_messages WHERE room_id=? AND created_at<=? "
            "AND sender NOT IN ('aion','connor','system') ORDER BY created_at DESC LIMIT 1",
            (room_id, created_at),
        )
        row = await cur.fetchone()
    if not row:
        return
    chat_type = "group" if room and room["type"] == "group" else "private"
    await capture_turn(sender, row["content"], ai_response, chat_type=chat_type)


def schedule_chatroom_capture(room_id: str, sender: str, ai_response: str, created_at: float) -> None:
    """聊天室里 AI 发言落库后调用：找到对应的用户发言，后台记入 Memory Hub。"""
    cfg = load_config()
    if not _is_active(cfg) or not source_ai_for(sender, cfg) or not str(ai_response or "").strip():
        return
    try:
        from context_builder import strip_tool_commands
        ai_response = strip_tool_commands(ai_response)
    except Exception:
        pass
    task = asyncio.create_task(_chatroom_capture(room_id, sender, ai_response, created_at))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
