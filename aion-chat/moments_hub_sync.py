"""
朋友圈进 Memory Hub（Hub #136，接口见 Hub 仓库 docs/给AionsHome的接口说明.md 优先级 1 第 5 项）。

一条朋友圈当成一个小群：每条新朋友圈、每条新评论各送一轮 POST /api/capture/log。
不在每个发朋友圈的地方各插一段（家里有 8 处会写朋友圈和评论），而是每分钟扫一次新增的，
排进 memory_hub_bridge 的待发队列；以后新加的发朋友圈入口也不会漏。

- 小猫写的：主人钥匙 + sender_type=owner，内容放 user_message；ai_id 填这条朋友圈作者的 AI，
  朋友圈本身是她发的就填 claude。
- AI 写的：内容放 ai_response，ai_id 填作者本人。没接 Hub 的座位（空位）跳过。
- event_id = aionshome-moments-<朋友圈编号>-<评论编号或 post>；reply_to_id 填被回复那条的 event_id，
  直接评论朋友圈的就填朋友圈本身的。重发时整个请求原样重发，Hub 按 ai_id + chat_id + event_id 去重。
- 只送开启以后新发的，不补送旧朋友圈。编辑 / 删除这一版不同步（删掉的由小猫在记忆页手动删）。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from contextlib import closing
from typing import Any

import aiosqlite

import memory_hub_bridge as bridge
from database import get_db

log = logging.getLogger("moments_hub_sync")

# 往回多看一段：同一时刻写进库的朋友圈，created_at 可能比上次扫到的最晚一条略早
LOOKBACK_SECONDS = 600
MAX_CHARS = 8000


def event_id_for(moment_id: str, comment_id: str | None = None) -> str:
    return f"aionshome-moments-{moment_id}-{comment_id or 'post'}"


def _state() -> sqlite3.Connection:
    conn = bridge._outbox()
    conn.execute("CREATE TABLE IF NOT EXISTS moment_hub_synced (event_id TEXT PRIMARY KEY, created_at REAL NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS moment_hub_state (key TEXT PRIMARY KEY, value REAL NOT NULL)")
    conn.commit()
    return conn


def is_enabled(cfg: dict[str, Any] | None = None) -> bool:
    cfg = cfg or bridge.load_config()
    return bool(bridge._is_active(cfg) and cfg.get("capture_enabled", True) and cfg.get("moments_enabled", True))


def _hub_ai(author: str, cfg: dict[str, Any]) -> str:
    """作者对应的 Hub AI；小猫和没接 Hub 的座位返回空串。"""
    if not author or author == "user":
        return ""
    return bridge.source_ai_for(author, cfg)


def _text(content: str, attachments: str | None = None) -> str:
    text = str(content or "").strip()
    if not text and attachments and attachments not in ("[]", "null"):
        text = "[图片]"
    return text[:MAX_CHARS]


def _body(*, moment_id: str, comment_id: str | None, text: str, by_owner: bool, ai_id: str,
          reply_to: str | None) -> dict[str, Any]:
    body = {
        "user_message": text if by_owner else "",
        "ai_response": "" if by_owner else text,
        "ai_id": ai_id,
        "platform": bridge.MOMENTS_PLATFORM,
        "chat_type": "private_group",
        "chat_id": f"moment:{moment_id}",
        "event_id": event_id_for(moment_id, comment_id),
    }
    if reply_to:
        body["reply_to_id"] = reply_to
    return body


async def _new_rows(since: float) -> tuple[list[aiosqlite.Row], list[aiosqlite.Row]]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, author, content, attachments, created_at FROM moments WHERE created_at>? ORDER BY created_at",
            (since,),
        )
        moments = await cur.fetchall()
        cur = await db.execute(
            "SELECT c.id, c.moment_id, c.author, c.content, c.reply_to_id, c.created_at, m.author AS moment_author "
            "FROM moment_comments c JOIN moments m ON m.id = c.moment_id WHERE c.created_at>? ORDER BY c.created_at",
            (since,),
        )
        comments = await cur.fetchall()
    return moments, comments


def _plan(moment_rows, comment_rows, cfg: dict[str, Any]) -> list[tuple[float, str, dict[str, Any] | None, bool]]:
    """整理成 (时间, event_id, 请求体或 None=跳过, 是否小猫写的)，按时间排好：朋友圈先于它下面的评论送。"""
    owner_moment_ai = bridge.source_ai_for("aion", cfg) or "claude"
    items: list[tuple[float, str, dict[str, Any] | None, bool]] = []
    for m in moment_rows:
        event_id = event_id_for(m["id"])
        text = _text(m["content"], m["attachments"])
        by_owner = m["author"] == "user"
        ai_id = owner_moment_ai if by_owner else _hub_ai(m["author"], cfg)
        body = _body(moment_id=m["id"], comment_id=None, text=text, by_owner=by_owner, ai_id=ai_id,
                     reply_to=None) if text and ai_id else None
        items.append((float(m["created_at"]), event_id, body, by_owner))
    for c in comment_rows:
        event_id = event_id_for(c["moment_id"], c["id"])
        text = _text(c["content"])
        by_owner = c["author"] == "user"
        if by_owner:
            moment_author = c["moment_author"]
            ai_id = owner_moment_ai if moment_author == "user" else _hub_ai(moment_author, cfg)
        else:
            ai_id = _hub_ai(c["author"], cfg)
        reply_to = event_id_for(c["moment_id"], c["reply_to_id"]) if c["reply_to_id"] else event_id_for(c["moment_id"])
        body = _body(moment_id=c["moment_id"], comment_id=c["id"], text=text, by_owner=by_owner, ai_id=ai_id,
                     reply_to=reply_to) if text and ai_id else None
        items.append((float(c["created_at"]), event_id, body, by_owner))
    items.sort(key=lambda item: (item[0], 0 if item[1].endswith("-post") else 1))
    return items


async def sync_new(now: float | None = None) -> int:
    """把上次以来新发的朋友圈和评论排进待发队列；返回排上的条数。第一次运行只记下起点，不补送旧的。"""
    cfg = bridge.load_config()
    if not is_enabled(cfg):
        return 0
    now = time.time() if now is None else now
    with closing(_state()) as conn:
        row = conn.execute("SELECT value FROM moment_hub_state WHERE key='watermark'").fetchone()
        if row is None:
            with conn:
                conn.execute("INSERT INTO moment_hub_state (key, value) VALUES ('watermark', ?)", (now,))
            log.info("[MemoryHub] 朋友圈进 Hub 开始：只送从现在起新发的")
            return 0
        watermark = float(row[0])
    moment_rows, comment_rows = await _new_rows(watermark - LOOKBACK_SECONDS)
    queued = 0
    newest = watermark
    with closing(_state()) as conn:
        for created_at, event_id, body, by_owner in _plan(moment_rows, comment_rows, cfg):
            if conn.execute("SELECT 1 FROM moment_hub_synced WHERE event_id=?", (event_id,)).fetchone():
                newest = max(newest, created_at)
                continue
            if body is not None:
                if not bridge.enqueue_moment(body, from_owner=by_owner):
                    break  # 队列满了：这条和后面的下次再排，起点不往后挪
                queued += 1
            with conn:
                conn.execute("INSERT OR IGNORE INTO moment_hub_synced (event_id, created_at) VALUES (?,?)",
                             (event_id, created_at))
            newest = max(newest, created_at)
        with conn:
            conn.execute("UPDATE moment_hub_state SET value=? WHERE key='watermark'", (newest,))
            # 去重记录只要留过回看窗口就够了
            conn.execute("DELETE FROM moment_hub_synced WHERE created_at<?", (newest - 7 * 86400,))
    if queued:
        bridge._kick_drain()
    return queued


async def sync_loop(interval_seconds: float = 60.0) -> None:
    while True:
        try:
            await sync_new()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.warning("[MemoryHub] 朋友圈送 Hub 出错：%s", error)
        await asyncio.sleep(interval_seconds)
