"""AI 自己改签名（11-11 小猫）：回复里写 [签名:内容]，家里把名片上的签名换掉，这个标记不显示在聊天里。

只有 TA 自己正常回复时才算数；系统叫醒、AI 之间接话时只删不改（和 [出门:…] 一样）。
"""

from __future__ import annotations

import re

MAX_CHARS = 40
MAX_PER_DAY = 3
_TAG = re.compile(r"\[签名\s*[:：]\s*([^\]\n]{0,120})\]")
_changes: dict[str, list[float]] = {}


def _clean(value: str) -> str:
    return re.sub(r"[\x00-\x1f<>]", "", value).strip()[:MAX_CHARS]


def process(text: str, actor: str, *, allowed: bool = True, now: float | None = None) -> str:
    """摘掉标记；allowed 时用最后一个标记换签名。返回给人看的正文。"""
    if not text or "签名" not in text:
        return text
    found = _TAG.findall(text)
    if not found:
        return text
    shown = re.sub(r"\n{3,}", "\n\n", _TAG.sub("", text)).strip()
    sign = _clean(found[-1])
    if allowed and sign:
        import time
        moment = time.time() if now is None else now
        recent = [t for t in _changes.get(actor, []) if moment - t < 86400]
        if len(recent) < MAX_PER_DAY:
            try:
                import actors
                if actors.set_sign(actor, sign):
                    recent.append(moment)
            except Exception:
                pass
        _changes[actor] = recent
    return shown


def ability_text(user_name: str) -> str:
    return (f"[签名:内容] — 想换自己名片上的签名时用（比如 [签名:今天也在想你]），{MAX_CHARS} 字以内。"
            f"这个标记{user_name}看不到，签名会显示在你的名片上。想换才换，不用每次都写。")
