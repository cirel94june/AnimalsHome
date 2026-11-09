"""
日历上的日子（11-09 小猫）：公历节日、农历节日，加上小猫自己在桌面设置里填的生日和纪念日。
AI 从 now_text 里知道今天和最近几天是什么日子；小日历上也显示。

农历节日按年写死到 2030 年，不引入新的依赖。到期后这几个节日只是不显示，公历的和自己加的不受影响。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

FIXED = {
    "01-01": "元旦", "02-14": "情人节", "03-08": "妇女节", "05-01": "劳动节", "05-20": "520", "06-01": "儿童节",
    "10-01": "国庆节", "10-31": "万圣节", "12-24": "平安夜", "12-25": "圣诞节",
}
LUNAR = {
    "春节": ("2026-02-17", "2027-02-06", "2028-01-26", "2029-02-13", "2030-02-03"),
    "元宵节": ("2026-03-03", "2027-02-20", "2028-02-09", "2029-02-27", "2030-02-17"),
    "清明节": ("2026-04-05", "2027-04-05", "2028-04-04", "2029-04-04", "2030-04-05"),
    "端午节": ("2026-06-19", "2027-06-09", "2028-05-28", "2029-06-16", "2030-06-05"),
    "七夕": ("2026-08-19", "2027-08-08", "2028-08-26", "2029-08-16", "2030-08-05"),
    "中秋节": ("2026-09-25", "2027-09-15", "2028-10-03", "2029-09-22", "2030-09-12"),
    "重阳节": ("2026-10-18", "2027-10-08", "2028-10-26", "2029-10-16", "2030-10-05"),
}
_BY_DATE: dict[str, list[str]] = {}
for _name, _days in LUNAR.items():
    for _day in _days:
        _BY_DATE.setdefault(_day, []).append(_name)
for _day in LUNAR["春节"]:
    _eve = (date.fromisoformat(_day) - timedelta(days=1)).isoformat()
    _BY_DATE.setdefault(_eve, []).append("除夕")
_MD = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


def _personal() -> dict[str, list[str]]:
    """桌面设置里的生日和纪念日（MM-DD）。"""
    try:
        from config import load_worldbook
        from skin import load_skin

        desk = load_skin().get("desk") or {}
        user = (load_worldbook().get("user_name") or "小猫").strip() or "小猫"
    except Exception:
        return {}
    out: dict[str, list[str]] = {}
    if _MD.match(str(desk.get("birthday") or "")):
        out.setdefault(desk["birthday"], []).append(f"{user}的生日")
    for item in desk.get("dates") if isinstance(desk.get("dates"), list) else []:
        if isinstance(item, dict) and _MD.match(str(item.get("md") or "")) and item.get("name"):
            out.setdefault(item["md"], []).append(str(item["name"])[:30])
    return out


def on(day: date, personal: dict[str, list[str]] | None = None) -> list[str]:
    personal = _personal() if personal is None else personal
    md = day.strftime("%m-%d")
    names = list(personal.get(md, []))
    if md in FIXED:
        names.append(FIXED[md])
    return names + _BY_DATE.get(day.isoformat(), [])


def upcoming(day: date, within: int = 3) -> list[tuple[int, str]]:
    """[(还有几天, 名字)]，0 是今天。"""
    personal = _personal()
    out = []
    for offset in range(within + 1):
        out += [(offset, name) for name in on(day + timedelta(days=offset), personal)]
    return out


def hint(day: date) -> str:
    """给 AI 看的一句话，没什么日子时是空串。"""
    parts = []
    for offset, name in upcoming(day):
        parts.append(f"今天是{name}" if offset == 0 else f"明天是{name}" if offset == 1 else f"再过{offset}天是{name}")
    return "（" + "，".join(parts) + "）" if parts else ""
