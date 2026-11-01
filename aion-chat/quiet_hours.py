"""静默时段：「HH:MM」到「HH:MM」，可以跨午夜（比如 23:00 到 08:00）。

按服务器本地时间算（服务进程 TZ=Asia/Shanghai），和摄像头哨兵的静默时段同一个算法。
"""

from __future__ import annotations

import re
import time

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def valid_hhmm(value: str) -> bool:
    return bool(_HHMM.match(str(value or "")))


def _minutes(value: str) -> int:
    hour, minute = str(value).split(":")
    return int(hour) * 60 + int(minute)


def is_quiet(cfg: dict, now: float | None = None) -> bool:
    """cfg = {enabled, start, end}；格式不对或开始等于结束都当没开。"""
    if not cfg or not cfg.get("enabled"):
        return False
    start, end = cfg.get("start"), cfg.get("end")
    if not (valid_hhmm(start) and valid_hhmm(end)) or start == end:
        return False
    local = time.localtime(time.time() if now is None else now)
    cur = local.tm_hour * 60 + local.tm_min
    s, e = _minutes(start), _minutes(end)
    return s <= cur < e if s < e else (cur >= s or cur < e)


def quiet_end_after(cfg: dict, now: float | None = None) -> float:
    """正处在静默时段时，返回这段静默结束的时间戳；不在静默里就返回 now。"""
    now = time.time() if now is None else now
    if not is_quiet(cfg, now):
        return now
    local = time.localtime(now)
    cur = local.tm_hour * 60 + local.tm_min
    end = _minutes(cfg["end"])
    ahead = (end - cur) % (24 * 60) or 24 * 60
    # 对齐到整分钟，避免秒数让「刚好结束」又被判成还在静默里
    return now - local.tm_sec + ahead * 60
