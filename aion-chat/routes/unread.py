"""聊天未读：哪些窗口有 AI 的新消息小猫还没看（11-01 小猫：去旅行页再回来，不知道哪里有新回复）。

「看到哪里」存在服务器上，手机和电脑共用。只看 AI 发的消息；小猫自己发的、系统提示不算。
第一次用时把「现在」记成起点，以前的消息不算未读，免得一上线满屏红点。
"""

from __future__ import annotations

import re
import time

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import SETTINGS, save_settings
from database import get_db

router = APIRouter()

LOOKBACK_SECONDS = 30 * 24 * 3600
_NOT_AI = ("user", "system", "")


class ReadBody(BaseModel):
    container: str
    # 页面实际显示到的最后一条消息的时间；只记到这里，没加载出来的新消息仍算未读（Codex §122 M1）
    at: float | None = None


def _baseline() -> float:
    baseline = float(SETTINGS.get("unread_baseline_at") or 0)
    if not baseline:
        baseline = time.time()
        SETTINGS["unread_baseline_at"] = baseline
        save_settings(SETTINGS)
    return baseline


async def _ensure_table(db) -> None:
    await db.execute(
        "CREATE TABLE IF NOT EXISTS unread_read_marks (container TEXT PRIMARY KEY, read_at REAL NOT NULL)"
    )


_CONTAINER_RE = re.compile(r"(conv|room):([A-Za-z0-9_.-]{1,120})")


def _parse_container(container: str) -> tuple[str, str] | None:
    """只认「conv:编号」「room:编号」，编号里不能有空白、冒号等（Codex §122 L1）。"""
    match = _CONTAINER_RE.fullmatch(container or "")
    return (match.group(1), match.group(2)) if match else None


def _valid_container(container: str) -> bool:
    return _parse_container(container) is not None


@router.get("/api/unread")
async def unread_summary():
    baseline = _baseline()
    since = max(baseline, time.time() - LOOKBACK_SECONDS)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await _ensure_table(db)
        marks = {row["container"]: float(row["read_at"]) for row in await (await db.execute(
            "SELECT container, read_at FROM unread_read_marks")).fetchall()}
        latest: list[tuple[str, str, float, str]] = []
        for row in await (await db.execute(
                "SELECT conv_id, MAX(created_at) AS last_at FROM messages "
                "WHERE role='assistant' AND created_at > ? GROUP BY conv_id", (since,))).fetchall():
            latest.append((f"conv:{row['conv_id']}", "private", float(row["last_at"]), "aion"))
        rooms = {row["id"]: row for row in await (await db.execute(
            "SELECT id, type, actor FROM chatroom_rooms")).fetchall()}
        for row in await (await db.execute(
                "SELECT room_id, MAX(created_at) AS last_at FROM chatroom_messages "
                f"WHERE sender NOT IN ({','.join('?' for _ in _NOT_AI)}) AND created_at > ? GROUP BY room_id",
                (*_NOT_AI, since))).fetchall():
            room = rooms.get(row["room_id"])
            if room is None:
                continue
            kind = "group" if room["type"] == "group" else "private"
            actor = "connor" if room["type"] == "connor_1v1" else (room["actor"] or "")
            latest.append((f"room:{row['room_id']}", kind, float(row["last_at"]), actor))
    unread = [
        {"container": container, "kind": kind, "last_at": last_at, "actor": actor}
        for container, kind, last_at, actor in latest
        if last_at > marks.get(container, baseline)
    ]
    unread.sort(key=lambda item: item["last_at"], reverse=True)
    return {
        "unread": unread,
        "private": any(item["kind"] == "private" for item in unread),
        "group": any(item["kind"] == "group" for item in unread),
    }


@router.post("/api/unread/read")
async def mark_read(body: ReadBody):
    """小猫正在看这个窗口：记到她看到的那条（只往后挪，不往回，不超过现在）。"""
    parsed = _parse_container(body.container)
    if parsed is None:
        raise HTTPException(400, "窗口不对")
    kind, ident = parsed
    now = time.time()
    read_at = now if body.at is None else min(float(body.at), now)
    if read_at <= 0:
        raise HTTPException(400, "时间不对")
    table = "conversations" if kind == "conv" else "chatroom_rooms"
    async with get_db() as db:
        if await (await db.execute(f"SELECT 1 FROM {table} WHERE id=?", (ident,))).fetchone() is None:
            raise HTTPException(404, "没有这个窗口")
        await _ensure_table(db)
        await db.execute(
            "INSERT INTO unread_read_marks (container, read_at) VALUES (?, ?) "
            "ON CONFLICT(container) DO UPDATE SET read_at=MAX(read_at, excluded.read_at)",
            (body.container, read_at),
        )
        await db.commit()
    return {"ok": True, "read_at": read_at}
