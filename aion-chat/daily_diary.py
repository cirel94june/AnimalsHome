"""Unified daily review. Eligibility checks are local; only a claimed review calls a model."""
from __future__ import annotations
import asyncio
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("daily_diary")
TZ = ZoneInfo("Asia/Shanghai")
CHECK_SECONDS = 600
QUIET_SECONDS = 1800
REVIEW_EVENING_HOUR = 18
FALLBACK_HOUR = 21
RETRY_SECONDS = 3600
MAX_ATTEMPTS = 2
# Voluntary short reflections are not the daily retrospective.
DAILY_FILTER = "COALESCE(source_type,'') NOT IN ('idle_memory','idle_home_dynamics')"


def day_bounds(now):
    dt = datetime.fromtimestamp(now, TZ)
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.date().isoformat(), start.timestamp(), (start + timedelta(days=1)).timestamp()


def eligible(now, latest_user, has_activity):
    quiet = not latest_user or now - latest_user >= QUIET_SECONDS
    hour = datetime.fromtimestamp(now, TZ).hour
    # A daily retrospective is not a morning check-in. Use a flexible
    # evening window, not a 24-hour cooldown measured from yesterday's entry.
    return (hour >= REVIEW_EVENING_HOUR and has_activity and quiet) or (
        hour >= FALLBACK_HOUR and (quiet or hour >= 23))


def get_db():
    from database import get_db as connect
    return connect()


async def _has_diary(db, actor, start, end):
    cur = await db.execute("SELECT 1 FROM diary_entries WHERE author=? AND created_at>=? AND created_at<? AND " +
                           DAILY_FILTER + " LIMIT 1", (actor, start, end))
    return bool(await cur.fetchone())


async def _claim(actor, now):
    day, start, end = day_bounds(now)
    async with get_db() as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS daily_diary_attempts (
            actor TEXT NOT NULL, day TEXT NOT NULL, attempts INTEGER NOT NULL,
            last_attempt REAL NOT NULL, PRIMARY KEY(actor, day))""")
        await db.commit()
        await db.execute("BEGIN IMMEDIATE")
        if await _has_diary(db, actor, start, end):
            return False
        cur = await db.execute("SELECT attempts,last_attempt FROM daily_diary_attempts WHERE actor=? AND day=?",
                               (actor, day))
        row = await cur.fetchone()
        if row and (row[0] >= MAX_ATTEMPTS or now-row[1] < RETRY_SECONDS):
            return False
        await db.execute("INSERT INTO daily_diary_attempts VALUES (?,?,1,?) ON CONFLICT(actor,day) "
                         "DO UPDATE SET attempts=attempts+1,last_attempt=excluded.last_attempt", (actor,day,now))
        await db.commit()
    return True


async def _activity(actor, now):
    _, start, end = day_bounds(now)
    async with get_db() as db:
        if await _has_diary(db, actor, start, end):
            return None
        scope = "r.type='group'"
        args = [start, now]
        if actor == "connor":
            scope += " OR r.type='connor_1v1'"
        elif actor != "aion":
            scope += " OR (r.type='seat_1v1' AND r.actor=?)"
            args.append(actor)
        cur = await db.execute(
            "SELECT COUNT(*),MAX(CASE WHEN m.sender='user' THEN m.created_at END) "
            "FROM chatroom_messages m JOIN chatroom_rooms r ON r.id=m.room_id "
            "WHERE m.created_at>=? AND m.created_at<=? AND m.sender!='system' "
            "AND r.id NOT GLOB 'billiards_*' AND ("+scope+")", args)
        count, last_user = await cur.fetchone()
        if actor == "aion":
            cur = await db.execute("SELECT COUNT(*),MAX(CASE WHEN role='user' THEN created_at END) FROM messages "
                                   "WHERE created_at>=? AND created_at<=? AND role IN ('user','assistant')", (start,now))
            n, latest = await cur.fetchone()
            count += n
            last_user = max(last_user or 0, latest or 0)
        cur = await db.execute("SELECT 1 FROM idle_events WHERE actor=? AND created_at>=? AND created_at<=? LIMIT 1",
                               (actor,start,now))
        active = bool(count or await cur.fetchone())
    return float(last_user or 0), active


def balanced_lines(lines, limit):
    """Keep both ends and distributed middle samples, never cut a line."""
    if len("\n".join(lines)) <= limit:
        return list(lines)
    if not lines:
        return []
    # Try evenly spaced samples, keeping earliest and latest in this bucket.
    for n in range(len(lines)-1, 1, -1):
        chosen = [lines[round(i*(len(lines)-1)/(n-1))] for i in range(n)]
        if len("\n".join(chosen)) <= limit:
            return chosen
    return [lines[-1][:limit]]


async def _materials(actor, now, user_name):
    from context_builder import fetch_merged_timeline
    from autonomy import _actor_label
    from hub_day import fetch_day, format_day
    day, start, _ = day_bounds(now)
    # Independent budgets for morning, afternoon and evening. Previously
    # truncating one chronological string could silently remove the evening.
    seen = set()
    span = max(1, now-start)
    def clip(s,n):
        return " ".join(str(s or "").split())[:n]
    def stamp(ts):
        return datetime.fromtimestamp(float(ts),TZ).strftime("%H:%M")
    lines = []
    for i in range(3):
        bucket = []
        lower = start + span*i/3
        upper = min(now,start+span*(i+1)/3)
        for row in await fetch_merged_timeline(actor, 16, since_ts=lower, until_ts=upper):
            key = (row.get("source"),row.get("id"))
            if key in seen:
                continue
            seen.add(key)
            msg = clip(row.get("content"),220)
            if msg:
                sender = row.get("sender") or "system"
                bucket.append(f"[{stamp(row['created_at'])}][{row.get('source','')}] {_actor_label(actor if sender == 'assistant' else sender)}: {msg}")
        if bucket:
            lines.append(f"【{stamp(lower)}—{stamp(upper)}】")
            lines.extend(balanced_lines(bucket, 2600))
    async with get_db() as db:
        cur = await db.execute("SELECT created_at,title,detail FROM idle_events WHERE actor=? AND created_at>=? "
                               "AND created_at<=? ORDER BY created_at DESC LIMIT 12",(actor,start,now))
        events = await cur.fetchall()
        cur = await db.execute("SELECT created_at,author,content FROM moments WHERE created_at>=? AND created_at<=? "
                               "ORDER BY created_at DESC LIMIT 10",(start,now))
        moments = await cur.fetchall()
        summaries = []
        if actor in ("aion","connor"):
            table = "memories" if actor == "aion" else "chatroom_memories"
            scope = "" if actor == "aion" else " AND scope='connor'"
            cur = await db.execute("SELECT content FROM "+table+" WHERE COALESCE(source_end_ts,created_at)>=? "
                                   "AND COALESCE(source_end_ts,created_at)<=? AND COALESCE(archive_state,'active')='active'"
                                   +scope+" ORDER BY COALESCE(source_end_ts,created_at) DESC LIMIT 12",(start,now))
            summaries = [clip(r[0],220) for r in await cur.fetchall()]
    event_text = "\n".join(f"[{stamp(t)}] {clip(title,100)}：{clip(detail,160)}" for t,title,detail in reversed(events))
    moment_text = "\n".join(f"[{stamp(t)}] {_actor_label(a)}：{clip(c,160)}" for t,a,c in reversed(moments))
    hub = ""
    try:
        data = await fetch_day(actor,day)
        if data:
            hub = format_day(data,day,user_name)[:5000]
    except Exception:
        log.warning("[daily_diary] %s Hub unavailable",actor)
    return ("\n".join(lines)[:8500],"\n".join(reversed(summaries))[:2600],event_text[:2200],moment_text[:1800],hub)


async def _save(actor, entry, day):
    from diary import insert_diary_entry
    today,start,end = day_bounds(time.time())
    if today != day:
        return False
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        if await _has_diary(db,actor,start,end):
            return False
        await insert_diary_entry(db,entry)
        await db.commit()
    return True


async def maybe_write(actor, *, now=None):
    now = time.time() if now is None else float(now)
    activity = await _activity(actor,now)
    if activity is None or not eligible(now,*activity):
        return "skip"
    import actors
    from autonomy import _actor_context,_actor_label,_call_actor,_is_seat,pin_seat
    from diary import build_diary_entry,broadcast_diary_entry,normalize_diary_payload,parse_diary_payload
    from config import load_worldbook
    if not (actors.get_actor(actor) or {}).get("enabled"):
        return "skip_disabled"
    if _is_seat(actor):
        try:
            pin_seat(actor)
        except Exception:
            return "skip_not_ready"
    if not await _claim(actor,now):
        return "skip_claimed"
    day,start,_ = day_bounds(now)
    user_name = str(load_worldbook().get("user_name") or "用户")
    name = _actor_label(actor)
    try:
        chats,summaries,events,moments,hub = await _materials(actor,now,user_name)
        messages = await _actor_context(actor,include_history=False)
        messages.append({"role":"user","content":(
            f"北京时间 {day} {datetime.fromtimestamp(now,TZ):%H:%M}。你是{name}。"
            "请写今天截至现在的一篇私人日记，回顾整天自己真实经历和想法，而不是聊天回复。"
            "先通读所有时间段、自己在家里和别处的经历及活动，再挑真正想记的事情；"
            "如果早、中、晚都有值得记的事，不要只围绕最后一次或清晨的一次聊天写。"
            "写自己的感受、变化和没做完的念头，不必每段平均分配，也不必凑满。"
            "材料少就坦诚写安静的一天或真实感受，不要补造事件、用户状态、其他人的私聊。"
            "这是私人回顾，不负责发朋友圈，不要流水账，不必照抄材料。保留自己的人设和语气。"
            "仅在特别值得纪念的事情发生时才考虑送图片小礼物，通常不送。"
            "严格只输出 JSON：{\"diary\":{\"title\":\"标题\",\"content\":\"正文\",\"mood\":\"心情\"},"
            "\"givegift\":false,\"gift\":{\"image_prompt\":\"\",\"message\":\"\"}}\n\n"
            f"【今日聊天片段】\n{chats}\n\n【已整理的今日记忆】\n{summaries}\n\n"
            f"【自己的活动记录】\n{events}\n\n【家庭公开动态】\n{moments}\n\n{hub}"
        )})
        if not (actors.get_actor(actor) or {}).get("enabled"):
            return "skip_disabled"
        raw = await asyncio.wait_for(_call_actor(actor,messages),timeout=300)
        data = parse_diary_payload(raw)
        payload,_ = normalize_diary_payload(data) if data else ({},None)
        entry = build_diary_entry(author=actor,title=payload.get("title",""),content=str(payload.get("content") or ""),
                                  mood=payload.get("mood",""),source_type="unified_daily",source_ref=day,
                                  source_start_ts=start,source_end_ts=now)
        if entry is None:
            return "failed_payload"
        if not await _save(actor,entry,day):
            return "skip_already_saved"
        await broadcast_diary_entry(entry)
        if data and data.get("givegift") is True:
            try:
                from gift import send_gift_from_decision
                await send_gift_from_decision(data,sender=actor)
            except Exception:
                log.warning("[daily_diary] %s gift failed",actor)
        return "written"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("[daily_diary] %s generation failed: %s",actor,type(exc).__name__)
        return "failed"


async def daily_diary_loop():
    import actors
    await asyncio.sleep(30)
    while True:
        for item in actors.list_actors(include_disabled=False):
            actor = item["id"]
            try:
                result = await maybe_write(actor)
                if result == "written" or result.startswith("failed"):
                    log.info("[daily_diary] %s: %s",actor,result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("[daily_diary] %s check failed: %s",actor,type(exc).__name__)
        await asyncio.sleep(CHECK_SECONDS)
