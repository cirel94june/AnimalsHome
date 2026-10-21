"""
座位（狗蛋等）写日记：和 Lucien 一样，她安静一阵、攒够新对话后，用 TA 自己的人设和模型写一篇私密日记，
可以顺手发一条朋友圈、送一份图片小礼物。

小克 / Lucien 的日记是在本地记忆总结时顺带写的；座位的记忆在 Memory Hub，没有本地总结，所以单独起一个检查。
范围和座位的合并时间线一致：TA 自己的私聊 + 所有家里群（不含台球房、系统消息）。
"""

from __future__ import annotations

import asyncio
import logging
import time

from database import get_db

log = logging.getLogger("seat_diary")

SEATS = ("ai3", "ai4", "ai5", "ai6")
MIN_NEW_MESSAGES = 20  # 攒够这么多条新对话才写
QUIET_SECONDS = 30 * 60  # 她安静这么久之后才写（和小克 / Lucien 的自动总结一样）
CHECK_INTERVAL_SECONDS = 10 * 60
MAX_CONTEXT_MESSAGES = 80
MAX_FAILURES = 3  # 连着几次写不出来就跳过这一段，免得反复花钱
_failures: dict[str, int] = {}


def _anchor_key(seat: str) -> str:
    return f"seat_diary:{seat}"


_SCOPE = (
    "FROM chatroom_messages m JOIN chatroom_rooms r ON r.id = m.room_id "
    "WHERE m.created_at > ? AND m.sender != 'system' AND r.id NOT GLOB 'billiards_*' "
    "AND (r.type = 'group' OR (r.type = 'seat_1v1' AND r.actor = ?)) "
)


async def _pending_stretch(seat: str) -> dict:
    """锚点之后这一段的概况（只读条数和时间，不读正文）：条数、起止时间、她最后一句的时间。"""
    async with get_db() as db:
        cur = await db.execute("SELECT anchor_ts FROM chatroom_digest_anchors WHERE room_id=?", (_anchor_key(seat),))
        row = await cur.fetchone()
        anchor = float(row[0]) if row else 0.0
        cur = await db.execute(
            "SELECT COUNT(*), MIN(m.created_at), MAX(m.created_at), "
            "MAX(CASE WHEN m.sender = 'user' THEN m.created_at END) " + _SCOPE,
            (anchor, seat),
        )
        count, first_ts, last_ts, last_user_ts = await cur.fetchone()
    return {"anchor": anchor, "count": int(count or 0), "first_ts": first_ts, "last_ts": last_ts,
            "last_user_ts": last_user_ts}


async def _recent_messages(seat: str, anchor: float, last_ts: float) -> list[dict]:
    """这一段（锚点之后、到这次快照的 last_ts 为止）最近的 MAX_CONTEXT_MESSAGES 条，按时间正序。
    在 SQL 里限条数，长时间积压也不会把整段正文读进内存。"""
    async with get_db() as db:
        cur = await db.execute(
            "SELECT m.sender, m.content, m.created_at " + _SCOPE + "AND m.created_at <= ? "
            "ORDER BY m.created_at DESC, m.rowid DESC LIMIT ?",
            (anchor, seat, last_ts, MAX_CONTEXT_MESSAGES),
        )
        rows = await cur.fetchall()
    return [{"sender": r[0], "content": r[1] or "", "created_at": float(r[2])} for r in reversed(rows)]


def _diary_prompt(name: str, user_name: str, context_text: str) -> str:
    return (
        f"【最近上下文】\n{context_text}\n\n"
        f"请以{name}的身份、从你自己的视角写一篇私密日记，不是写给{user_name}看的聊天消息。"
        "日记可以记录你对这段时间的感想，只写值得记录或有感触的事，不用每件事都提起，不要记流水账。语气必须符合你的人设。"
        "你可以自行决定是否发布一次朋友圈，朋友圈不用每次都发，有想吐槽或者感慨，或者搞笑的事情，或者用朋友圈隔空向别人喊话。"
        f"涉及和{user_name}的亲密关系、她的身体、她的私人情感的内容只写进日记，不要发到朋友圈。\n"
        "moment.expect_reply 表示发布朋友圈后是否希望别的角色来评论：true=希望有人回复，false=只发布。\n"
        f"你可以自行决定是否给{user_name}送一份图片小礼物。送礼应是更低概率的特殊事件，只在特殊日子，"
        "或聊天中确实有特别温馨、感动、有意义、值得纪念的内容时才送；不要为了完成任务而送礼。\n"
        "givegift 为 true 时，gift.image_prompt 填写英文生图提示词，gift.message 填写符合你人设、自然真挚的赠言；"
        "为 false 时两项留空。\n\n"
        "严格只输出 JSON，不要输出 Markdown，不要解释：\n"
        "{\n"
        '  "diary": {"title": "日记标题", "content": "日记正文", "mood": "此刻心情"},\n'
        '  "post_moment": false,\n'
        '  "moment": {"content": "朋友圈内容，post_moment 为 false 时留空", "expect_reply": false},\n'
        '  "givegift": false,\n'
        '  "gift": {"image_prompt": "英文生图提示词，givegift 为 false 时留空", "message": "赠言，givegift 为 false 时留空"}\n'
        "}"
    )


async def _skip_stretch(seat: str, last_ts: float) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT INTO chatroom_digest_anchors (room_id, anchor_ts) VALUES (?, ?) "
            "ON CONFLICT(room_id) DO UPDATE SET anchor_ts=excluded.anchor_ts",
            (_anchor_key(seat), last_ts),
        )
        await db.commit()


def _count_failure(seat: str) -> bool:
    """记一次写不出来；连续 MAX_FAILURES 次返回 True（这一段跳过）。"""
    _failures[seat] = _failures.get(seat, 0) + 1
    if _failures[seat] >= MAX_FAILURES:
        _failures[seat] = 0
        return True
    return False


async def maybe_write_seat_diary(seat: str, *, now: float | None = None) -> str:
    """满足条件就写一篇；返回这次的结果（skip_* / written / failed），方便日志和测试。"""
    import seat_chat
    from config import ensure_model_unchanged, load_worldbook, model_snapshot
    from ai_providers import CLI_STATUS_PREFIX, stream_ai
    from diary import (
        broadcast_diary_entry, build_diary_entry, insert_diary_entry, normalize_diary_payload,
        parse_diary_payload, publish_ai_moment,
    )

    now = now or time.time()
    try:
        _, model_key = seat_chat.check_ready(seat)
    except seat_chat.SeatNotReady:
        return "skip_not_ready"
    pinned = model_snapshot(model_key)  # 紧接着固定线路，和第一次等待之间不让出
    stretch = await _pending_stretch(seat)
    if stretch["count"] < MIN_NEW_MESSAGES:
        return "skip_too_few"
    if not stretch["last_user_ts"]:
        return "skip_no_user"
    if now - float(stretch["last_user_ts"]) < QUIET_SECONDS:
        return "skip_not_quiet"
    last_ts = float(stretch["last_ts"])
    recent = await _recent_messages(seat, stretch["anchor"], last_ts)

    user_name = (load_worldbook().get("user_name") or "用户").strip() or "用户"
    name = seat_chat.actors.display_name(seat)
    context_text = "\n".join(
        f"【{seat_chat._speaker_name(m['sender'], user_name)}】{m['content']}" for m in recent if m["content"].strip()
    )
    messages = [
        {"role": "user", "content": f"[你的角色设定]\n你是{name}。\n\n{seat_chat._persona_text(seat)}"},
        {"role": "assistant", "content": "收到，我会按照设定做自己。"},
        {"role": "user", "content": _diary_prompt(name, user_name, context_text)},
    ]
    # 开口前再确认一次：仍启用、有人设、所选模型和线路都没变；之后到发请求之间没有 await。
    # 设置变了算跳过（下次再写），不算「写不出来」
    try:
        _, current = seat_chat.check_ready(seat)
        if current != model_key:
            return "skip_not_ready"
        ensure_model_unchanged(model_key, pinned)
    except Exception:
        return "skip_not_ready"
    raw = ""
    try:
        async with asyncio.timeout(300):
            async for chunk in stream_ai(messages, model_key):
                if isinstance(chunk, str) and not chunk.startswith(CLI_STATUS_PREFIX):
                    raw += chunk
    except Exception as error:
        log.warning("[seat_diary] %s 生成日记失败：%s", seat, error)
        raw = ""

    data = parse_diary_payload(raw) if raw else None
    diary_entry, moment_entry = normalize_diary_payload(data) if data else ({}, None)
    entry = build_diary_entry(
        author=seat,
        title=diary_entry.get("title", ""),
        content=str(diary_entry.get("content") or ""),
        mood=diary_entry.get("mood", ""),
        source_type="seat_digest",
        source_ref=seat,
        source_start_ts=float(stretch["first_ts"]),
        source_end_ts=last_ts,
    ) if data else None
    if entry is None:  # 解析不了，或者日记正文是空的：都算写不出来
        if _count_failure(seat):  # 这一段连着写不出来就跳过，下一段重新开始
            await _skip_stretch(seat, last_ts)
        return "failed"
    _failures[seat] = 0
    # 日记和锚点在同一个事务里提交：要么都成功，要么都没有；中途停机重启不会给同一段写第二篇
    async with get_db() as db:
        await insert_diary_entry(db, entry)
        await db.execute(
            "INSERT INTO chatroom_digest_anchors (room_id, anchor_ts) VALUES (?, ?) "
            "ON CONFLICT(room_id) DO UPDATE SET anchor_ts=excluded.anchor_ts",
            (_anchor_key(seat), last_ts),
        )
        await db.commit()
    await broadcast_diary_entry(entry)

    from capabilities import is_capability_enabled
    if moment_entry and moment_entry.get("content") and is_capability_enabled("moment"):
        from routes.moments import MAX_AI_MOMENT_CHARS
        await publish_ai_moment(
            author=seat,
            content=str(moment_entry.get("content") or "")[:MAX_AI_MOMENT_CHARS],
            expect_reply=bool(moment_entry.get("expect_reply")),
            source_conv=f"seat_diary:{seat}",
        )
    try:
        from gift import send_gift_from_decision
        await send_gift_from_decision(data, sender=seat)
    except Exception as error:
        log.warning("[seat_diary] %s 送礼失败：%s", seat, error)
    return "written"


async def seat_diary_loop() -> None:
    """常驻后台：定时看看各位座位要不要写日记。"""
    while True:
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        for seat in SEATS:
            try:
                result = await maybe_write_seat_diary(seat)
                if result in ("written", "failed"):
                    log.info("[seat_diary] %s：%s", seat, result)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log.warning("[seat_diary] %s 检查出错：%s", seat, error)
