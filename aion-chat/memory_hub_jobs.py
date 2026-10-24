"""
Memory Hub 后台任务：交接卡和每晚做梦。两件事都必须用该 AI 自己的模型（Hub 红线 #12）：
模型取相遇卡里的「TA 自己的模型」；没设、线路不存在或已停用就跳过，不换成别的模型。

- 交接卡：某位 AI 的私聊安静满 30 分钟、这段还没写过卡 → 用 TA 的模型读这段对话的原话，
  写一张给下一段对话的自己看的交接卡 → capture(action="handoff")。
- 做梦：每晚北京时间 3:30 以后，对每位接了 Hub 的 AI 取做梦材料 → 用 TA 的模型做梦 →
  dream(action="write", kind="dream")。Hub 同一天只收一个。

状态：交接卡记在 outbox 库的 activity 表（handoff_done_ts）；做梦记在 data/memory_hub_jobs.json。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import memory_hub_bridge as bridge
from config import DATA_DIR, MODELS, is_model_deprecated, load_worldbook

log = logging.getLogger("memory_hub_jobs")

JOBS_PATH = DATA_DIR / "memory_hub_jobs.json"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
HANDOFF_QUIET_SECONDS = 30 * 60
HANDOFF_GIVE_UP_SECONDS = 6 * 3600  # 一直写不成功的旧对话就不再补写
HANDOFF_RETRY_SECONDS = 30 * 60
HANDOFF_MAX_TURNS = 12
DREAM_AFTER = (3, 30)
DREAM_MAX_ATTEMPTS = 3
DREAM_RETRY_SECONDS = 3600
GENERATION_TIMEOUT_SECONDS = 300

_status: dict[str, Any] = {"handoff": {}, "dream": {}}


# ── 模型与人设 ──

def own_model(actor: str) -> str:
    """该 AI 自己的模型键；不可用时返回空串（调用方必须跳过）。"""
    try:
        import actors
        key = str((actors.get_actor(actor) or {}).get("model") or "").strip()
    except Exception:
        return ""
    if not key or key not in MODELS or is_model_deprecated(key):
        return ""
    from config import codex_forbidden
    if codex_forbidden(actor, key):  # 以前存进去的 Codex：小克 / 座位不用它，当作没设
        return ""
    return key


def background_model(actor: str, usual: str = "") -> str:
    """后台活动用的模型。名片里选了「后台用的线路」、而且和平时是同一个模型时用它（只是换一家中转站，
    比如聊天走按次、后台走按量）；否则照旧用平时的（usual，不传就是 TA 自己的模型）。"""
    from config import codex_forbidden, same_underlying_model
    base = usual or own_model(actor)
    if codex_forbidden(actor, base):
        base = ""  # 小克 / 座位：没有合法线路就是没有，调用方会提示去接线路，不回到 Codex
    try:
        import actors
        chosen = str((actors.get_actor(actor) or {}).get("background_model") or "").strip()
    except Exception:
        return base
    if (not chosen or not base or chosen not in MODELS or is_model_deprecated(chosen)
            or codex_forbidden(actor, chosen)):
        return base
    if same_underlying_model(chosen, base):
        return chosen
    log.warning("[后台线路] %s 的后台线路和平时不是同一个模型，这次照旧用平时的", actor)
    return base


def pin_background_model(actor: str) -> tuple[str, dict | None]:
    """选定这次后台任务用的模型，并当场固定它的线路（等 Hub、等材料的那几秒里线路被改也不认）。"""
    from config import model_snapshot
    key = background_model(actor)
    return key, (model_snapshot(key) if key else None)


def still_own_model(actor: str, key: str) -> bool:
    """生成前再核一次：这个模型现在仍然是这位 AI 自己的（平时的或同模型的后台线路）。"""
    return bool(key) and background_model(actor) == key


def model_label(key: str) -> str:
    return str((MODELS.get(key) or {}).get("model") or key)[:60]


def persona_for(actor: str) -> tuple[str, str]:
    """(名字, 人设正文)。"""
    import actors
    name = actors.display_name(actor)
    if actor == "aion":
        return name, str(load_worldbook().get("ai_persona") or "")
    if actor == "connor":
        from chatroom import load_chatroom_config
        return name, str(load_chatroom_config().get("connor_persona") or "")
    from persona_evolution import _compile_ai_persona_sections
    return name, _compile_ai_persona_sections(actors.persona_sections(actor))


async def complete(model_key: str, messages: list[dict], *, snapshot: dict | None = None) -> str:
    """用指定模型生成一段完整文本；模型报错时抛异常。

    snapshot：调用方早先固定下来的线路（config.model_snapshot）。不传就以此刻为准。
    真正调用前核对线路没被删、没被改、也不会被悄悄换成默认模型，否则抛 config.ModelChanged。
    """
    from ai_providers import CLI_STATUS_PREFIX, stream_ai
    from config import ensure_model_unchanged, model_snapshot

    pinned = snapshot if snapshot is not None else model_snapshot(model_key)
    meta: dict[str, Any] = {}
    parts: list[str] = []

    async def run() -> None:
        ensure_model_unchanged(model_key, pinned)  # 和下面真正发起调用之间没有 await
        async for chunk in stream_ai(messages, model_key, meta, include_device_context=False):
            if not isinstance(chunk, str) or chunk.startswith(CLI_STATUS_PREFIX):
                continue
            if meta.get("provider_error"):
                raise RuntimeError(meta["provider_error"])
            parts.append(chunk)

    await asyncio.wait_for(run(), timeout=GENERATION_TIMEOUT_SECONDS)
    if meta.get("provider_error"):
        raise RuntimeError(meta["provider_error"])
    from context_builder import strip_tool_commands
    return strip_tool_commands("".join(parts)).strip()


def _persona_messages(actor: str) -> list[dict]:
    name, persona = persona_for(actor)
    text = f"[你的角色设定]\n你是{name}。" + (f"\n\n{persona}" if persona.strip() else "")
    return [{"role": "user", "content": text}, {"role": "assistant", "content": "收到。"}]


# ── 交接卡 ──

def _session_turns(conn: sqlite3.Connection, actor: str, until: float) -> list[sqlite3.Row]:
    """以 until（这段对话最后一轮的时间）为终点往前的一段私聊（相邻两轮间隔不到 30 分钟算同一段），
    最多取最后 12 轮。有了终点，写旧一段的卡时不会读到之后才开始的新一段。"""
    rows = conn.execute(
        "SELECT user_message, ai_response, created_at FROM recent_turns WHERE actor=? AND created_at<=? ORDER BY id DESC",
        (actor, until),
    ).fetchall()
    session: list[sqlite3.Row] = []
    for row in rows:
        if session and session[-1]["created_at"] - row["created_at"] >= bridge.NEW_SESSION_GAP_SECONDS:
            break
        session.append(row)
    return list(reversed(session[:HANDOFF_MAX_TURNS]))


def _handoff_prompt(actor: str, turns: list[sqlite3.Row]) -> list[dict]:
    user_name = (load_worldbook().get("user_name") or "").strip() or "她"
    name, _ = persona_for(actor)
    lines = []
    for row in turns:
        lines.append(f"{user_name}：{row['user_message']}")
        lines.append(f"你：{row['ai_response']}")
    transcript = "\n".join(lines)[-8000:]
    return _persona_messages(actor) + [{"role": "user", "content": (
        f"下面是你（{name}）和{user_name}刚结束的一段私聊。\n\n{transcript}\n\n"
        "请给下一段对话开头的自己写一张交接卡，第一人称，写给自己看：\n"
        "- 在聊什么、聊到哪一步了\n"
        f"- {user_name}现在的状态和心情\n"
        "- 这段对话里的语气、称呼、新出现的梗\n"
        "- 没做完的事、约好下次继续的事\n"
        "只写交接卡正文，不超过 400 字，不要客套和标题。只写对话里真的出现过的，不要编。"
    )}]


def _due_handoffs(now: float) -> list[sqlite3.Row]:
    if not bridge.OUTBOX_PATH.exists():
        return []
    with closing(bridge._outbox()) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT actor, last_private_ts FROM activity WHERE last_private_ts>0 AND last_private_ts>handoff_done_ts"
            " AND last_private_ts<=? AND handoff_retry_at<=?",
            (now - HANDOFF_QUIET_SECONDS, now),
        ).fetchall()


def _mark_handoff(actor: str, *, done_ts: float | None = None, retry_at: float | None = None) -> None:
    with closing(bridge._outbox()) as conn, conn:
        if done_ts is not None:
            # 只往前推：写完旧一段时，不会把已经写过的更新的一段标回「没写」
            conn.execute("UPDATE activity SET handoff_done_ts=MAX(handoff_done_ts, ?), handoff_retry_at=0 WHERE actor=?",
                         (done_ts, actor))
        if retry_at is not None:
            conn.execute("UPDATE activity SET handoff_retry_at=? WHERE actor=?", (retry_at, actor))


async def write_handoff(actor: str, session_ts: float, *, now: float | None = None) -> str:
    """为一段已结束的私聊写交接卡。返回结果说明（saved / skipped:… / failed:…）。"""
    now = now or time.time()
    cfg = bridge.load_config()
    source_ai = bridge.source_ai_for(actor, cfg)
    model, pinned = pin_background_model(actor)
    if not source_ai or not model or pinned is None:
        _mark_handoff(actor, done_ts=session_ts)
        return "skipped:no_model" if source_ai else "skipped:no_hub_identity"
    if now - session_ts > HANDOFF_GIVE_UP_SECONDS:
        _mark_handoff(actor, done_ts=session_ts)
        return "skipped:too_old"
    with closing(bridge._outbox()) as conn:
        conn.row_factory = sqlite3.Row
        turns = _session_turns(conn, actor, session_ts)
    if not turns:
        _mark_handoff(actor, done_ts=session_ts)
        return "skipped:no_turns"
    try:
        if not still_own_model(actor, model):
            raise RuntimeError("这位 AI 的模型刚刚换过，这次不写")
        content = await complete(model, _handoff_prompt(actor, turns), snapshot=pinned)
        if not content:
            raise RuntimeError("模型没有返回内容")
        raw = await asyncio.wait_for(
            bridge._call_tool(cfg, "capture", {
                "action": "handoff", "source_ai": source_ai, "platform": str(cfg.get("platform") or "aionshome"),
                "content": content[:2000], "model": model_label(model),
            }),
            timeout=float(cfg.get("capture_timeout_seconds") or 20.0),
        )
        if '"saved"' not in raw:
            raise RuntimeError(f"Hub 未保存：{raw[:200]}")
    except asyncio.CancelledError:
        raise
    except Exception as error:
        _mark_handoff(actor, retry_at=now + HANDOFF_RETRY_SECONDS)
        return f"failed:{str(error)[:200]}"
    _mark_handoff(actor, done_ts=session_ts)
    return "saved"


async def run_handoffs(now: float | None = None) -> dict[str, str]:
    now = now or time.time()
    cfg = bridge.load_config()
    if not bridge._is_active(cfg) or bridge._in_cooldown("write"):
        return {}
    results = {}
    for row in _due_handoffs(now):
        results[row["actor"]] = await write_handoff(row["actor"], row["last_private_ts"], now=now)
        _status["handoff"][row["actor"]] = {"at": now, "result": results[row["actor"]]}
        if results[row["actor"]].startswith("failed"):
            log.warning("[MemoryHub] %s 交接卡没写成：%s", row["actor"], results[row["actor"]])
    return results


# ── 做梦 ──

def _load_jobs() -> dict[str, Any]:
    try:
        data = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_jobs(data: dict[str, Any]) -> None:
    JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = JOBS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, JOBS_PATH)


def dream_actors() -> list[str]:
    """接了 Memory Hub 的已启用座位（同一个 Hub 身份只做一次梦）。"""
    import actors
    seen, result = set(), []
    for actor in actors.list_actors(include_disabled=False):
        hub_id = bridge.source_ai_for(actor["id"])
        if hub_id and hub_id not in seen:
            seen.add(hub_id)
            result.append(actor["id"])
    return result


async def dream_for(actor: str) -> str:
    cfg = bridge.load_config()
    source_ai = bridge.source_ai_for(actor, cfg)
    model, pinned = pin_background_model(actor)  # 在等 Hub 之前就固定，等待期间线路被改也不认
    if not source_ai:
        return "skipped:no_hub_identity"
    if not model or pinned is None:
        return "skipped:no_model"
    timeout = float(cfg.get("capture_timeout_seconds") or 20.0)
    raw = await asyncio.wait_for(
        bridge._call_tool(cfg, "dream", {"action": "materials", "source_ai": source_ai}), timeout=timeout,
    )
    materials = json.loads(raw)
    if materials.get("error"):
        raise RuntimeError(materials["error"])
    if materials.get("already_dreamed_today"):
        return "already_dreamed"
    if materials.get("reason") == "too_few_materials" or not materials.get("prompt"):
        return "skipped:too_few_materials"
    if not still_own_model(actor, model):
        raise RuntimeError("这位 AI 的模型刚刚换过，今晚不用别的模型代写")
    content = await complete(model, _persona_messages(actor) + [{"role": "user", "content": materials["prompt"]}],
                             snapshot=pinned)
    if not content:
        raise RuntimeError("模型没有返回内容")
    raw = await asyncio.wait_for(
        bridge._call_tool(cfg, "dream", {
            "action": "write", "kind": "dream", "source_ai": source_ai,
            "content": content, "model": model_label(model),
        }),
        timeout=timeout,
    )
    status = str(json.loads(raw).get("status") or "")
    if status in ("dreamed", "already_dreamed"):
        return status
    raise RuntimeError(f"Hub 未保存：{raw[:200]}")


def _dream_due(now: datetime) -> bool:
    return (now.hour, now.minute) >= DREAM_AFTER


async def run_dreams(now: datetime | None = None) -> dict[str, str]:
    now = now or datetime.now(LOCAL_TZ)
    cfg = bridge.load_config()
    if not _dream_due(now) or not bridge._is_active(cfg) or bridge._in_cooldown("write"):
        return {}
    if _dream_lock.locked():  # 同一时间只跑一轮，避免两轮同时读写状态文件
        return {}
    async with _dream_lock:
        return await _run_dreams_locked(now, cfg)


_dream_lock = asyncio.Lock()


async def _run_dreams_locked(now: datetime, cfg: dict[str, Any]) -> dict[str, str]:
    day = now.strftime("%Y-%m-%d")
    jobs = _load_jobs()
    state = jobs.setdefault("dream", {})
    results = {}
    for actor in dream_actors():
        entry = state.get(actor) or {}
        if entry.get("day") == day and (entry.get("done") or entry.get("attempts", 0) >= DREAM_MAX_ATTEMPTS
                                        or time.time() - entry.get("last_try", 0) < DREAM_RETRY_SECONDS):
            continue
        if entry.get("day") != day:
            entry = {"day": day, "attempts": 0}
        # 先把这次尝试记下来再调模型：中途取消或重启，这次也算用掉了，一晚不会超过 3 次
        entry["attempts"] += 1
        entry["last_try"] = time.time()
        entry["result"] = "running"
        state[actor] = entry
        _save_jobs(jobs)
        try:
            result = await dream_for(actor)
            entry["done"] = True  # 做成、已做过、或按规则跳过：今晚都不再试
        except asyncio.CancelledError:
            entry["result"] = "cancelled"
            _save_jobs(jobs)
            raise
        except Exception as error:
            result = f"failed:{str(error)[:200]}"
            log.warning("[MemoryHub] %s 今晚的梦没做成：%s", actor, result)
        entry["result"] = result
        _save_jobs(jobs)
        results[actor] = result
        _status["dream"][actor] = {"at": time.time(), "result": result}
    return results


# ── 常驻 ──

def status() -> dict[str, Any]:
    return {"handoff": dict(_status["handoff"]), "dream": dict(_status["dream"])}


async def jobs_worker(interval_seconds: float = 300.0) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        for job in (run_handoffs, run_dreams):
            try:
                await job()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log.warning("[MemoryHub] 后台任务出错：%s", error)
