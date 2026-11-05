"""
回家借工具：TG 上的 bot（出门的小克 / Lucien / 狗蛋）需要家里才有的功能时，请求这里，
家里办完把纯文字结果交回，bot 再用自己的口吻在 TG 里说。方案见 docs/施工方案-bot出门.md。

两把钥匙一起才进得来：
- 门口：Cloudflare 服务钥匙，只对 /api/outing/* 有效（cf_access.py 里单独核对，见那边说明）；
- 这里：每只 bot 一把自己的钥匙（/etc/aionshome.env 里 AIONSHOME_OUTING_KEY_AION / _CONNOR / _AI3 …），
  认出是哪个座位。没配钥匙的座位借不了。

规矩：
- 每只 bot 能借哪些工具、每小时几次，在 data/outing_tools.json（开关总览里能停）；
- 先认领额度（同一个事务里数次数、记下 request_id），再去办事；同一个 request_id 只办一次；
- 并发：每只同时最多 2 个，全家同时最多 4 个，满了直接回「家里正忙」，不排队；
- 总超时 30 秒；结果最多 3000 字；
- 朋友大群（public_group）里只有小猫开口叫他查时（owner_asked=true）才借；
- 日志只记谁、哪个工具、多久、成没成和失败分类，不记搜索词和结果正文；错误只回分类。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import time
from contextlib import closing
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from config import DATA_DIR
from request_limits import read_body_limited
from stream_safety import describe_failure

log = logging.getLogger("outing_tools")
router = APIRouter()

CONFIG_PATH = DATA_DIR / "outing_tools.json"
DB_PATH = DATA_DIR / "outing_tools.db"
TOOLS = ("search", "read", "taobao")
TOOL_LABELS = {"search": "联网搜索", "read": "读网页", "taobao": "淘宝搜商品"}
SEATS = ("aion", "connor", "ai3", "ai4", "ai5", "ai6")
DEFAULT_PER_HOUR = 30
PER_BOT_CONCURRENCY = 2
GLOBAL_CONCURRENCY = 4
TOTAL_TIMEOUT_SECONDS = 30.0
MAX_RESULT_CHARS = 3000
MAX_BODY_BYTES = 16 * 1024
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{8,120}$")

_running: dict[str, int] = {}
_running_total = 0


class OutingRefused(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


# ── 钥匙 ──

def _key_env(actor: str) -> str:
    return f"AIONSHOME_OUTING_KEY_{actor.upper()}"


def actor_for_key(presented: str) -> str:
    """bot 带来的钥匙是哪个座位的；对不上返回空串。逐个用定长比较，不提前退出。"""
    presented_digest = hashlib.sha256(str(presented or "").encode()).digest()
    found = ""
    for actor in SEATS:
        key = os.environ.get(_key_env(actor), "").strip()
        if len(key) < 24:  # 太短的钥匙当没配
            continue
        if hmac.compare_digest(presented_digest, hashlib.sha256(key.encode()).digest()):
            found = actor
    return found


def _bearer(request: Request) -> str:
    value = request.headers.get("authorization", "")
    return value[7:].strip() if value[:7].lower() == "bearer " else ""


# ── 设置 ──

def load_settings() -> dict[str, Any]:
    data: dict[str, Any] = {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    bots = data.get("bots") if isinstance(data.get("bots"), dict) else {}
    result = {"enabled": data.get("enabled", True) is not False, "bots": {}}
    for actor in SEATS:
        raw = bots.get(actor) if isinstance(bots.get(actor), dict) else {}
        tools = raw.get("tools")
        result["bots"][actor] = {
            "enabled": raw.get("enabled", True) is not False,
            "tools": [t for t in tools if t in TOOLS] if isinstance(tools, list) else list(TOOLS),
            "per_hour": max(0, min(200, int(raw.get("per_hour", DEFAULT_PER_HOUR) or 0))),
            "key_set": len(os.environ.get(_key_env(actor), "").strip()) >= 24,
        }
    return result


def save_settings(value: dict[str, Any]) -> dict[str, Any]:
    current = load_settings()
    bots = value.get("bots") if isinstance(value.get("bots"), dict) else {}
    stored: dict[str, Any] = {"enabled": bool(value.get("enabled", current["enabled"])), "bots": {}}
    for actor in SEATS:
        old = current["bots"][actor]
        raw = bots.get(actor) if isinstance(bots.get(actor), dict) else {}
        tools = raw.get("tools", old["tools"])
        if not isinstance(tools, list) or any(t not in TOOLS for t in tools):
            raise ValueError("不认识的工具")
        per_hour = raw.get("per_hour", old["per_hour"])
        if not isinstance(per_hour, int) or isinstance(per_hour, bool) or not 0 <= per_hour <= 200:
            raise ValueError("每小时次数要在 0～200 之间")
        stored["bots"][actor] = {"enabled": bool(raw.get("enabled", old["enabled"])), "tools": tools,
                                 "per_hour": per_hour}
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)
    return load_settings()


# ── 额度和日志 ──

def _db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5, isolation_level=None)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS outing_calls ("
        " request_id TEXT NOT NULL, actor TEXT NOT NULL, tool TEXT NOT NULL, chat_type TEXT NOT NULL,"
        " created_at REAL NOT NULL, finished_at REAL, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',"
        " PRIMARY KEY (actor, request_id))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_outing_calls_actor_time ON outing_calls(actor, created_at)")
    return conn


def claim(actor: str, request_id: str, tool: str, chat_type: str, per_hour: int, now: float | None = None) -> None:
    """先认领：同一个事务里数这一小时用了几次、登记这个 request_id。不行就抛 OutingRefused。"""
    now = time.time() if now is None else now
    with closing(_db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT 1 FROM outing_calls WHERE actor=? AND request_id=?",
                            (actor, request_id)).fetchone():
                raise OutingRefused(409, "这个编号已经借过了")
            (used,) = conn.execute("SELECT COUNT(*) FROM outing_calls WHERE actor=? AND created_at>?",
                                   (actor, now - 3600)).fetchone()
            if used >= per_hour:
                raise OutingRefused(429, "这一小时借的次数用完了")
            conn.execute("INSERT INTO outing_calls (request_id, actor, tool, chat_type, created_at, status)"
                         " VALUES (?,?,?,?,?, 'running')", (request_id, actor, tool, chat_type, now))
            conn.execute("DELETE FROM outing_calls WHERE created_at<?", (now - 30 * 86400,))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def finish(actor: str, request_id: str, status: str, reason: str = "") -> None:
    with closing(_db()) as conn:
        conn.execute("UPDATE outing_calls SET status=?, reason=?, finished_at=? WHERE actor=? AND request_id=?",
                     (status, reason[:120], time.time(), actor, request_id))


def recent_calls(limit: int = 50) -> list[dict[str, Any]]:
    if not DB_PATH.exists():
        return []
    with closing(_db()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM outing_calls ORDER BY created_at DESC LIMIT ?",
                            (max(1, min(200, limit)),)).fetchall()
    return [dict(r) for r in rows]


def _take_slot(actor: str) -> bool:
    global _running_total
    if _running_total >= GLOBAL_CONCURRENCY or _running.get(actor, 0) >= PER_BOT_CONCURRENCY:
        return False
    _running[actor] = _running.get(actor, 0) + 1
    _running_total += 1
    return True


def _release_slot(actor: str) -> None:
    global _running_total
    _running[actor] = max(0, _running.get(actor, 0) - 1)
    _running_total = max(0, _running_total - 1)


# ── 工具 ──

def _clip(text: str) -> str:
    text = str(text or "").strip()
    return text if len(text) <= MAX_RESULT_CHARS else text[: MAX_RESULT_CHARS - 1] + "…"


def _arg(args: dict[str, Any], name: str, limit: int) -> str:
    value = str(args.get(name) or "").strip()
    if not value or len(value) > limit:
        raise OutingRefused(400, f"{name} 要填 1～{limit} 个字")
    return value


async def run_tool(tool: str, args: dict[str, Any]) -> str:
    if tool == "search":
        import web_search
        query = _arg(args, "query", 200)
        payload = await web_search.tavily_search(query, max_results=5, timeout=TOTAL_TIMEOUT_SECONDS - 5)
        return web_search.format_tavily_context(payload, kind="search") or "没搜到相关结果"
    if tool == "read":
        import web_search
        url = _arg(args, "url", 1000)
        if not re.match(r"^https?://", url, re.I):
            raise OutingRefused(400, "只能读 http / https 网页")
        payload = await web_search.tavily_extract(url, query=str(args.get("query") or "")[:200],
                                                  timeout=TOTAL_TIMEOUT_SECONDS - 10)
        return web_search.format_tavily_context(payload, kind="extract") or "这个网页没读到正文"
    if tool == "taobao":
        import taobao_union
        from taobao_shopping import get_store
        keyword = _arg(args, "keyword", 60)
        settings = await (await get_store()).settings()
        result = await taobao_union.search(keyword, app_key=settings.get("union_app_key", ""),
                                           adzone_id=settings.get("union_adzone_id", ""), page_size=5)
        lines = [f"- {p['title']}｜{p.get('price') or '价格未知'}｜{p.get('shop') or ''}｜{p['url']}"
                 for p in result["products"][:5]]
        return "\n".join(lines) if lines else "淘宝上没找到合适的"
    raise OutingRefused(400, "不认识的工具")


# ── 接口 ──

@router.post("/api/outing/tool")
async def api_outing_tool(request: Request):
    actor = actor_for_key(_bearer(request))
    if not actor:
        raise HTTPException(401, "钥匙不对")
    try:
        body = json.loads(await read_body_limited(request, MAX_BODY_BYTES, "请求太大"))
    except ValueError:
        raise HTTPException(400, "格式不正确")
    if not isinstance(body, dict):
        raise HTTPException(400, "格式不正确")
    tool = str(body.get("tool") or "")
    request_id = str(body.get("request_id") or "")
    chat_type = str(body.get("chat_type") or "private")
    args = body.get("args") if isinstance(body.get("args"), dict) else {}
    if tool not in TOOLS:
        raise HTTPException(400, "不认识的工具")
    if not _REQUEST_ID.match(request_id):
        raise HTTPException(400, "request_id 格式不对")
    if chat_type not in ("private", "private_group", "public_group"):
        raise HTTPException(400, "chat_type 不对")

    settings = load_settings()
    bot = settings["bots"][actor]
    if not settings["enabled"] or not bot["enabled"]:
        raise HTTPException(403, "家里暂停了借工具")
    if tool not in bot["tools"]:
        raise HTTPException(403, f"{TOOL_LABELS[tool]}没开给这位")
    if chat_type == "public_group" and body.get("owner_asked") is not True:
        raise HTTPException(403, "大群里要小猫开口叫你查，才能回家借")

    if not _take_slot(actor):
        raise HTTPException(429, "家里正忙，等一下再来")
    started = time.monotonic()
    try:
        try:
            claim(actor, request_id, tool, chat_type, bot["per_hour"])
        except OutingRefused as refused:
            raise HTTPException(refused.status, refused.reason) from None
        try:
            result = await asyncio.wait_for(run_tool(tool, args), timeout=TOTAL_TIMEOUT_SECONDS)
        except OutingRefused as refused:
            finish(actor, request_id, "refused", refused.reason)
            raise HTTPException(refused.status, refused.reason) from None
        except asyncio.TimeoutError:
            finish(actor, request_id, "timeout", "超过 30 秒")
            log.warning("[outing] %s %s %s 超时", request_id, actor, tool)
            return {"ok": False, "reason": "家里这次没在 30 秒内办完"}
        except Exception as error:  # 只回分类，不把家里的内部报错交出去
            reason = describe_failure(str(error)) or "家里办的时候出错了"
            if "没填好" in str(error) or "未配置" in str(error):
                reason = "家里这个工具还没配好"
            finish(actor, request_id, "failed", reason)
            log.warning("[outing] %s %s %s 失败：%s", request_id, actor, tool, reason)
            return {"ok": False, "reason": reason}
        finish(actor, request_id, "ok")
        log.info("[outing] %s %s %s 成功 %dms", request_id, actor, tool, (time.monotonic() - started) * 1000)
        return {"ok": True, "tool": tool, "result": _clip(result)}
    finally:
        _release_slot(actor)


# 下面两个只给小猫自己的页面用（经 Cloudflare 时要她的登录；服务钥匙进不来，见 cf_access.py）

@router.get("/api/outing-settings")
async def api_outing_settings():
    return {**load_settings(), "recent": recent_calls(30), "tools": TOOL_LABELS}


@router.put("/api/outing-settings")
async def api_save_outing_settings(request: Request):
    try:
        value = await request.json()
        return save_settings(value if isinstance(value, dict) else {})
    except ValueError as error:
        raise HTTPException(400, str(error) or "格式不正确")
