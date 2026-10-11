"""Calendar-based, traceable memory compression.

Models receive period-grouped memory text only. Database identifiers and
archive transitions remain deterministic application responsibilities.
"""

from __future__ import annotations

import json
import asyncio
import logging
import re
import time
from calendar import monthrange
from datetime import datetime, timedelta
from typing import Iterable

import aiosqlite

from ai_providers import simple_ai_call
from config import MODELS, is_model_deprecated, load_worldbook
from database import get_db


DAY_START_HOUR = 5
LEVELS = {
    "daily": {
        "source_stage": 0,
        "source_period_kind": "",
        "output_stage": 1,
        "output_period_kind": "day",
        "periods_per_call": 7,
        "title": "每日记忆",
    },
    "weekly": {
        "source_stage": 1,
        "source_period_kind": "day",
        "output_stage": 2,
        "output_period_kind": "week",
        "periods_per_call": 8,
        "title": "每周记忆",
    },
    "monthly": {
        "source_stage": 2,
        "source_period_kind": "week",
        "output_stage": 3,
        "output_period_kind": "month",
        "periods_per_call": 6,
        "title": "每月记忆",
    },
}
_ACTIVE_JOB_TASKS: dict[str, asyncio.Task] = {}
_RUN_LOCKS: dict[str, asyncio.Lock] = {}
logger = logging.getLogger(__name__)


def _shifted_datetime(ts: float) -> datetime:
    return datetime.fromtimestamp(float(ts)) - timedelta(hours=DAY_START_HOUR)


def memory_day_for_ts(ts: float) -> str:
    return _shifted_datetime(ts).strftime("%Y-%m-%d")


def _day_bounds(day_text: str) -> tuple[float, float]:
    start = datetime.strptime(day_text, "%Y-%m-%d") + timedelta(hours=DAY_START_HOUR)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def _legacy_capsule_day(row: dict) -> str:
    match = re.match(r"\s*(20\d{2}-\d{2}-\d{2})", str(row.get("content") or ""))
    if match:
        try:
            datetime.strptime(match.group(1), "%Y-%m-%d")
            return match.group(1)
        except ValueError:
            pass
    timestamp = float(row.get("created_at") or row.get("source_start_ts") or row.get("source_end_ts") or 0)
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")


async def migrate_legacy_daily_capsules() -> dict:
    """Register old progressive-compression daily rows as first-stage day capsules."""
    counts = {"main": 0, "chatroom": 0}
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, content, created_at, source_start_ts, source_end_ts "
            "FROM memories WHERE LOWER(type) IN ('daily','digest','seeky_digest','seeky_compressed') "
            "AND COALESCE(archive_state,'active')='active' "
            "AND COALESCE(compression_stage,0) IN (1,2) "
            "AND COALESCE(period_kind,'')=''"
        )
        main_rows = [dict(row) for row in await cur.fetchall()]
        for row in main_rows:
            start, end = _day_bounds(_legacy_capsule_day(row))
            cur = await db.execute(
                "UPDATE memories SET compression_stage=1, period_kind='day', "
                "period_start_ts=?, period_end_ts=? "
                "WHERE id=? AND COALESCE(period_kind,'')=''",
                (start, end, row["id"]),
            )
            counts["main"] += max(0, int(cur.rowcount or 0))

        cur = await db.execute(
            "SELECT id, content, created_at, source_start_ts, source_end_ts "
            "FROM chatroom_memories WHERE memory_kind='daily' "
            "AND COALESCE(archive_state,'active')='active' "
            "AND COALESCE(compression_stage,0) IN (1,2) "
            "AND COALESCE(period_kind,'')=''"
        )
        chatroom_rows = [dict(row) for row in await cur.fetchall()]
        for row in chatroom_rows:
            start, end = _day_bounds(_legacy_capsule_day(row))
            cur = await db.execute(
                "UPDATE chatroom_memories SET compression_stage=1, period_kind='day', "
                "period_start_ts=?, period_end_ts=? "
                "WHERE id=? AND COALESCE(period_kind,'')=''",
                (start, end, row["id"]),
            )
            counts["chatroom"] += max(0, int(cur.rowcount or 0))
        await db.commit()
    return counts


def _week_bounds(ts: float) -> tuple[str, float, float]:
    shifted = _shifted_datetime(ts)
    monday = shifted.date() - timedelta(days=shifted.weekday())
    start = datetime.combine(monday, datetime.min.time()) + timedelta(hours=DAY_START_HOUR)
    end = start + timedelta(days=7)
    label = f"{start.strftime('%Y-%m-%d')} ~ {(end - timedelta(days=1)).strftime('%Y-%m-%d')}"
    return label, start.timestamp(), end.timestamp()


def _month_bounds(ts: float) -> tuple[str, float, float]:
    shifted = _shifted_datetime(ts)
    start = datetime(shifted.year, shifted.month, 1, DAY_START_HOUR)
    if shifted.month == 12:
        end = datetime(shifted.year + 1, 1, 1, DAY_START_HOUR)
    else:
        end = datetime(shifted.year, shifted.month + 1, 1, DAY_START_HOUR)
    return start.strftime("%Y-%m"), start.timestamp(), end.timestamp()


def _current_week_start(now_ts: float) -> float:
    return _week_bounds(now_ts)[1]


def _current_month_start(now_ts: float) -> float:
    shifted = _shifted_datetime(now_ts)
    return datetime(shifted.year, shifted.month, 1, DAY_START_HOUR).timestamp()


def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


async def ensure_calendar_compression_schema() -> None:
    async with get_db() as db:
        for table in ("memories", "chatroom_memories"):
            for column, definition in (
                ("archive_state", "TEXT DEFAULT 'active'"),
                ("archived_at", "REAL"),
                ("period_kind", "TEXT DEFAULT ''"),
                ("period_start_ts", "REAL"),
                ("period_end_ts", "REAL"),
                ("compression_batch_id", "TEXT DEFAULT ''"),
                ("source_memory_ids", "TEXT"),
            ):
                try:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                except Exception:
                    pass
        await db.execute(
            "CREATE TABLE IF NOT EXISTS memory_compression_batches ("
            "id TEXT PRIMARY KEY, target TEXT NOT NULL, level TEXT NOT NULL, "
            "model_key TEXT NOT NULL, status TEXT NOT NULL, period_start_ts REAL, "
            "period_end_ts REAL, input_count INTEGER DEFAULT 0, output_count INTEGER DEFAULT 0, "
            "error TEXT DEFAULT '', created_at REAL NOT NULL, completed_at REAL)"
        )
        columns = {row[1] for row in await (await db.execute(
            "PRAGMA table_info(memory_compression_batches)"
        )).fetchall()}
        for column in ("reflection", "actor_name"):
            if column not in columns:
                await db.execute(f"ALTER TABLE memory_compression_batches ADD COLUMN {column} TEXT DEFAULT ''")
        if "durable_output_count" not in columns:
            await db.execute("ALTER TABLE memory_compression_batches ADD COLUMN durable_output_count INTEGER")
        await db.execute(
            "CREATE TABLE IF NOT EXISTS memory_compression_batch_inputs ("
            "batch_id TEXT NOT NULL, store TEXT NOT NULL, memory_id TEXT NOT NULL, "
            "PRIMARY KEY (batch_id, store, memory_id))"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS memory_compression_batch_outputs ("
            "batch_id TEXT NOT NULL, store TEXT NOT NULL, memory_id TEXT NOT NULL, "
            "PRIMARY KEY (batch_id, store, memory_id))"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_compression_inputs_memory "
            "ON memory_compression_batch_inputs(store, memory_id)"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS memory_compression_jobs ("
            "id TEXT PRIMARY KEY, target TEXT NOT NULL, level TEXT NOT NULL, "
            "model_key TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', "
            "progress_json TEXT NOT NULL DEFAULT '{}', result_json TEXT NOT NULL DEFAULT '{}', "
            "error TEXT DEFAULT '', created_at REAL NOT NULL, started_at REAL, "
            "updated_at REAL NOT NULL, completed_at REAL)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_compression_jobs_target "
            "ON memory_compression_jobs(target, level, created_at DESC)"
        )
        await db.commit()


def _row_event_ts(row: dict) -> float:
    return float(row.get("period_start_ts") or row.get("source_end_ts") or row.get("source_start_ts") or row.get("created_at") or 0)


def _period_for_row(level: str, row: dict) -> tuple[str, float, float]:
    ts = _row_event_ts(row)
    if level == "daily":
        label = memory_day_for_ts(ts)
        start, end = _day_bounds(label)
        return label, start, end
    if level == "weekly":
        return _week_bounds(ts)
    return _month_bounds(ts)


def _eligible_period(level: str, period_end: float, now_ts: float) -> bool:
    """Keep detail for 7/90/365 days after the source calendar period ends."""
    if level == "daily":
        return period_end <= now_ts - (7 * 86400)
    if level == "weekly":
        return (
            period_end <= _current_week_start(now_ts)
            and period_end <= now_ts - (90 * 86400)
        )
    return (
        period_end <= _current_month_start(now_ts)
        and period_end <= now_ts - (365 * 86400)
    )


async def _candidate_rows(target: str, level: str) -> list[dict]:
    cfg = LEVELS[level]
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if target == "main":
            params: list = [cfg["source_stage"]]
            period_sql = ""
            if cfg["source_period_kind"]:
                period_sql = "AND period_kind=? "
                params.append(cfg["source_period_kind"])
            cur = await db.execute(
                "SELECT id, content, type, created_at, source_conv, keywords, importance, "
                "source_start_ts, source_end_ts, source_msg_id, compression_stage, "
                "period_kind, period_start_ts, period_end_ts "
                "FROM memories WHERE LOWER(type) IN ('daily','digest','seeky_digest','seeky_compressed') "
                "AND COALESCE(archive_state,'active')='active' "
                "AND COALESCE(compression_stage,0)=? "
                f"{period_sql}"
                "ORDER BY COALESCE(period_start_ts, source_start_ts, created_at) ASC",
                tuple(params),
            )
        else:
            params = [cfg["source_stage"]]
            period_sql = ""
            if cfg["source_period_kind"]:
                period_sql = "AND period_kind=? "
                params.append(cfg["source_period_kind"])
            cur = await db.execute(
                "SELECT id, room_id, scope, content, keywords, importance, created_at, "
                "source_start_ts, source_end_ts, source_msg_id, memory_kind, compression_stage, "
                "period_kind, period_start_ts, period_end_ts "
                "FROM chatroom_memories WHERE memory_kind='daily' "
                "AND COALESCE(archive_state,'active')='active' "
                "AND COALESCE(compression_stage,0)=? "
                f"{period_sql}"
                "ORDER BY COALESCE(period_start_ts, source_start_ts, created_at) ASC",
                tuple(params),
            )
        return [dict(row) for row in await cur.fetchall()]


async def compression_preview(
    target: str,
    level: str,
    *,
    now_ts: float | None = None,
) -> dict:
    target = target if target in {"main", "chatroom"} else "main"
    if level not in LEVELS:
        raise ValueError("Unsupported compression level.")
    await ensure_calendar_compression_schema()
    now_ts = float(now_ts or time.time())
    blocked_periods: set[str] = set()
    if level == "weekly":
        for row in await _candidate_rows(target, "daily"):
            blocked_periods.add(_period_for_row("weekly", row)[0])
    elif level == "monthly":
        for row in await _candidate_rows(target, "weekly"):
            blocked_periods.add(_period_for_row("monthly", row)[0])
    grouped: dict[str, dict] = {}
    for row in await _candidate_rows(target, level):
        label, start, end = _period_for_row(level, row)
        if not _eligible_period(level, end, now_ts) or label in blocked_periods:
            continue
        period = grouped.setdefault(
            label,
            {
                "label": label,
                "period_start_ts": start,
                "period_end_ts": end,
                "rows": [],
            },
        )
        period["rows"].append(row)
    periods = []
    for period in sorted(grouped.values(), key=lambda item: item["period_start_ts"]):
        periods.append(
            {
                "label": period["label"],
                "period_start_ts": period["period_start_ts"],
                "period_end_ts": period["period_end_ts"],
                "memory_count": len(period["rows"]),
                "memory_ids": [row["id"] for row in period["rows"]],
            }
        )
    size = LEVELS[level]["periods_per_call"]
    return {
        "target": target,
        "level": level,
        "title": LEVELS[level]["title"],
        "memory_count": sum(period["memory_count"] for period in periods),
        "period_count": len(periods),
        "estimated_calls": (len(periods) + size - 1) // size if periods else 0,
        "periods": periods,
        "can_run": bool(periods),
    }


def _compression_actor_context(target: str) -> dict:
    context = {"name": "AI", "user_name": "用户", "persona": "", "user_persona": ""}
    try:
        wb = load_worldbook()
        context.update(user_name=wb.get("user_name") or "用户", user_persona=wb.get("user_persona") or "")
        if target == "main":
            context.update(name=wb.get("ai_name") or "AI", persona=wb.get("ai_persona") or "")
        else:
            from chatroom import load_chatroom_config, _read_connor_persona
            cfg = load_chatroom_config()
            context.update(name=cfg.get("connor_name") or "AI", persona=_read_connor_persona())
    except Exception:
        logger.warning("Compression persona unavailable; keeping factual compression", exc_info=True)
    return context


def _content_chars(contents) -> int:
    return sum(len("".join(content.split())) for content in contents)


def _compression_budget(periods: list[dict], rows_by_id: dict[str, dict]) -> dict:
    contents = [rows_by_id[mem_id]["content"] for period in periods for mem_id in period["memory_ids"]]
    return {"max_output_count": len(contents), "max_content_chars": _content_chars(contents)}


def _source_reference_map(periods: list[dict]) -> dict[str, str]:
    """Short references are local to one model call; real IDs stay in the application."""
    memory_ids = [mem_id for period in periods for mem_id in period["memory_ids"]]
    return {f"M{index}": mem_id for index, mem_id in enumerate(memory_ids, start=1)}


def _period_prompt(level: str, periods: list[dict], rows_by_id: dict[str, dict], actor_context: dict | None = None) -> str:
    references = {mem_id: ref for ref, mem_id in _source_reference_map(periods).items()}
    period_payload = []
    for period in periods:
        period_payload.append(
            {
                "period": period["label"],
                "start": datetime.fromtimestamp(period["period_start_ts"]).isoformat(timespec="minutes"),
                "end_exclusive": datetime.fromtimestamp(period["period_end_ts"]).isoformat(timespec="minutes"),
                "max_memories": min(5, len(period["memory_ids"])),
                "memories": [
                    {
                        "memory_id": references[mem_id],
                        "source_period": (
                            _week_bounds(_row_event_ts(row))[0]
                            if level == "monthly" else memory_day_for_ts(_row_event_ts(row))
                        ),
                        "content": row["content"],
                    }
                    for mem_id, row in ((mem_id, rows_by_id[mem_id]) for mem_id in period["memory_ids"])
                ],
            }
        )
    level_instruction = {
        "daily": "当前任务：日记忆压缩。输入是按日归组的原始记忆条目。"
                 "记住当天重要或有个人意义的经历，合并同一事件的经过，只留关键细节与感受。",
        "weekly": "当前任务：周记忆压缩。输入是已经整理过的日记忆。"
                  "概括这一周的主要经历、发展和变化，合并跨天的同一事件，不逐日复述；"
                  "只为影响理解的重要节点保留具体日期和细节。",
        "monthly": "当前任务：月记忆压缩。输入是已经整理过的周记忆。"
                   "留下这个月的重大经历、关系变化和阶段结果，进一步淡化过程细节，不逐周罗列；"
                   "跨月周可能归入本组，以原文的实际事件日期为准，不把邻月事件改写成本月发生。",
    }[level]
    return (
        "你是下面角色配置中的 AI 伴侣，正在压缩自己与用户相处的旧记忆。"
        "目标是用更少的文字留下值得长期记住的经历与感受，不是润色或扩写原文。\n"
        f"你的身份、人设与用户信息（用于记忆整理和随口感想）：{json.dumps(actor_context or {}, ensure_ascii=False)}\n"
        f"{level_instruction}\n"
        "每组 period 是本次整理的日期、周起止日期或月份；start 到 end_exclusive 为当地时间范围，"
        "右端不含，记忆日以凌晨 5 点分界。source_period 是输入条目的来源周期。"
        "逐组整理并原样返回 period，不跨组合并，不把未提供的经历补成完整日历。\n"
        "先取舍再概括：合并重复与同一事件，省略流水账、重复对话和不影响理解的过程。"
        "重要事实的含义不能改变，但不必保留每个事实细节；关键承诺、健康安全信息和重大变化不能省掉。\n"
        "memories 与 durable_facts 的正文统一使用第三人称姓名叙述：记忆所属角色用配置中的 name，"
        "用户用 user_name，其他人物沿用原文中的姓名；不使用“我、我们、你、你们、他、她、他们、她们”等人称代词代替人物姓名。"
        "每条须独立写清人物，不能依赖其他条目补足指代；原文引语若含人称代词，改为姓名明确的转述，不冒充逐字引用。"
        "将输入中有依据、对角色有意义的情绪自然融入事件，"
        "不逐件补写气氛、情绪分析或意义升华。人设决定视角，不补造事实、对话或当时的感受；"
        "人物、时间、关键经过和结果须准确，转述不改成亲历，正文不输出数据库 ID。\n"
        "每条 memories 和 durable_facts 必须用 source_memory_ids 列出实际支撑它的输入 memory_id；"
        "memory_id 是本次调用的临时短编号（M1、M2 等），原样引用，不自行编造、改写或返回数据库编号。"
        "只引用提供的短编号，memories 不跨 period 引用，不把整个批次都当成每条记忆的来源。\n"
        "每条围绕一个主题，尽量用简短的 1-3 句写清。每组通常 1-3 条，重要主题确实较多时最多 max_memories 条；"
        "不凑条数，无保留价值可返回空数组。原文已简洁时不强删重要信息，其余应明显缩短。\n"
        "durable_facts 只提取今后仍需单独记住的稳定偏好、健康禁忌或长期承诺，保持简短准确，"
        "不要把普通经历升级为长期事实，不与 memories 重复保存同一事实，本批内去重，无则留空。\n"
        f"本批输出上限：{json.dumps(_compression_budget(periods, rows_by_id), ensure_ascii=False)}。"
        "所有 memories 与 durable_facts 合计不得超过输入总条数和正文总字数（不计空白）；"
        "上限不是目标，长期事实也占预算，不靠拆条或长段落绕过压缩。\n"
        "reflection 可选：回想其中一件旧事，依你的性格随口对用户说一句，最多 120 字，"
        "吐槽、笑意、心疼或无语都可以，不必正面或煽情。新生的感想放在这里，不扩写进旧记忆；"
        "不写回顾文章、不复述经历、不输出动作协议，不把往事说成今天刚发生。"
        "没话说可省略或留空；感想不计入记忆预算，先完成记忆整理。\n"
        "严格输出 JSON："
        '{"periods":[{"period":"输入中的周期标签","memories":['
        '{"content":"简洁且带有个人视角的记忆","keywords":["关键词"],"importance":0.5,"source_memory_ids":["M1"]}]}],'
        '"durable_facts":[{"content":"长期事实","keywords":["关键词"],"importance":0.85,"source_memory_ids":["M2"]}],'
        '"reflection":"可选，想起这件事时顺口想说的话"}\n'
        f"输入：{json.dumps(period_payload, ensure_ascii=False)}"
    )


def _parse_json_response(raw: str) -> dict | None:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except Exception:
            return None
    return parsed if isinstance(parsed, dict) else None


async def _call_compression_model(model_key: str, prompt: str) -> dict | None:
    raw = await simple_ai_call([{"role": "user", "content": prompt}], model_key)
    return _parse_json_response(raw or "")


async def _embedding_for_content(content: str):
    from memory import get_embedding

    return await get_embedding(content)


def _clean_keywords(value) -> list[str]:
    return [str(item).strip() for item in _json_list(value) if str(item).strip()][:8]


def _optional_reflection(value) -> str:
    # Optional prose never participates in memory validation or model retries.
    return " ".join(value.split())[:200] if isinstance(value, str) else ""


def _compression_record(raw) -> dict:
    row = dict(raw)
    start = memory_day_for_ts(row["period_start_ts"])
    end = memory_day_for_ts(row["period_end_ts"] - 1)
    if row["level"] == "monthly":
        start, end = start[:7], end[:7]
    inputs, outputs = row["input_count"], row["output_count"]
    durable = row.get("durable_output_count")
    return {
        "id": row["id"], "target": row["target"], "level": row["level"],
        "period_label": start if start == end else f"{start}～{end}",
        "model_key": row["model_key"], "completed_at": row["completed_at"],
        "input_count": inputs, "output_count": outputs, "delta": outputs - inputs,
        "reduction_percent": round((inputs - outputs) * 100 / inputs, 1) if inputs else None,
        "durable_output_count": durable,
        "memory_output_count": outputs - durable if durable is not None else None,
        "reflection": _optional_reflection(row.get("reflection")),
        "actor_name": row.get("actor_name") or "",
    }


async def _read_completed_batches(*, since: float = 0, target: str | None = None, limit: int = 80, offset: int = 0):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        exists = await (await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_compression_batches'"
        )).fetchone()
        if not exists:
            return []
        return await (await db.execute(
            "SELECT * FROM memory_compression_batches WHERE status='completed' AND completed_at>=? "
            "AND (? IS NULL OR target=?) ORDER BY completed_at DESC,id DESC LIMIT ? OFFSET ?",
            (since, target, target, limit, offset),
        )).fetchall()


async def list_compression_history(target: str, *, limit: int = 20, offset: int = 0) -> dict:
    target = target if target in {"main", "chatroom"} else "main"
    limit, offset = max(1, min(limit, 100)), max(0, offset)
    rows = await _read_completed_batches(target=target, limit=limit + 1, offset=offset)
    return {"items": [_compression_record(row) for row in rows[:limit]], "has_more": len(rows) > limit}


async def list_compression_events(*, since: float, limit: int = 80) -> list[dict]:
    """Completed batches are the durable events; reading them never creates duplicates."""
    rows = await _read_completed_batches(since=since, limit=limit)
    events = []
    for raw in rows:
        row = _compression_record(raw)
        period = row["period_label"]
        name = row.get("actor_name") or _compression_actor_context(row["target"])["name"]
        label = {"daily": "记忆", "weekly": "周记忆", "monthly": "月记忆"}[row["level"]]
        reflection = _optional_reflection(row.get("reflection"))
        summary = f"整理了 {row['input_count']} 条记忆，生成 {row['output_count']} 条。"
        if row["durable_output_count"] is not None:
            summary += f"其中普通记忆 {row['memory_output_count']} 条，长期事实 {row['durable_output_count']} 条。"
        delta = row["delta"]
        summary += f"净增加 {delta} 条。" if delta > 0 else f"减少 {-delta} 条。" if delta < 0 else "条数不变。"
        events.append({
            "timestamp": row["completed_at"], "kind": "memory_compression",
            "actor": name, "author": "aion" if row["target"] == "main" else "connor",
            "title": f"{name}整理了 {period} 的{label}",
            "detail": f"{reflection}\n\n{summary}" if reflection else summary,
            "reflection": reflection, "source_id": row["id"], "attachments": [],
        })
    return events


async def _notify_job_changed(target: str):
    try:
        from ws import manager
        await asyncio.wait_for(manager.broadcast({
            'type': 'memory_compression_job', 'data': {'target': target}
        }), timeout=2)
    except Exception:
        logger.warning('Compression job notification unavailable', exc_info=True)


async def _notify_compression_completed(batch_id: str):
    try:
        from ws import manager
        await asyncio.wait_for(manager.broadcast({
            "type": "family_event", "data": {"kind": "memory_compression", "batch_id": batch_id}
        }), timeout=2)
    except Exception:
        # The event is already committed with the memories; a refresh can load it.
        logger.warning("Compression event saved; live refresh unavailable", exc_info=True)


def _normalize_outputs(parsed: dict, expected_periods: set[str]) -> tuple[dict[str, list[dict]], list[dict]] | None:
    normalized: dict[str, list[dict]] = {label: [] for label in expected_periods}
    seen_periods = set()
    for period in parsed.get("periods") or []:
        if not isinstance(period, dict):
            continue
        label = str(period.get("period") or "").strip()
        if label not in expected_periods or label in seen_periods:
            continue
        seen_periods.add(label)
        for item in (period.get("memories") or [])[:5]:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            try:
                importance = max(0.0, min(0.7, float(item.get("importance", 0.4))))
            except Exception:
                importance = 0.4
            normalized[label].append(
                {
                    "content": content,
                    "keywords": _clean_keywords(item.get("keywords")),
                    "importance": importance,
                    "source_memory_ids": item.get("source_memory_ids"),
                }
            )
    if seen_periods != expected_periods:
        return None
    durable = []
    for item in (parsed.get("durable_facts") or [])[:8]:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        try:
            importance = max(0.8, min(1.0, float(item.get("importance", 0.85))))
        except Exception:
            importance = 0.85
        durable.append(
            {
                "content": content,
                "keywords": _clean_keywords(item.get("keywords")),
                "importance": importance,
                "source_memory_ids": item.get("source_memory_ids"),
            }
        )
    return normalized, durable


def _chunks(items: list[dict], size: int) -> Iterable[list[dict]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


async def resolve_source_memories(target: str, memory_id: str, *, max_nodes: int = 500) -> list[dict]:
    """Return original records; legacy batch ancestry is explicitly approximate."""
    if target not in {"main", "chatroom"}:
        raise ValueError("Unsupported memory store")
    pending = [(str(memory_id), True)]
    visited = set()
    originals = []
    table = "memories" if target == "main" else "chatroom_memories"
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        while pending and len(visited) < max_nodes:
            current_id, exact = pending.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)
            cur = await db.execute(
                f"SELECT * FROM {table} WHERE id=?" + (" AND scope='connor'" if target == "chatroom" else ""),
                (current_id,),
            )
            row = await cur.fetchone()
            if not row:
                continue
            mem = dict(row)
            if _json_list(mem.get("source_msg_id")):
                originals.append({**mem, "lineage_exact": exact})
                continue
            parents = mem.get("source_memory_ids")
            if parents is not None:
                pending.extend((str(parent), exact) for parent in _json_list(parents))
                continue
            batch_id = str(mem.get("compression_batch_id") or "").strip()
            if not batch_id:
                originals.append({**mem, "lineage_exact": exact})
                continue
            cur = await db.execute(
                "SELECT store, memory_id FROM memory_compression_batch_inputs "
                "WHERE batch_id=? AND store=? ORDER BY memory_id",
                (batch_id, target),
            )
            pending.extend((str(r["memory_id"]), False) for r in await cur.fetchall())
    return originals


async def resolve_source_message_ids(target: str, memory_id: str, *, max_nodes: int = 500) -> list[str]:
    originals = await resolve_source_memories(target, memory_id, max_nodes=max_nodes)
    # Keep message namespaces when following old unprefixed private IDs.
    ids = []
    for mem in originals:
        for value in _json_list(mem.get("source_msg_id")):
            source_id = str(value).strip()
            if not source_id:
                continue
            if ":" not in source_id:
                prefix = "chatroom" if target == "chatroom" or str(mem.get("source_conv") or "").startswith("chatroom:") else "private"
                source_id = f"{prefix}:{source_id}"
            ids.append(source_id)
    return list(dict.fromkeys(ids))


async def _insert_output(
    db,
    *,
    target: str,
    item: dict,
    memory_id: str,
    batch_id: str,
    stage: int,
    period_kind: str,
    period_start_ts: float,
    period_end_ts: float,
    embedding,
    durable: bool = False,
    template_row: dict | None = None,
    allowed_source_ids: list[str] | None = None,
) -> None:
    from memory import _pack_embedding

    packed = _pack_embedding(embedding) if embedding else None
    parents = item.get("source_memory_ids")
    if parents is not None:
        if not isinstance(parents, list) or not parents or any(
            not isinstance(parent, str) or parent not in (allowed_source_ids or []) for parent in parents
        ):
            raise ValueError("Compression output has invalid source_memory_ids")
        parents = list(dict.fromkeys(parents))
    now = time.time()
    if target == "main":
        await db.execute(
            "INSERT INTO memories ("
            "id, content, type, created_at, source_conv, embedding, keywords, importance, "
            "source_start_ts, source_end_ts, unresolved, source_msg_id, compression_stage, "
            "evidence_summary, evidence_detail_level, archive_state, period_kind, "
            "period_start_ts, period_end_ts, compression_batch_id, source_memory_ids"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                memory_id,
                item["content"],
                "important" if durable else "daily",
                period_start_ts or now,
                "calendar_memory_compression",
                packed,
                json.dumps(item["keywords"], ensure_ascii=False),
                item["importance"],
                period_start_ts,
                period_end_ts,
                0,
                json.dumps([], ensure_ascii=False),
                0 if durable else stage,
                f"由{period_kind}压缩批次整理，可沿批次查看冷档案来源。",
                "summary",
                "active",
                "fact" if durable else period_kind,
                period_start_ts,
                period_end_ts,
                batch_id,
                json.dumps(parents) if parents is not None else None,
            ),
        )
    else:
        template_row = template_row or {}
        await db.execute(
            "INSERT INTO chatroom_memories ("
            "id, room_id, scope, content, keywords, importance, embedding, source_start_ts, "
            "source_end_ts, created_at, unresolved, source_msg_id, memory_kind, compression_stage, "
            "evidence_summary, evidence_detail_level, archive_state, period_kind, "
            "period_start_ts, period_end_ts, compression_batch_id, source_memory_ids"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                memory_id,
                template_row.get("room_id") or "connor_unified",
                template_row.get("scope") or "connor",
                item["content"],
                ",".join(item["keywords"]),
                item["importance"],
                packed,
                period_start_ts,
                period_end_ts,
                period_start_ts or now,
                0,
                json.dumps([], ensure_ascii=False),
                "long_term" if durable else "daily",
                0 if durable else stage,
                f"由{period_kind}压缩批次整理，可沿批次查看冷档案来源。",
                "summary",
                "active",
                "fact" if durable else period_kind,
                period_start_ts,
                period_end_ts,
                batch_id,
                json.dumps(parents) if parents is not None else None,
            ),
        )


async def run_calendar_compression(
    target: str,
    level: str,
    model_key: str,
    *,
    now_ts: float | None = None,
    progress_callback=None,
) -> dict:
    target = target if target in {"main", "chatroom"} else "main"
    # Manual requests and scheduled jobs must re-check candidates under one lock.
    lock = _RUN_LOCKS.setdefault(target, asyncio.Lock())
    async with lock:
        return await _run_calendar_compression(
            target, level, model_key, now_ts=now_ts, progress_callback=progress_callback
        )


async def _run_calendar_compression(
    target: str,
    level: str,
    model_key: str,
    *,
    now_ts: float | None = None,
    progress_callback=None,
) -> dict:
    target = target if target in {"main", "chatroom"} else "main"
    if level not in LEVELS:
        return {"ok": False, "reason": "invalid_level", "message": "不支持的压缩层级。"}
    if model_key not in MODELS or is_model_deprecated(model_key):
        return {"ok": False, "reason": "invalid_model", "message": "请选择有效的压缩模型。"}
    now_ts = float(now_ts or time.time())
    preview = await compression_preview(target, level, now_ts=now_ts)
    if not preview["can_run"]:
        return {
            "ok": False,
            "reason": "no_candidates",
            "message": "目前没有可用于本次压缩的记忆，未调用模型。",
            "preview": preview,
        }
    if progress_callback:
        await progress_callback({
            "completed_calls": 0,
            "total_calls": preview["estimated_calls"],
            "processed_inputs": 0,
            "total_inputs": preview["memory_count"],
            "created_outputs": 0,
            "message": "候选已锁定，准备调用模型",
        })

    rows = await _candidate_rows(target, level)
    by_id = {row["id"]: row for row in rows}
    cfg = LEVELS[level]
    total_inputs = total_outputs = total_calls = 0
    batch_ids = []
    actor_context = _compression_actor_context(target)
    for period_chunk in _chunks(preview["periods"], cfg["periods_per_call"]):
        source_references = _source_reference_map(period_chunk)
        prompt = _period_prompt(level, period_chunk, by_id, actor_context)
        parsed = await _call_compression_model(model_key, prompt)
        normalized = _normalize_outputs(parsed or {}, {period["label"] for period in period_chunk})
        if normalized is None:
            return {
                "ok": False,
                "reason": "invalid_model_output",
                "message": "模型没有返回可用的周期记忆，旧记忆保持活跃。",
                "input_count": total_inputs,
                "output_count": total_outputs,
            }
        period_outputs, durable_facts = normalized
        budget = _compression_budget(period_chunk, by_id)
        output_contents = [item["content"] for items in period_outputs.values() for item in items]
        output_contents.extend(item["content"] for item in durable_facts)
        if (
            len(output_contents) > budget["max_output_count"]
            or _content_chars(output_contents) > budget["max_content_chars"]
            or any(len(period_outputs[period["label"]]) > len(period["memory_ids"]) for period in period_chunk)
        ):
            return {
                "ok": False,
                "reason": "compression_expanded",
                "message": "模型整理后的记忆条数或正文总字数增加，本批未写入，原记忆保持活跃；已完成批次见整理存档。",
                "input_count": total_inputs,
                "output_count": total_outputs,
            }
        source_groups = [(item, period['memory_ids']) for period in period_chunk
                         for item in period_outputs[period['label']]]
        source_groups.extend((item, [mid for p in period_chunk for mid in p['memory_ids']]) for item in durable_facts)
        for item, allowed in source_groups:
            parents = item.get('source_memory_ids')
            # A single input is unambiguous; otherwise the model must identify its sources.
            if parents is None and len(allowed) == 1:
                item['source_memory_ids'] = list(allowed)
                continue
            if not isinstance(parents, list) or not parents or any(
                not isinstance(parent, str) or source_references.get(parent) not in allowed for parent in parents
            ):
                return {'ok': False, 'reason': 'invalid_source_lineage',
                        'message': '本批来源关联缺失或无效，未写入；原记忆保持活跃。',
                        'input_count': total_inputs, 'output_count': total_outputs}
            item['source_memory_ids'] = list(dict.fromkeys(source_references[parent] for parent in parents))
        prepared = []
        for period in period_chunk:
            for item in period_outputs.get(period["label"], []):
                prepared.append(
                    {
                        "item": item,
                        "period": period,
                        "durable": False,
                        "embedding": await _embedding_for_content(item["content"]),
                    }
                )
        overall_start = min(period["period_start_ts"] for period in period_chunk)
        overall_end = max(period["period_end_ts"] for period in period_chunk)
        for item in durable_facts:
            prepared.append(
                {
                    "item": item,
                    "period": {
                        "period_start_ts": overall_start,
                        "period_end_ts": overall_end,
                    },
                    "durable": True,
                    "embedding": await _embedding_for_content(item["content"]),
                }
            )

        batch_id = f"mcb_{time.time_ns()}"
        input_ids = [mem_id for period in period_chunk for mem_id in period["memory_ids"]]
        template_row = by_id[input_ids[0]] if input_ids else {}
        async with get_db() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute(
                    "INSERT INTO memory_compression_batches ("
                    "id, target, level, model_key, status, period_start_ts, period_end_ts, "
                    "input_count, output_count, created_at, reflection, actor_name, durable_output_count"
                    ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        batch_id,
                        target,
                        level,
                        model_key,
                        "applying",
                        overall_start,
                        overall_end,
                        len(input_ids),
                        len(prepared),
                        time.time(),
                        _optional_reflection((parsed or {}).get("reflection")),
                        actor_context["name"],
                        len(durable_facts),
                    ),
                )
                for mem_id in input_ids:
                    await db.execute(
                        "INSERT INTO memory_compression_batch_inputs (batch_id, store, memory_id) "
                        "VALUES (?,?,?)",
                        (batch_id, target, mem_id),
                    )
                output_ids = []
                for index, output in enumerate(prepared):
                    mem_id = f"memc_{time.time_ns()}_{index}"
                    await _insert_output(
                        db,
                        target=target,
                        item=output["item"],
                        memory_id=mem_id,
                        batch_id=batch_id,
                        stage=cfg["output_stage"],
                        period_kind=cfg["output_period_kind"],
                        period_start_ts=output["period"]["period_start_ts"],
                        period_end_ts=output["period"]["period_end_ts"],
                        embedding=output["embedding"],
                        durable=output["durable"],
                        template_row=template_row,
                        allowed_source_ids=input_ids if output["durable"] else output["period"]["memory_ids"],
                    )
                    await db.execute(
                        "INSERT INTO memory_compression_batch_outputs (batch_id, store, memory_id) "
                        "VALUES (?,?,?)",
                        (batch_id, target, mem_id),
                    )
                    output_ids.append(mem_id)
                placeholders = ",".join("?" for _ in input_ids)
                table = "memories" if target == "main" else "chatroom_memories"
                await db.execute(
                    f"UPDATE {table} SET archive_state='cold', archived_at=?, embedding=NULL "
                    f"WHERE id IN ({placeholders}) AND COALESCE(archive_state,'active')='active'",
                    (time.time(), *input_ids),
                )
                await db.execute(
                    "UPDATE memory_compression_batches SET status='completed', completed_at=? WHERE id=?",
                    (time.time(), batch_id),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        total_inputs += len(input_ids)
        total_outputs += len(prepared)
        total_calls += 1
        batch_ids.append(batch_id)
        await _notify_compression_completed(batch_id)
        if progress_callback:
            await progress_callback({
                "completed_calls": total_calls,
                "total_calls": preview["estimated_calls"],
                "processed_inputs": total_inputs,
                "total_inputs": preview["memory_count"],
                "created_outputs": total_outputs,
                "message": f"已完成 {total_calls}/{preview['estimated_calls']} 个模型批次",
            })

    return {
        "ok": True,
        "message": f"压缩完成：处理 {total_inputs} 条旧记忆，生成 {total_outputs} 条新记忆。",
        "target": target,
        "level": level,
        "model_key": model_key,
        "input_count": total_inputs,
        "output_count": total_outputs,
        "model_calls": total_calls,
        "batch_ids": batch_ids,
    }


def _decode_json_object(value) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _serialize_job(row) -> dict | None:
    if not row:
        return None
    data = dict(row)
    return {
        "id": data["id"],
        "target": data["target"],
        "level": data["level"],
        "model_key": data["model_key"],
        "status": data["status"],
        "progress": _decode_json_object(data.get("progress_json")),
        "result": _decode_json_object(data.get("result_json")),
        "error": data.get("error") or "",
        "created_at": data.get("created_at"),
        "started_at": data.get("started_at"),
        "updated_at": data.get("updated_at"),
        "completed_at": data.get("completed_at"),
    }


async def _update_job_progress(job_id: str, progress: dict) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE memory_compression_jobs SET progress_json=?, updated_at=? "
            "WHERE id=? AND status='running'",
            (json.dumps(progress, ensure_ascii=False), time.time(), job_id),
        )
        await db.commit()


async def _execute_compression_job(
    job_id: str,
    target: str,
    level: str,
    model_key: str,
) -> None:
    now = time.time()
    async with get_db() as db:
        await db.execute(
            "UPDATE memory_compression_jobs SET status='running', started_at=?, updated_at=?, "
            "progress_json=? WHERE id=? AND status='queued'",
            (
                now,
                now,
                json.dumps({"message": "正在准备压缩任务"}, ensure_ascii=False),
                job_id,
            ),
        )
        await db.commit()
    try:
        result = await run_calendar_compression(
            target,
            level,
            model_key,
            progress_callback=lambda progress: _update_job_progress(job_id, progress),
        )
        finished = time.time()
        completed = result.get("ok") or result.get("reason") == "no_candidates"
        status = "completed" if completed else "failed"
        error = "" if completed else str(result.get("message") or "压缩失败")
        async with get_db() as db:
            await db.execute(
                "UPDATE memory_compression_jobs SET status=?, result_json=?, error=?, "
                "updated_at=?, completed_at=? WHERE id=?",
                (
                    status,
                    json.dumps(result, ensure_ascii=False),
                    error,
                    finished,
                    finished,
                    job_id,
                ),
            )
            await db.commit()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        finished = time.time()
        async with get_db() as db:
            await db.execute(
                "UPDATE memory_compression_jobs SET status='failed', error=?, updated_at=?, "
                "completed_at=? WHERE id=?",
                (str(exc), finished, finished, job_id),
            )
            await db.commit()


async def create_calendar_compression_job(
    target: str,
    level: str,
    model_key: str,
) -> dict:
    target = target if target in {"main", "chatroom"} else "main"
    if level not in LEVELS:
        return {"ok": False, "reason": "invalid_level", "message": "不支持的压缩层级。"}
    if model_key not in MODELS or is_model_deprecated(model_key):
        return {"ok": False, "reason": "invalid_model", "message": "请选择有效的压缩模型。"}
    preview = await compression_preview(target, level)
    if not preview["can_run"]:
        return {
            "ok": False,
            "reason": "no_candidates",
            "message": "目前没有可用于本次压缩的记忆，未创建任务，也未调用模型。",
            "preview": preview,
        }
    await ensure_calendar_compression_schema()
    job_id = f"mcj_{time.time_ns()}"
    now = time.time()
    initial_progress = {
        "completed_calls": 0,
        "total_calls": preview["estimated_calls"],
        "processed_inputs": 0,
        "total_inputs": preview["memory_count"],
        "created_outputs": 0,
        "message": "任务已排队",
    }
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT * FROM memory_compression_jobs "
            "WHERE target=? AND status IN ('queued','running') "
            "ORDER BY created_at DESC LIMIT 1",
            (target,),
        )
        existing = await cur.fetchone()
        if existing:
            await db.rollback()
            return {
                "ok": False,
                "reason": "already_running",
                "message": "这个记忆库已有压缩任务正在进行。",
                "job": _serialize_job(existing),
            }
        await db.execute(
            "INSERT INTO memory_compression_jobs ("
            "id, target, level, model_key, status, progress_json, result_json, "
            "created_at, updated_at"
            ") VALUES (?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                target,
                level,
                model_key,
                "queued",
                json.dumps(initial_progress, ensure_ascii=False),
                "{}",
                now,
                now,
            ),
        )
        await db.commit()
    task = asyncio.create_task(_execute_compression_job(job_id, target, level, model_key))
    _ACTIVE_JOB_TASKS[job_id] = task
    def done(_task):
        _ACTIVE_JOB_TASKS.pop(job_id, None)
        asyncio.create_task(_notify_job_changed(target))
    task.add_done_callback(done)
    await _notify_job_changed(target)
    return {
        "ok": True,
        "message": "压缩任务已开始，可离开页面后再回来查看。",
        "job": {
            "id": job_id,
            "target": target,
            "level": level,
            "model_key": model_key,
            "status": "queued",
            "progress": initial_progress,
            "result": {},
            "error": "",
            "created_at": now,
            "updated_at": now,
        },
    }


async def get_calendar_compression_job(job_id: str) -> dict | None:
    await ensure_calendar_compression_schema()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM memory_compression_jobs WHERE id=?",
            (job_id,),
        )
        row = await cur.fetchone()
    job = _serialize_job(row)
    if job and job["status"] in {"queued", "running"} and job_id not in _ACTIVE_JOB_TASKS:
        finished = time.time()
        async with get_db() as db:
            await db.execute(
                "UPDATE memory_compression_jobs SET status='failed', error=?, "
                "updated_at=?, completed_at=? WHERE id=? AND status IN ('queued','running')",
                ("服务曾重启或任务已中断，请重新发起压缩。", finished, finished, job_id),
            )
            await db.commit()
        return await get_calendar_compression_job(job_id)
    return job


async def list_latest_calendar_compression_jobs(target: str) -> dict[str, dict]:
    await ensure_calendar_compression_schema()
    target = target if target in {"main", "chatroom"} else "main"
    result = {}
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        for level in LEVELS:
            cur = await db.execute(
                "SELECT * FROM memory_compression_jobs WHERE target=? AND level=? "
                "ORDER BY created_at DESC LIMIT 1",
                (target, level),
            )
            row = await cur.fetchone()
            if row:
                result[level] = _serialize_job(row)
    for level, job in list(result.items()):
        if job["status"] in {"queued", "running"}:
            result[level] = await get_calendar_compression_job(job["id"])
    return result
