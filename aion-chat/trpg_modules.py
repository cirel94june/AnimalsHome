"""跑团模组库（11-08 小猫）：几个原创短模组，加她自己粘贴或上传的。

内容只交给 DM，玩家（包括 AI 和页面）看不到。她自己放的模组只存在服务器 data/ 里，不进仓库。
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from typing import Any

MAX_TEXT = 40000
_lock = threading.Lock()

BUILTIN = [
    {"id": "builtin-snow-inn", "title": "雪线之上的第七个房间", "rule": "coc", "hook": "大雪封山，山顶旅馆多出来一个登记簿上没有的房间。", "text": """【开场】调查员因各自的理由住进雪线旅馆。第一晚大雪封路，电话不通。老板说旅馆只有六间客房，但二楼走廊尽头有第七扇门。
【真相】十年前的雪崩埋了旅馆的旧翼，当时住在七号房的少女没被找到。老板一家隐瞒了这件事继续营业。第七扇门只在雪夜出现，门后是十年前的那个夜晚；每次有人开门，少女就更清醒一点，也会有一位住客第二天「变成」十年前的遇难者（忘记现在，只记得那晚）。
【线索】登记簿被撕掉的一页（图书馆）；老板妻子夜里给走廊放一杯热牛奶（侦查/心理学）；墙里传来敲击声，节奏是求救信号（聆听）；旧翼地基的裂缝里有童鞋（侦查）。
【转折】第二晚有一位住客开始只说十年前的事。
【结局】把少女的遗物从旧翼带出来交给她，房间消失，雪停（好结局）；烧掉旅馆可以脱身但她留在雪里（坏结局）；全员在房间过夜则一起成为那晚的遇难者。
【理智】看到门后的那晚 -3；发现住客换了记忆 -5。"""},
    {"id": "builtin-lighthouse", "title": "不再转动的灯塔", "rule": "coc", "hook": "渔村的灯塔停了三天，守塔人留下一本写满同一句话的日记。", "text": """【开场】调查员受托来到小渔村，灯塔停转三天，守塔人失踪，渔船接连在无风的夜里失踪。
【真相】守塔人发现塔灯其实是在「压着」海底的东西：灯光规律是一种镇压的节奏。他的女儿病重，有人告诉他关灯就能换回女儿，他照做了。那人是村里的教师，崇拜海底的存在。
【线索】日记里重复的那句话倒着读是一个日期（图书馆/神秘学）；灯塔齿轮被人为卸下一枚（侦查/机械常识）；教师家的地下室有湿脚印通向海边（侦查）；女儿的病在关灯后真的好了，但她不再眨眼（心理学）。
【转折】第三晚潮水不退，村民开始朝海里走。
【结局】装回齿轮重启灯塔，女儿会倒下（代价）；不修灯塔，整个村子入海；揭穿教师可以争取时间。
【理智】看到不眨眼的女儿 -3；看到海面下的轮廓 -8。"""},
    {"id": "builtin-goblin-bell", "title": "偷走钟声的地精", "rule": "dnd", "hook": "镇子的大钟再也不响了，地精们却在山洞里敲得很开心。", "text": """【开场】河湾镇的报时钟舌被偷，镇长悬赏。线索指向北边的矿洞。
【真相】地精并不想作恶。矿洞深处有一只沉睡的土元素，钟声能安抚它。镇上的钟声传不到矿洞后它开始躁动，地精才偷钟舌自己敲。真正的麻烦是矿主为了挖更深，故意震醒它。
【场景】矿洞入口的绊索陷阱（察觉/巧手）；地精哨兵（可战斗，也可说服）；会塌的木桥（杂技/运动）；地精祭司能解释一切（说服/洞悉）；最深处的土元素（强敌，可用钟声安抚）。
【转折】矿主雇的打手追进矿洞，要抢钟舌。
【结局】合作把钟装进矿洞并揭穿矿主（最好）；打败地精拿回钟舌，几天后地震毁镇（坏）。
【奖励】矿主的账本、矮人造的提灯、一枚能让元素安静的钟舌碎片。"""},
    {"id": "builtin-mirror-tower", "title": "镜塔试炼", "rule": "dnd", "hook": "每层都有一面镜子，镜子里的你比你更会打架。", "text": """【开场】法师公会招募冒险者取回塔顶的星图，给的报酬高得可疑。
【真相】塔是前任大法师的试炼：每层的镜像是挑战者的倒影，打败倒影的人会失去相应的一段记忆。公会想要的不是星图，而是用冒险者的记忆去喂养塔，让大法师复活。
【场景】第一层：镜像模仿你的动作（用不寻常的行动才能赢，调查/欺瞒）；第二层：镜像说出你的秘密（说服/威吓/表演）；第三层：地面是镜面，走错就掉进反面（杂技/奥秘）；塔顶：星图和大法师残影。
【转折】玩家发现自己忘了某件事，而日记上写着自己来过这里。
【结局】打碎核心镜放出被吞的记忆（好）；带走星图，公会得逞（坏）；和残影交易换一个愿望（代价自定）。"""},
]


def _path():
    from config import DATA_DIR
    return DATA_DIR / "trpg_modules.json"


def _mine() -> list[dict[str, Any]]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        return []


def listing() -> list[dict[str, Any]]:
    """给页面：不带正文，免得剧透。"""
    rows = [{"id": m["id"], "title": m["title"], "rule": m["rule"], "hook": m["hook"], "mine": False} for m in BUILTIN]
    return rows + [{"id": m["id"], "title": m["title"], "rule": m["rule"], "hook": "", "mine": True} for m in _mine()]


def get(module_id: str) -> dict[str, Any] | None:
    return next((m for m in [*BUILTIN, *_mine()] if m["id"] == module_id), None)


def add(title: Any, rule: Any, text: Any) -> dict[str, Any]:
    title, text = str(title or "").strip()[:40], str(text or "").strip()
    if not title or not text:
        raise ValueError("要有标题和模组内容")
    if rule not in ("any", "simple", "dnd", "coc"):
        raise ValueError("规则不对")
    if len(text) > MAX_TEXT:
        raise ValueError(f"模组太长了，最多 {MAX_TEXT} 字")
    item = {"id": uuid.uuid4().hex[:16], "title": title, "rule": rule, "text": text, "at": time.time()}
    with _lock:
        rows = _mine()
        rows.append(item)
        _path().write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return {k: item[k] for k in ("id", "title", "rule")}


def remove(module_id: str) -> bool:
    if not re.fullmatch(r"[a-f0-9]{16}", module_id or ""):
        return False
    with _lock:
        rows = _mine()
        left = [m for m in rows if m["id"] != module_id]
        if len(left) == len(rows):
            return False
        _path().write_text(json.dumps(left, ensure_ascii=False), encoding="utf-8")
    return True
