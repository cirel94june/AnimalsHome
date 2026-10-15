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


_COLUMNS = ("day TEXT, kind TEXT, route TEXT, model TEXT, requests INTEGER, prompt INTEGER, completion INTEGER, "
            "estimated INTEGER, cache_read INTEGER DEFAULT 0, cache_write INTEGER DEFAULT 0, "
            "cache_write_1h INTEGER DEFAULT 0, PRIMARY KEY (day, kind, route, model)")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(USAGE_DB, timeout=5)
    conn.execute(f"CREATE TABLE IF NOT EXISTS usage ({_COLUMNS})")
    have = {row[1] for row in conn.execute("PRAGMA table_info(usage)")}
    if "route" not in have:
        # 旧表（按 日/用途/模型 记，没有线路）：换成新表，旧行的线路记成空 = 「未知线路（旧记录）」，不按现在的配置猜
        with conn:
            for column in ("cache_read", "cache_write"):
                if column not in have:
                    conn.execute(f"ALTER TABLE usage ADD COLUMN {column} INTEGER DEFAULT 0")
            conn.execute("ALTER TABLE usage RENAME TO usage_old")
            conn.execute(f"CREATE TABLE usage ({_COLUMNS})")
            conn.execute(
                "INSERT INTO usage (day, kind, route, model, requests, prompt, completion, estimated, cache_read, cache_write) "
                "SELECT day, kind, '', model, requests, prompt, completion, estimated, "
                "COALESCE(cache_read, 0), COALESCE(cache_write, 0) FROM usage_old"
            )
            conn.execute("DROP TABLE usage_old")
    return conn


def route_for_model(model: str) -> str:
    """记账时这个模型属于哪条线路（中转站）；不是中转站的（内置模型、哨兵等）记 other。记下来就不再变。"""
    try:
        from config import MODELS
        return str((MODELS.get(model) or {}).get("route_id") or OTHER_ROUTE)
    except Exception:
        return OTHER_ROUTE


def record(model: str, kind: str, prompt_tokens: int, completion_tokens: int, *, estimated: bool,
           cache_read: int = 0, cache_write: int = 0, cache_write_1h: int = 0, route: str | None = None) -> None:
    """prompt_tokens 是这次读入的全部（含命中缓存的部分）；cache_write_1h 是写缓存里保留 1 小时的部分。"""
    global _last_prune
    day = time.strftime("%Y-%m-%d")
    route = route_for_model(model) if route is None else route
    cache_write = max(0, int(cache_write or 0))
    try:
        with _lock:
            conn = _connect()
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO usage (day, kind, route, model, requests, prompt, completion, estimated, "
                        "cache_read, cache_write, cache_write_1h) VALUES (?,?,?,?,1,?,?,?,?,?,?) "
                        "ON CONFLICT(day, kind, route, model) DO UPDATE SET "
                        "requests=requests+1, prompt=prompt+excluded.prompt, "
                        "completion=completion+excluded.completion, estimated=estimated+excluded.estimated, "
                        "cache_read=cache_read+excluded.cache_read, cache_write=cache_write+excluded.cache_write, "
                        "cache_write_1h=cache_write_1h+excluded.cache_write_1h",
                        (day, kind, route, model or "未知模型", max(0, int(prompt_tokens or 0)),
                         max(0, int(completion_tokens or 0)), 1 if estimated else 0,
                         max(0, int(cache_read or 0)), cache_write,
                         min(cache_write, max(0, int(cache_write_1h or 0)))),
                    )
                    if _last_prune != day:
                        cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - KEEP_DAYS * 86400))
                        conn.execute("DELETE FROM usage WHERE day < ?", (cutoff,))
                        _last_prune = day
            finally:
                conn.close()
    except Exception as error:  # 记账失败不影响聊天
        log.warning("用量记账失败：%s", type(error).__name__)


def _num(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def cache_tokens(usage: Any) -> tuple[int, int]:
    """从各家的用量格式里取（读缓存, 写缓存）。各家字段名不一样，认得的都试一遍。"""
    if not isinstance(usage, dict):
        return 0, 0
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    read = (_num(details.get("cached_tokens")) if isinstance(details, dict) else 0) \
        or _num(usage.get("cache_read_input_tokens")) or _num(usage.get("cachedContentTokenCount")) \
        or _num(usage.get("cached_input_tokens")) or _num(usage.get("cached_tokens"))
    write = _num(usage.get("cache_creation_input_tokens"))
    if not write and isinstance(details, dict):
        write = _num(details.get("cache_creation_tokens")) or _num(details.get("cache_write_tokens"))
    return read, write


def _input_excludes_cache(usage: Any) -> bool:
    """Claude 原生格式（input_tokens + cache_*_input_tokens，没有 prompt_tokens）里，读入不含缓存部分。
    OpenAI（prompt_tokens / input_tokens + cached_tokens）和 Gemini 的读入本来就含缓存。"""
    return (isinstance(usage, dict) and "prompt_tokens" not in usage and "input_tokens" in usage
            and ("cache_read_input_tokens" in usage or "cache_creation_input_tokens" in usage))


def cache_write_1h(usage: Any, write: int, ttl: str | None) -> int:
    """写缓存里保留 1 小时的有多少：中转站给了 5 分钟 / 1 小时明细就用明细，
    没给就按这次实际发出去的缓存时长（ttl）算，都不知道按 5 分钟算。"""
    detail = usage.get("cache_creation") if isinstance(usage, dict) else None
    if isinstance(detail, dict) and ("ephemeral_1h_input_tokens" in detail or "ephemeral_5m_input_tokens" in detail):
        return min(write, _num(detail.get("ephemeral_1h_input_tokens")))
    return write if ttl == "1h" else 0


def _record_real(model: str, kind: str, prompt: int, completion: int, usage: Any,
                 ttl: str | None = None, route: str | None = None) -> None:
    read, write = cache_tokens(usage)
    if _input_excludes_cache(usage):
        prompt += read + write  # 统一成含缓存的总读入
    record(model, kind, prompt, completion, estimated=False, cache_read=read, cache_write=write,
           cache_write_1h=cache_write_1h(usage, write, ttl), route=route)


def record_call(model: str, kind: str, meta: dict | None, prompt_text: str, output_text: str,
                route: str | None = None) -> None:
    """stream_ai 用：meta 里有中转站给的用量就用，没有就估算。route 是调用时的线路。"""
    meta = meta or {}
    prompt, completion = _num(meta.get("prompt_tokens")), _num(meta.get("completion_tokens"))
    if prompt or completion:
        _record_real(model, kind, prompt, completion, meta.get("raw"), meta.get("cache_ttl"), route)
    else:
        record(model, kind, estimate_tokens(prompt_text), estimate_tokens(output_text), estimated=True, route=route)


def record_json(model: str, kind: str, data: Any, prompt_text: str, output_text: str) -> None:
    """非流式调用用：OpenAI 的 usage 或 Gemini 的 usageMetadata。"""
    usage = data.get("usage") if isinstance(data, dict) else None
    if isinstance(usage, dict) and (usage.get("prompt_tokens") or usage.get("completion_tokens")):
        _record_real(model, kind, _num(usage.get("prompt_tokens")), _num(usage.get("completion_tokens")), usage)
        return
    if isinstance(usage, dict) and (usage.get("input_tokens") or usage.get("output_tokens")):  # Claude 原生
        _record_real(model, kind, _num(usage.get("input_tokens")), _num(usage.get("output_tokens")), usage)
        return
    gemini = data.get("usageMetadata") if isinstance(data, dict) else None
    if isinstance(gemini, dict) and (gemini.get("promptTokenCount") or gemini.get("candidatesTokenCount")):
        _record_real(model, kind, _num(gemini.get("promptTokenCount")), _num(gemini.get("candidatesTokenCount")), gemini)
        return
    record(model, kind, estimate_tokens(prompt_text), estimate_tokens(output_text), estimated=True)


def summary(days: int) -> dict:
    """最近 days 天（含今天）的合计、按用途、按模型、按线路（线路是记账当时的，不随现在的配置变）。"""
    days = max(1, min(int(days or 1), KEEP_DAYS))
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - (days - 1) * 86400))
    try:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT day, kind, model, requests, prompt, completion, estimated, cache_read, cache_write, "
                "cache_write_1h, route FROM usage WHERE day >= ?",
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
            item = out.setdefault(row[index], {"name": row[index], "requests": 0, "prompt": 0, "completion": 0,
                                               "estimated": 0, "cache_read": 0, "cache_write": 0, "cache_write_1h": 0})
            item["requests"] += row[3]
            item["prompt"] += row[4]
            item["completion"] += row[5]
            item["estimated"] += row[6]
            item["cache_read"] += row[7] or 0
            item["cache_write"] += row[8] or 0
            item["cache_write_1h"] += row[9] or 0
            if index == 10:
                item.setdefault("models", [])
                if row[2] not in item["models"]:
                    item["models"].append(row[2])
        return sorted(out.values(), key=lambda x: (-x["requests"], x["name"]))

    total = {"requests": sum(r[3] for r in rows), "prompt": sum(r[4] for r in rows),
             "completion": sum(r[5] for r in rows), "estimated": sum(r[6] for r in rows),
             "cache_read": sum(r[7] or 0 for r in rows), "cache_write": sum(r[8] or 0 for r in rows),
             "cache_write_1h": sum(r[9] or 0 for r in rows)}
    by_day = {d["name"]: d for d in group(0)}
    return {"days": days, "since": since, "total": total, "by_kind": group(1), "by_model": group(2),
            "by_route": group(10), "by_day": [by_day[k] for k in sorted(by_day)]}


# ── 价格与每日花费提醒 ──────────────────────────────
# 价格存在服务器上，每条线路（中转站）各填各的；内置模型、哨兵等不属于任何线路的算「其他」。
# 文件格式：{"daily_limit": 元, "routes": {线路 id: {billing, call, inp, out, cache, write}}}
PRICES_FILE = DATA_DIR / "usage_prices.json"
OTHER_ROUTE = "other"
UNKNOWN_ROUTE = ""  # 加线路记录之前的旧用量：不知道是哪家，不计价
ROUTE_PRICE_FIELDS = ("call", "inp", "out", "cache", "write")
BILLINGS = ("token", "call")
MAX_PRICE = 1_000_000  # 每百万 / 每次 / 每天提醒线都不会超过这个数；超过当没填


def _price(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and 0 <= number <= MAX_PRICE else None


def _finite(value: float | None) -> float | None:
    return value if value is None or math.isfinite(value) else None


def _clean_route_prices(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = {key: _price(raw.get(key)) for key in ROUTE_PRICE_FIELDS}
    out["billing"] = raw.get("billing") if raw.get("billing") in BILLINGS else ""
    return out


def _clean_prices(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    routes = raw.get("routes") if isinstance(raw.get("routes"), dict) else {}
    return {
        "daily_limit": _price(raw.get("daily_limit")),
        "routes": {str(rid)[:120]: _clean_route_prices(v) for rid, v in list(routes.items())[:200]},
    }


def load_prices() -> dict:
    try:
        import json
        raw = json.loads(PRICES_FILE.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    return _clean_prices(raw)


def save_prices(data: dict) -> dict:
    import json
    prices = _clean_prices(data)
    tmp = PRICES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(prices, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PRICES_FILE)
    return prices


def token_cost(total: dict, prices: dict) -> float | None:
    """按量粗算（元）。读入、写出价都填了才算。写入缓存没填价时，保留 1 小时的按读入价 ×2、5 分钟的 ×1.25。"""
    inp, out = prices.get("inp"), prices.get("out")
    if inp is None or out is None:
        return None
    cache = prices.get("cache")
    cache = inp if cache is None else cache
    prompt = total.get("prompt", 0)
    read = min(total.get("cache_read", 0), prompt)
    written = min(total.get("cache_write", 0), prompt - read)
    long_written = min(total.get("cache_write_1h", 0), written)
    write = prices.get("write")
    write_cost = written * write if write is not None else long_written * inp * 2 + (written - long_written) * inp * 1.25
    plain = prompt - read - written
    return _finite((plain * inp + read * cache + write_cost + total.get("completion", 0) * out) / 1e6)


def call_cost(total: dict, prices: dict) -> float | None:
    call = prices.get("call")
    return None if call is None else _finite(total.get("requests", 0) * call)


def chosen_cost(token: float | None, call: float | None, billing: str) -> float | None:
    """实际按哪种算：选了就按选的；没选时有按量价就按量，否则按次。"""
    if billing == "call":
        return call
    if billing == "token":
        return token
    return token if token is not None else call


def route_costs(by_route: list[dict], prices: dict, route_name) -> list[dict]:
    """每条线路（记账当时的）按次、按量、实际各多少钱。route_name(线路 id) -> 显示名。
    旧记录（线路未知）不计价、标 unknown。"""
    out = []
    for row in by_route:
        item = {**row, "id": row["name"], "name": route_name(row["name"]), "unknown": row["name"] == UNKNOWN_ROUTE}
        p = _clean_route_prices({}) if item["unknown"] else (prices["routes"].get(item["id"]) or _clean_route_prices({}))
        item["prices"] = p
        item["cost_token"] = token_cost(item, p)
        item["cost_call"] = call_cost(item, p)
        item["cost"] = chosen_cost(item["cost_token"], item["cost_call"], p["billing"])
        out.append(item)
    return sorted(out, key=lambda r: (r["unknown"], r["id"] == OTHER_ROUTE, -r["requests"], r["name"]))


def total_cost(routes: list[dict]) -> float | None:
    known = [r["cost"] for r in routes if r["cost"] is not None]
    return _finite(round(sum(known), 4)) if known else None


def today_spend(route_name) -> dict:
    """今天大约花了多少（没填价格的线路、旧记录不算在内）、提醒线是多少、超没超。"""
    prices = load_prices()
    cost = total_cost(route_costs(summary(1)["by_route"], prices, route_name))
    limit = prices.get("daily_limit")
    return {"cost": cost, "limit": limit,
            "over": cost is not None and limit is not None and limit > 0 and cost >= limit}
