"""小日历（11-08 小猫）：每天家里发生了什么、她的心情、聊天频率。

只读家里已有的记录（朋友圈、日记、旅行、出门、聊天条数）。聊天只数条数，不读内容。心情存 data/calendar_moods.json。
"""

from __future__ import annotations

import calendar
import json
import re
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any

from database import get_db

MOODS = ["😊", "🥰", "😌", "😐", "😢", "😡", "😴"]
SHOWN_ACTIONS = {"travel", "mcp_outing", "xhs_roam", "private_chat", "role_chat"}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_lock = threading.Lock()


class CalendarError(Exception):
    """给小猫看的话。"""


import festivals


def _path():
    from config import DATA_DIR
    return DATA_DIR / "calendar_moods.json"


def _moods() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def set_mood(day: str, mood: str, note: str = "") -> dict[str, Any]:
    _parse(day)
    note = str(note or "").strip()[:200]
    if mood and mood not in MOODS:
        raise CalendarError("这个心情不认识")
    with _lock:
        data = _moods()
        if mood or note:
            data[day] = {"mood": mood, "note": note}
        else:
            data.pop(day, None)
        path = _path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    return data.get(day) or {"mood": "", "note": ""}


def _parse(day: str) -> date:
    if not _DATE.fullmatch(day or ""):
        raise CalendarError("日期不对")
    try:
        return date.fromisoformat(day)
    except ValueError:
        raise CalendarError("日期不对") from None


def _stamp(day: date) -> float:
    return datetime(day.year, day.month, day.day).timestamp()


def _key(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _people() -> dict[str, str]:
    import actors
    from config import load_worldbook
    names = {a["id"]: a["name"] for a in actors.list_actors(include_disabled=True)}
    names["user"] = (load_worldbook().get("user_name") or "").strip() or "你"
    return names


async def _chat_counts(start: float, end: float) -> list[tuple[float, str]]:
    """只取时间和谁说的，不读内容。小克的私聊在 messages 表里。"""
    async with get_db() as db:
        rows = await (await db.execute("SELECT created_at, sender FROM chatroom_messages WHERE created_at>=? AND created_at<? AND sender!='system'", (start, end))).fetchall()
        private = await (await db.execute("SELECT created_at, role FROM messages WHERE created_at>=? AND created_at<? AND role IN ('user','assistant')", (start, end))).fetchall()
    return [(float(a), str(b)) for a, b in rows] + [(float(a), "user" if b == "user" else "aion") for a, b in private]


async def _events(start: float, end: float) -> list[dict[str, Any]]:
    names = _people()
    out: list[dict[str, Any]] = []
    async with get_db() as db:
        for ts, who, text in await (await db.execute("SELECT created_at, author, content FROM moments WHERE created_at>=? AND created_at<?", (start, end))).fetchall():
            out.append({"at": float(ts), "who": who, "name": names.get(who, who), "kind": "朋友圈", "text": str(text or "")[:200]})
        for ts, who, title, text in await (await db.execute("SELECT created_at, author, title, content FROM diary_entries WHERE created_at>=? AND created_at<?", (start, end))).fetchall():
            out.append({"at": float(ts), "who": who, "name": names.get(who, who), "kind": "日记", "text": str(title or text or "")[:200]})
        for ts, who, action, title in await (await db.execute("SELECT created_at, actor, action, title FROM idle_events WHERE created_at>=? AND created_at<?", (start, end))).fetchall():
            if action in SHOWN_ACTIONS:
                kind = {"travel": "旅行", "mcp_outing": "出门", "xhs_roam": "逛小红书"}.get(action, "主动找你")
                out.append({"at": float(ts), "who": who, "name": names.get(who, who), "kind": kind, "text": str(title or "")[:200]})
    out.sort(key=lambda e: e["at"])
    return out


async def month(year: int, month_no: int) -> dict[str, Any]:
    if not (2000 <= year <= 2100 and 1 <= month_no <= 12):
        raise CalendarError("月份不对")
    days = calendar.monthrange(year, month_no)[1]
    first = date(year, month_no, 1)
    start, end = _stamp(first), _stamp(first + timedelta(days=days))
    cells: dict[str, dict[str, Any]] = {(first + timedelta(days=i)).isoformat(): {"chats": 0, "events": 0} for i in range(days)}
    for ts, _who in await _chat_counts(start, end):
        cells[_key(ts)]["chats"] += 1
    for event in await _events(start, end):
        cells[_key(event["at"])]["events"] += 1
    moods = _moods()
    personal = festivals._personal()
    for key, cell in cells.items():
        cell["mood"] = (moods.get(key) or {}).get("mood", "")
        cell["festivals"] = festivals.on(date.fromisoformat(key), personal)
    return {"year": year, "month": month_no, "first_weekday": first.weekday(), "days": cells, "moods": MOODS, "today": date.today().isoformat()}


async def day_detail(day: str) -> dict[str, Any]:
    target = _parse(day)
    start, end = _stamp(target), _stamp(target + timedelta(days=1))
    names = _people()
    counts: dict[str, int] = {}
    for _ts, who in await _chat_counts(start, end):
        counts[who] = counts.get(who, 0) + 1
    chats = sorted(({"who": w, "name": names.get(w, w), "count": c} for w, c in counts.items()), key=lambda r: -r["count"])
    return {"date": day, "festivals": festivals.on(target), "chats": chats, "events": await _events(start, end), **(_moods().get(day) or {"mood": "", "note": ""}), "moods": MOODS,
            "generated_at": time.time()}
