"""
小作品（11-09 小猫）：AI 自己决定给小猫做的小网页，放在「爱的印记」旁边，她不在线时也能准备。

- 每位 AI 每天最多想一次；只有快到特殊日子，或者偶尔的随机时候才想，平时不花 token。
- 硬上限：每位 AI 滚动 7 天最多 WEEKLY_LIMIT 个。提示词也叫它别频繁，但不靠提示词管住。
- 网页是不受信任的内容：只在 sandbox 的 iframe 里显示（见 static/works.html），
  不同源、不能联网，读不到登录状态和聊天。这里只存文本，不当页面直接返回。
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import sqlite3
import time
import uuid
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from config import BASE_DIR, DATA_DIR

router = APIRouter()
log = logging.getLogger("uvicorn.error")

DB_PATH = DATA_DIR / "html_works.db"
WEEKLY_LIMIT = 2
MAX_HTML_CHARS = 120_000
RANDOM_DAY_CHANCE = 0.1
TICK_SECONDS = 600
OCCASION_WITHIN_DAYS = 3
OCCASION_MEMORY_DAYS = 30
_HTML_START = re.compile(r"<!doctype html|<html[\s>]", re.I)
_tasks: set[asyncio.Task] = set()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS works (
        id TEXT PRIMARY KEY, actor TEXT NOT NULL, name TEXT NOT NULL, title TEXT NOT NULL, note TEXT NOT NULL,
        html TEXT NOT NULL, occasion TEXT NOT NULL, created_at REAL NOT NULL, opened_at REAL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS plans (
        actor TEXT NOT NULL, day TEXT NOT NULL, due_at REAL NOT NULL, done INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (actor, day))""")
    return conn


def recent_count(actor: str, now: float | None = None) -> int:
    now = time.time() if now is None else now
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM works WHERE actor=? AND created_at>?", (actor, now - 7 * 86400)).fetchone()[0]


def occasion_done(actor: str, occasion: str, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM works WHERE actor=? AND occasion=? AND created_at>?",
                            (actor, occasion, now - OCCASION_MEMORY_DAYS * 86400)).fetchone() is not None


def save_work(actor: str, name: str, title: str, note: str, html: str, occasion: str, now: float | None = None) -> str | None:
    """存一个作品。上限在写入的同一个事务里再核一次。"""
    now = time.time() if now is None else now
    work_id = f"w_{uuid.uuid4().hex[:16]}"
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        count = conn.execute("SELECT COUNT(*) FROM works WHERE actor=? AND created_at>?", (actor, now - 7 * 86400)).fetchone()[0]
        if count >= WEEKLY_LIMIT:
            return None
        conn.execute("INSERT INTO works (id, actor, name, title, note, html, occasion, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (work_id, actor, name, title[:40] or "小作品", note[:200], html, occasion, now))
    return work_id


def list_works() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT id, name, title, note, occasion, created_at, opened_at FROM works ORDER BY created_at DESC LIMIT 200").fetchall()
    return [dict(r) for r in rows]


def get_work(work_id: str, mark_opened: bool = True, now: float | None = None) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT id, name, title, note, html, occasion, created_at FROM works WHERE id=?", (work_id,)).fetchone()
        if row is not None and mark_opened:
            conn.execute("UPDATE works SET opened_at=COALESCE(opened_at, ?) WHERE id=?", (time.time() if now is None else now, work_id))
    return dict(row) if row else None


def delete_work(work_id: str) -> bool:
    with _connect() as conn:
        return conn.execute("DELETE FROM works WHERE id=?", (work_id,)).rowcount == 1


def parse_reply(raw: str) -> tuple[str, str, str] | None:
    """(标题, 赠言, 网页)。模型说不做、没有网页、太长，都返回 None。"""
    match = _HTML_START.search(raw or "")
    if not match:
        return None
    head, body = raw[:match.start()], raw[match.start():]
    end = body.lower().rfind("</html>")
    if end >= 0:
        body = body[:end + len("</html>")]
    if len(body) > MAX_HTML_CHARS:
        return None
    title = re.search(r"标题[:：]\s*(.+)", head)
    note = re.search(r"赠言[:：]\s*(.+)", head)
    return (title.group(1).strip() if title else "", note.group(1).strip() if note else "", body)


def plan_today(actor: str, today: date, now: float, rng=random) -> None:
    """今天这位 AI 什么时候想一下（到今晚 23 点前的随机时刻）。"""
    end = datetime.combine(today, datetime.min.time()).timestamp() + 23 * 3600
    due = now + rng.random() * max(0.0, end - now)
    with _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO plans (actor, day, due_at) VALUES (?,?,?)", (actor, today.isoformat(), due))


def claim_plan(actor: str, today: date, now: float) -> bool:
    with _connect() as conn:
        return conn.execute("UPDATE plans SET done=1 WHERE actor=? AND day=? AND done=0 AND due_at<=?",
                            (actor, today.isoformat(), now)).rowcount == 1


def choose_occasion(actor: str, today: date, rng=random) -> tuple[bool, str]:
    """要不要想这一下，为了什么日子。没有日子时偶尔想一次。"""
    import festivals

    for offset, name in festivals.upcoming(today, OCCASION_WITHIN_DAYS):
        if not occasion_done(actor, name):
            return True, f"{name}（{'今天' if offset == 0 else f'{offset}天后'}）"
    return rng.random() < RANDOM_DAY_CHANCE, ""


def build_prompt(user_name: str, occasion: str, recent: str, left: int) -> str:
    reason = f"快到{occasion}了。" if occasion else "今天没有什么特别的日子。"
    return (
        f"{reason}你可以悄悄给{user_name}做一个小网页当礼物，比如小卡片、小动画、小游戏，她之后会在「爱的印记」里看到。\n\n"
        f"{recent}"
        f"这件事很费 token，不要频繁。你这周最多还能做 {left} 个，用不完也没关系。"
        "如果没有真的想送的东西，就只回复两个字：不做。不用勉强，大多数时候不做才是正常的。\n\n"
        "如果要做，按这个格式：\n第一行「标题：作品名」\n第二行「赠言：想对她说的一句话」\n第三行起是完整的 HTML。\n"
        "要求：所有样式和脚本都写在这一个文件里；不能加载任何外部资源（图片、字体、代码库都不行，也连不了网），图案用 CSS、SVG 或 canvas 画；"
        "手机竖屏能看；越短越好，不要超过 300 行。"
    )


async def consider(seat: dict[str, str], occasion_key: str, user_name: str) -> None:
    from ai_providers import CLI_STATUS_PREFIX, stream_ai
    from hub_day import day_text

    left = WEEKLY_LIMIT - await asyncio.to_thread(recent_count, seat["id"])
    if left <= 0:
        return
    try:
        recent = await day_text(seat["id"], user_name)
    except Exception:
        recent = ""
    messages = []
    if seat.get("persona"):
        messages += [{"role": "user", "content": f"[你的角色设定]\n你是{seat['name']}。\n\n{seat['persona']}"},
                     {"role": "assistant", "content": "收到，我会按照设定做自己。"}]
    messages.append({"role": "user", "content": build_prompt(user_name, occasion_key, recent, left)})
    raw = ""
    async with asyncio.timeout(420):
        async for chunk in stream_ai(messages, seat["model"]):
            if isinstance(chunk, str) and not chunk.startswith(CLI_STATUS_PREFIX):
                raw += chunk
    parsed = parse_reply(raw)
    if not parsed:
        return
    title, note, html = parsed
    occasion = occasion_key.split("（")[0]
    saved = await asyncio.to_thread(save_work, seat["id"], seat["name"], title, note, html, occasion)
    log.info("[html_works] %s 做了一个小作品：%s", seat["id"], bool(saved))


async def tick(now: float | None = None) -> None:
    from config import load_worldbook
    from story_spark import ready_seats

    now = time.time() if now is None else now
    today = date.fromtimestamp(now)
    user_name = (load_worldbook().get("user_name") or "用户").strip() or "用户"
    for seat in await asyncio.to_thread(ready_seats):
        await asyncio.to_thread(plan_today, seat["id"], today, now)
        if not await asyncio.to_thread(claim_plan, seat["id"], today, now):
            continue
        if await asyncio.to_thread(recent_count, seat["id"], now) >= WEEKLY_LIMIT:
            continue
        wanted, occasion = await asyncio.to_thread(choose_occasion, seat["id"], today)
        if wanted:
            try:
                await consider(seat, occasion, user_name)
            except Exception as error:
                log.warning("[html_works] %s 这次没做成：%s", seat["id"], type(error).__name__)


async def _loop() -> None:
    while True:
        await asyncio.sleep(TICK_SECONDS)
        try:
            await tick()
        except Exception as error:
            log.warning("[html_works] 循环出错：%s", type(error).__name__)


def start() -> None:
    task = asyncio.create_task(_loop())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


@router.get("/works")
async def page():
    return FileResponse(BASE_DIR / "static" / "works.html", headers={"Cache-Control": "no-cache"})


@router.get("/api/works")
async def api_list():
    return {"works": await asyncio.to_thread(list_works)}


@router.get("/api/works/{work_id}")
async def api_get(work_id: str):
    work = await asyncio.to_thread(get_work, work_id)
    if not work:
        raise HTTPException(404, "没有这个小作品")
    return work


@router.delete("/api/works/{work_id}")
async def api_delete(work_id: str):
    return {"ok": await asyncio.to_thread(delete_work, work_id)}

