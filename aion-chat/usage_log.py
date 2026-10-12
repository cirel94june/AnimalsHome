"""
用量小本本：每天每个模型、每类用途请求了几次、读了多少、写了多少。

- 中转站在回复里给了用量（usage）就记真实数字；没给就按字数估算，并记下「这次是估算的」，页面上会注明。
  估算：中文大约一个字一个 token，其他字符大约四个一个 token；只是量级参考。
- 用途按「是哪段代码发起的」归类（聊天、自主活动、做梦/交接卡、旅行……），
  不需要每个功能自己记账；哨兵和出门逛这两条不走 stream_ai 的路径单独记。
- 只存数字，不存聊天内容。记账失败只打日志，绝不影响聊天。
"""

from __future__ import annotations

import logging
import math
import sqlite3
import threading
import time
from typing import Any

from config import DATA_DIR

log = logging.getLogger("usage_log")

USAGE_DB = DATA_DIR / "usage.db"
KEEP_DAYS = 120
_lock = threading.Lock()
_last_prune = ""

# 发起请求的模块 → 用途
MODULE_KINDS = {
    "routes.chat": "聊天", "routes.chatroom": "聊天", "chatroom": "聊天", "seat_chat": "聊天",
    "autonomy": "自主活动", "memory_hub_jobs": "做梦 / 交接卡", "travel": "旅行", "mcp_outings": "出门逛",
    "moments": "朋友圈", "schedule": "日程提醒", "camera": "监控", "location": "定位",
    "theater": "小剧场", "theater_studio": "小剧场", "date_theater": "小剧场",
    "seeky": "Seeky", "ghost_forest": "小游戏", "doudizhu": "小游戏", "book": "共读",
    "fund": "基金", "repair_worker": "维修室", "repair_summary": "维修室", "ai_providers": "看图（哨兵代看）",
    "memory": "记忆整理", "memory_compression": "记忆整理", "persona_evolution": "人格成长",
    "english_corner": "学习角", "gift": "礼物",
}


def kind_for_module(module: str) -> str:
    return MODULE_KINDS.get(module or "", f"其他（{module or '未知'}）")


def estimate_tokens(text: str) -> int:
    text = text or ""
    cjk = sum(1 for ch in text if "㐀" <= ch <= "鿿" or "豈" <= ch <= "﫿")
    return math.ceil(cjk + (len(text) - cjk) / 4)


def messages_text(messages: list[dict] | None) -> str:
    parts: list[str] = []
    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(str(p.get("text") or "") for p in content if isinstance(p, dict))
    return "\n".join(parts)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(USAGE_DB, timeout=5)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS usage (day TEXT, kind TEXT, model TEXT, requests INTEGER, "
        "prompt INTEGER, completion INTEGER, estimated INTEGER, PRIMARY KEY (day, kind, model))"
    )
    return conn


def record(model: str, kind: str, prompt_tokens: int, completion_tokens: int, *, estimated: bool) -> None:
    global _last_prune
    day = time.strftime("%Y-%m-%d")
    try:
        with _lock:
            conn = _connect()
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO usage VALUES (?,?,?,1,?,?,?) ON CONFLICT(day, kind, model) DO UPDATE SET "
                        "requests=requests+1, prompt=prompt+excluded.prompt, "
                        "completion=completion+excluded.completion, estimated=estimated+excluded.estimated",
                        (day, kind, model or "未知模型", max(0, int(prompt_tokens or 0)),
                         max(0, int(completion_tokens or 0)), 1 if estimated else 0),
                    )
                    if _last_prune != day:
                        cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - KEEP_DAYS * 86400))
                        conn.execute("DELETE FROM usage WHERE day < ?", (cutoff,))
                        _last_prune = day
            finally:
                conn.close()
    except Exception as error:  # 记账失败不影响聊天
        log.warning("用量记账失败：%s", type(error).__name__)


def record_call(model: str, kind: str, meta: dict | None, prompt_text: str, output_text: str) -> None:
    """stream_ai 用：meta 里有中转站给的用量就用，没有就估算。"""
    meta = meta or {}
    prompt, completion = int(meta.get("prompt_tokens") or 0), int(meta.get("completion_tokens") or 0)
    if prompt or completion:
        record(model, kind, prompt, completion, estimated=False)
    else:
        record(model, kind, estimate_tokens(prompt_text), estimate_tokens(output_text), estimated=True)


def record_json(model: str, kind: str, data: Any, prompt_text: str, output_text: str) -> None:
    """非流式调用用：OpenAI 的 usage 或 Gemini 的 usageMetadata。"""
    usage = data.get("usage") if isinstance(data, dict) else None
    if isinstance(usage, dict) and (usage.get("prompt_tokens") or usage.get("completion_tokens")):
        record(model, kind, usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0, estimated=False)
        return
    gemini = data.get("usageMetadata") if isinstance(data, dict) else None
    if isinstance(gemini, dict) and (gemini.get("promptTokenCount") or gemini.get("candidatesTokenCount")):
        record(model, kind, gemini.get("promptTokenCount") or 0, gemini.get("candidatesTokenCount") or 0,
               estimated=False)
        return
    record(model, kind, estimate_tokens(prompt_text), estimate_tokens(output_text), estimated=True)


def summary(days: int) -> dict:
    """最近 days 天（含今天）的合计、按用途、按模型。"""
    days = max(1, min(int(days or 1), KEEP_DAYS))
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - (days - 1) * 86400))
    try:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT day, kind, model, requests, prompt, completion, estimated FROM usage WHERE day >= ?",
                (since,),
            ).fetchall()
        finally:
            conn.close()
    except Exception as error:
        log.warning("读用量失败：%s", type(error).__name__)
        rows = []

    def group(index: int) -> list[dict]:
        out: dict[str, dict] = {}
        for row in rows:
            item = out.setdefault(row[index], {"name": row[index], "requests": 0, "prompt": 0,
                                               "completion": 0, "estimated": 0})
            item["requests"] += row[3]
            item["prompt"] += row[4]
            item["completion"] += row[5]
            item["estimated"] += row[6]
        return sorted(out.values(), key=lambda x: (-x["requests"], x["name"]))

    total = {"requests": sum(r[3] for r in rows), "prompt": sum(r[4] for r in rows),
             "completion": sum(r[5] for r in rows), "estimated": sum(r[6] for r in rows)}
    by_day = {d["name"]: d for d in group(0)}
    return {"days": days, "since": since, "total": total, "by_kind": group(1), "by_model": group(2),
            "by_day": [by_day[k] for k in sorted(by_day)]}
