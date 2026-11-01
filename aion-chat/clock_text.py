"""给 AI 看的「现在几点」。

11-01 小猫：小克下午写的日记以为是凌晨、以为今天是工作日。原来各处只给「2026年11月01日 17:33」，
星期几要模型自己推算，上午还是下午、今天上不上班也没说。这里统一写清楚：
星期几、时段（凌晨 / 上午 / 下午 / 晚上…）、北京时间、今天是工作日还是休息日（含调休和法定假日）。
"""

from __future__ import annotations

from datetime import date, datetime

_WEEKDAYS = "一二三四五六日"


def _period(hour: int) -> str:
    if hour < 5:
        return "凌晨"
    if hour < 8:
        return "早上"
    if hour < 11:
        return "上午"
    if hour < 13:
        return "中午"
    if hour < 18:
        return "下午"
    if hour < 23:
        return "晚上"
    return "深夜"


def day_kind(day: date) -> str:
    """「工作日」或「休息日」。按中国的调休和法定假日算；日历库不认识的年份退回按周末算。"""
    try:
        from chinese_calendar import is_workday
        work = is_workday(day)
    except Exception:
        work = day.weekday() < 5
    return "工作日" if work else "休息日"


def now_text(now: datetime | None = None, *, seconds: bool = True) -> str:
    """例：2026年11月1日 星期日 下午 17:33:05（北京时间，今天是休息日）"""
    now = now or datetime.now()
    clock = now.strftime("%H:%M:%S" if seconds else "%H:%M")
    return (f"{now.year}年{now.month}月{now.day}日 星期{_WEEKDAYS[now.weekday()]} {_period(now.hour)} {clock}"
            f"（北京时间，今天是{day_kind(now.date())}）")
