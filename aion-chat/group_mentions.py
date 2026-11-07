"""群聊 @ 和发言顺序（11-07 小猫）。

- 她 @ 了谁，这条就只有被 @ 的回，按 @ 的先后。
- 没 @ 时按她排的顺序（设置里「按我排的顺序」）；没排就是原来的规则（随机 / 小克先 / Lucien 先，其他家人跟在后面）。
- AI 回话里 @ 了别人：被 @ 的下一个说。还没轮到的提前，已经说过的再说一次；
  这种「再说一次」每条她的消息最多 MAX_RELAY 次，免得 AI 之间来回 @ 停不下来、烧额度。
- 名字认显示名，也认 actors.json 里每位的 aliases（小名不写进仓库）。
"""

from __future__ import annotations

import re
from typing import Iterable

MAX_RELAY = 3
_AT = "@＠"


def names_for(actor_id: str) -> list[str]:
    """这位家人能被 @ 的名字：显示名 + aliases。"""
    import actors
    names = [actors.display_name(actor_id)]
    try:
        entry = (actors._load_raw().get("seats") or {}).get(actor_id) or {}
        names += [str(a).strip() for a in entry.get("aliases") or [] if str(a).strip()]
    except Exception:
        pass
    out: list[str] = []
    for name in names:
        if name and name not in out:
            out.append(name)
    return out


def find_mentions(text: str, members: Iterable[str]) -> list[str]:
    """按出现先后，返回被 @ 的家人（去重）。邮箱那种 a@b 不算；@ 后面可以有一个空格。"""
    members = list(members)
    if not text or not any(ch in text for ch in _AT):
        return []
    table = sorted(((name, actor) for actor in members for name in names_for(actor)),
                   key=lambda item: len(item[0]), reverse=True)  # 长名字先配，免得「小克克」被「小克」截走
    found: list[str] = []
    for match in re.finditer(f"[{_AT}]", text):
        start = match.start()
        if start > 0 and re.match(r"[A-Za-z0-9_.]", text[start - 1]):
            continue
        rest = text[match.end():]
        rest = rest[1:] if rest[:1] in (" ", "　") else rest
        for name, actor in table:
            if rest[:len(name)].lower() == name.lower():
                if actor not in found:
                    found.append(actor)
                break
    return found


def clean_order(order: Iterable[str], members: Iterable[str]) -> list[str]:
    """她排的顺序：只留现在在群里的；没排进去的（新来的）接在最后。"""
    members = list(members)
    out = [a for a in order or [] if a in members]
    out = list(dict.fromkeys(out))
    return out + [a for a in members if a not in out]


class Turn:
    """一条她的消息引出的这一轮：谁接着说，AI @ 人时怎么插队。"""

    def __init__(self, order: list[str], members: list[str]):
        self.queue = list(order)
        self.members = list(members)
        self.spoken: list[str] = []
        self.relays = 0

    def next(self) -> str | None:
        if not self.queue:
            return None
        actor = self.queue.pop(0)
        self.spoken.append(actor)
        return actor

    def heard(self, speaker: str, text: str) -> list[str]:
        """speaker 刚说完 text。返回这次被他 @ 到、要提前 / 再说一次的人。"""
        moved: list[str] = []
        for actor in reversed(find_mentions(text, self.members)):
            if actor == speaker:
                continue
            if actor in self.queue:          # 本来就要说：提前，不算次数
                self.queue.remove(actor)
                self.queue.insert(0, actor)
                moved.append(actor)
            elif self.relays < MAX_RELAY:    # 已经说过 / 本来不在这轮：再说一次，算次数
                self.relays += 1
                self.queue.insert(0, actor)
                moved.append(actor)
        return list(reversed(moved))


def hint_for(actor_id: str) -> str:
    """群聊说明里给这位 AI 的一句：可以 @ 谁。"""
    try:
        import seat_chat
        members = ["aion", "connor"]
        for seat in seat_chat.SEAT_ACTORS:
            try:
                seat_chat.check_ready(seat)
                members.append(seat)
            except seat_chat.SeatNotReady:
                continue
        import actors
        return mention_hint([actors.display_name(a) for a in members if a != actor_id])
    except Exception:
        return ""


def mention_hint(names: list[str]) -> str:
    """群聊说明里加的一句：AI 知道可以 @ 人。"""
    if not names:
        return ""
    return (f"想请某位家人接你的话，就在句子里写「@名字」（比如 @{names[0]}），TA 会接着说；"
            "不需要就别 @，也不要替别人说话。")
