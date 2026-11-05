"""Short, per-role follow-up timers for natural proactive companionship."""

from __future__ import annotations

import copy
import math
import re
import time
from datetime import datetime

import aiosqlite

from config import DB_PATH, SETTINGS, save_settings


PROACTIVE_TYPE = "proactive"
# 11-05 小猫：开了主动关心以后，三位风吹草动就来找她（两天里 70 次左右没等她回就又来）。现在的规矩：
#   1. 只有在回她的话时才能约下次：她没回、自己找上门的那条（主动关心、自己醒来说的话）不再约，不会一直追；
#   2. 提示词改成「有理由才约，大多数时候不用」，最短 10 分钟；
#   3. 到点时在静默时段里就不来（和「自己醒来」同一个静默时段，在 schedule 里判断）。
MIN_NEXT_CHAT_MINUTES = 10
NO_DECISION = object()
_NEXT_CHAT_RE = re.compile(
    r"[\[［]\s*NEXT_CHAT\s*[:：]\s*([^\]］]+?)\s*[\]］]",
    re.IGNORECASE,
)
SEATS = ("ai3", "ai4", "ai5", "ai6")
_SETTING_KEYS = {
    "aion": "proactive_companionship_aion_enabled",
    "connor": "proactive_companionship_connor_enabled",
    **{seat: f"proactive_companionship_{seat}_enabled" for seat in SEATS},  # 座位各自一个开关，默认关
}


def _actor_id(actor: str | None) -> str:
    actor = str(actor or "").strip().lower()
    if actor in SEATS:
        return actor
    return "connor" if actor == "connor" else "aion"


def is_proactive_enabled(actor: str) -> bool:
    return bool(SETTINGS.get(_SETTING_KEYS[_actor_id(actor)], False))


def set_proactive_enabled(actor: str, enabled: bool) -> None:
    SETTINGS[_SETTING_KEYS[_actor_id(actor)]] = bool(enabled)
    save_settings(SETTINGS)


def proactive_ability_text(actor: str) -> str:
    if not is_proactive_enabled(actor):
        return ""
    return (
        "【主动关心】每次回复末尾决定：她一段时间没回时，要不要再主动来找她一次。"
        "只在真有理由时才安排——话说到一半想接着聊、她说了要去做某件事想过后问问结果、或者担心她现在的状态；"
        "大多数时候不需要，她会自己回来。不要为了显得关心而安排。"
        "需要时输出 [NEXT_CHAT:x]，x 为 10～60 的整数分钟；不需要时输出 [NEXT_CHAT:NONE]。"
        "每次回复都要输出其中一种。"
    )


def inject_proactive_ability(messages: list[dict], actor: str) -> list[dict]:
    text = proactive_ability_text(actor)
    if not text:
        return messages
    insert_at = len(messages)
    if messages and messages[-1].get("role") == "user":
        insert_at -= 1
    messages.insert(insert_at, {"role": "system", "content": text})
    return messages


def extract_next_chat_decision(text: str) -> tuple[str, int | None | object]:
    source = str(text or "")
    decision: int | None | object = NO_DECISION
    for match in _NEXT_CHAT_RE.finditer(source):
        raw = match.group(1).strip()
        if raw.upper() == "NONE":
            decision = None
            continue
        if raw.isdigit():
            minutes = int(raw)
            if 1 <= minutes <= 60:
                decision = max(MIN_NEXT_CHAT_MINUTES, minutes)  # 最短 10 分钟
    return _NEXT_CHAT_RE.sub("", source).strip(), decision


def _message_actor(event: dict) -> str | None:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    if event.get("type") == "msg_created":
        role = str(data.get("role") or "").lower()
        if role == "user":
            return "user"
        if role == "assistant":
            return "aion"
    if event.get("type") == "chatroom_msg_created":
        sender = str(data.get("sender") or "").lower()
        if sender in ("user", "aion", "connor", *SEATS):
            return sender
    return None


async def _is_replying_to_her(db, event_type: str | None, data: dict, actor: str) -> bool:
    """这条 AI 消息是不是在回小猫：同一个窗口里，往前找小猫和这位最近说的一句，是小猫的才算。
    她没回、这位自己找上门的那条（主动关心到点、自己醒来说的话）不算，不能接着再约下一次。"""
    msg_id = str(data.get("id") or "")
    if event_type == "msg_created":
        conv_id = str(data.get("conv_id") or "")
        if not conv_id:
            return False
        cur = await db.execute(
            "SELECT role FROM messages WHERE conv_id=? AND id!=? AND role IN ('user','assistant') "
            "AND created_at <= ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (conv_id, msg_id, float(data.get("created_at") or time.time())),
        )
        row = await cur.fetchone()
        return bool(row and row[0] == "user")
    room_id = str(data.get("room_id") or "")
    if not room_id:
        return False
    cur = await db.execute(
        "SELECT sender FROM chatroom_messages WHERE room_id=? AND id!=? AND sender IN ('user', ?) "
        "AND created_at <= ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (room_id, msg_id, actor, float(data.get("created_at") or time.time())),
    )
    row = await cur.fetchone()
    return bool(row and row[0] == "user")


async def process_visible_message_event(event: dict) -> tuple[dict, bool]:
    """Clear old timers, then apply the visible AI message's fresh decision."""
    actor = _message_actor(event)
    if actor is None:
        return event, False

    updated = copy.deepcopy(event)
    data = updated["data"]
    clean_text = str(data.get("content") or "")
    decision: int | None | object = NO_DECISION
    if actor != "user":
        clean_text, decision = extract_next_chat_decision(clean_text)
        data["content"] = clean_text

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        if actor == "user":
            cur = await db.execute(
                "DELETE FROM schedules WHERE type=? AND status='active'",
                (PROACTIVE_TYPE,),
            )
        else:
            cur = await db.execute(
                "DELETE FROM schedules WHERE type=? AND status='active' AND origin=?",
                (PROACTIVE_TYPE, actor),
            )
            table = "messages" if updated.get("type") == "msg_created" else "chatroom_messages"
            await db.execute(
                f"UPDATE {table} SET content=? WHERE id=?",
                (clean_text, str(data.get("id") or "")),
            )

            replying = await _is_replying_to_her(db, updated.get("type"), data, actor)
            if (decision is not NO_DECISION and decision is not None and is_proactive_enabled(actor)
                    and replying):
                created_at = float(data.get("created_at") or time.time())
                trigger_at = datetime.fromtimestamp(created_at + decision * 60).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                # 座位记下是在哪个房间说的，到点回到这个房间；小克 / Lucien 照旧留空（按最后活跃窗口找）
                room = str(data.get("room_id") or "") if actor in SEATS else ""
                await db.execute(
                    "INSERT INTO schedules "
                    "(id,type,trigger_at,content,created_at,status,origin,origin_room_id) "
                    "VALUES (?,?,?,?,?,'active',?,?)",
                    (
                        f"pc_{actor}_{time.time_ns()}",
                        PROACTIVE_TYPE,
                        trigger_at,
                        "",
                        created_at,
                        actor,
                        room,
                    ),
                )
        await db.commit()
        changed = bool(cur.rowcount) or actor != "user"
    return updated, changed


async def clear_proactive_timers(actor: str) -> int:
    """关掉某位 AI 的主动陪伴时，清掉 TA 还没到点的计时器（别人的不动）。"""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM schedules WHERE type=? AND status='active' AND origin=?",
            (PROACTIVE_TYPE, _actor_id(actor)),
        )
        await db.commit()
        return cur.rowcount or 0


async def claim_proactive_timer(schedule_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM schedules WHERE id=? AND type=? AND status='active'",
            (schedule_id, PROACTIVE_TYPE),
        )
        await db.commit()
        return bool(cur.rowcount)


async def proactive_status_payload() -> dict:
    from chatroom import get_chatroom_names

    _user_name, ai_name, connor_name = get_chatroom_names()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT origin, trigger_at FROM schedules "
            "WHERE type=? AND status='active' ORDER BY trigger_at",
            (PROACTIVE_TYPE,),
        )
        timers = {row["origin"]: row["trigger_at"] for row in await cur.fetchall()}

    now = time.time()
    roles = []
    people = [("aion", ai_name), ("connor", connor_name)]
    try:  # 已启用的座位（狗蛋等）也列出来，各自开关
        import actors
        for actor in actors.list_actors(include_disabled=False):
            if actor.get("id") in SEATS:
                people.append((actor["id"], actor.get("name") or actor["id"]))
    except Exception:
        pass
    for actor, name in people:
        next_at = timers.get(actor, "")
        remaining = None
        if next_at:
            try:
                due_ts = datetime.strptime(next_at, "%Y-%m-%d %H:%M:%S").timestamp()
                remaining = max(0, math.ceil((due_ts - now) / 60))
            except ValueError:
                next_at = ""
        roles.append({
            "actor": actor,
            "name": name,
            "enabled": is_proactive_enabled(actor),
            "active": bool(next_at),
            "next_at": next_at,
            "remaining_minutes": remaining,
        })
    return {"name": "主动陪伴", "roles": roles}


async def proactive_status_event() -> dict:
    return {"type": "proactive_companionship_changed", "data": await proactive_status_payload()}
