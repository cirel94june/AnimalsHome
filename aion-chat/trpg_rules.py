"""跑团规则（11-08 小猫）：简易、DnD 风、CoC 风。只实现核心的检定和数值，没有搬任何规则书的文字。

检定都由程序掷骰。这里校验角色卡（点数不能超），AI 写的卡不合规就换一张随机的。
"""

from __future__ import annotations

import random
from typing import Any

HP_LOSS, HP_GAIN = 40, 30
GROW_EVERY = 3     # 每 3 轮成长一次
MAX_LEVEL = 10


class SheetError(ValueError):
    """给小猫看的话。"""


SIMPLE_STATS = {"str": "力量", "dex": "敏捷", "int": "智力", "cha": "魅力", "lck": "幸运"}

DND_STATS = {"str": "力量", "dex": "敏捷", "con": "体质", "int": "智力", "wis": "感知", "cha": "魅力"}
DND_COST = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
DND_POINTS = 27
DND_CLASSES = {"战士": {"hit": 10, "picks": 2}, "游侠": {"hit": 10, "picks": 3}, "盗贼": {"hit": 8, "picks": 4},
               "吟游诗人": {"hit": 8, "picks": 3}, "牧师": {"hit": 8, "picks": 2}, "法师": {"hit": 6, "picks": 2}}
DND_SKILLS = {"运动": "str", "杂技": "dex", "隐匿": "dex", "巧手": "dex", "奥秘": "int", "历史": "int", "调查": "int", "自然": "int",
              "宗教": "int", "驯兽": "wis", "洞悉": "wis", "医药": "wis", "察觉": "wis", "生存": "wis", "欺瞒": "cha", "威吓": "cha",
              "表演": "cha", "说服": "cha"}

COC_STATS = {"str": "力量", "con": "体质", "siz": "体型", "dex": "敏捷", "app": "外貌", "int": "智力", "pow": "意志", "edu": "教育"}
COC_POOL = 460
COC_MIN, COC_MAX = 15, 90
COC_SKILLS = {"侦查": 25, "图书馆": 20, "聆听": 20, "心理学": 10, "说服": 10, "话术": 5, "潜行": 20, "闪避": 25, "急救": 30, "医学": 1,
              "神秘学": 5, "历史": 5, "格斗": 25, "射击": 20, "驾驶": 20, "开锁": 1}
COC_DIFFICULTY = {"regular": ("普通", 1), "hard": ("困难", 2), "extreme": ("极难", 5)}

RULES = {
    "simple": {"name": "简易", "desc": "五项属性，20 面骰，上手最快"},
    "dnd": {"name": "DnD 风", "desc": "职业、等级、六项属性，20 面骰，会升级学技能"},
    "coc": {"name": "CoC 风", "desc": "技能百分比，100 面骰，有理智值，适合调查和恐怖"},
}


def describe() -> dict[str, Any]:
    """给页面捏人用。"""
    return {
        "rules": RULES,
        "simple": {"stats": SIMPLE_STATS, "total": 15, "min": 1, "max": 10},
        "dnd": {"stats": DND_STATS, "points": DND_POINTS, "cost": DND_COST, "classes": DND_CLASSES, "skills": DND_SKILLS},
        "coc": {"stats": COC_STATS, "pool": COC_POOL, "min": COC_MIN, "max": COC_MAX, "skills": COC_SKILLS},
    }


def stat_names(rule: str) -> dict[str, str]:
    return {"dnd": DND_STATS, "coc": COC_STATS}.get(rule, SIMPLE_STATS)


def mod(score: int) -> int:
    return (score - 10) // 2


def _ints(raw: Any, keys: dict[str, str]) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise SheetError("属性没填")
    out = {}
    for key in keys:
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise SheetError(f"{keys[key]}没填")
        try:
            out[key] = int(value)
        except ValueError:
            raise SheetError(f"{keys[key]}要填数字") from None
    return out


def _base(name: str, raw: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "character": str(raw.get("character") or "").strip()[:300],
            "appearance": str(raw.get("appearance") or "").strip()[:200], "items": []}


def build_sheet(rule: str, raw: Any, name: str) -> dict[str, Any]:
    """按规则校验一张角色卡。不合规抛 SheetError。"""
    raw = raw if isinstance(raw, dict) else {}
    sheet = _base(name, raw)
    if rule == "dnd":
        stats = _ints(raw.get("stats"), DND_STATS)
        if any(v not in DND_COST for v in stats.values()):
            raise SheetError("每项属性要在 8 到 15 之间")
        if sum(DND_COST[v] for v in stats.values()) > DND_POINTS:
            raise SheetError(f"点数超了，一共只有 {DND_POINTS} 点")
        klass = raw.get("class")
        if klass not in DND_CLASSES:
            raise SheetError("选一个职业")
        skills = list(dict.fromkeys(s for s in raw.get("skills") or [] if s in DND_SKILLS))
        if len(skills) != DND_CLASSES[klass]["picks"]:
            raise SheetError(f"{klass}要选 {DND_CLASSES[klass]['picks']} 个技能")
        hp = max(1, DND_CLASSES[klass]["hit"] + mod(stats["con"]))
        return {**sheet, "stats": stats, "class": klass, "level": 1, "skills": skills, "pending_skills": 0, "hp": hp, "max_hp": hp}
    if rule == "coc":
        stats = _ints(raw.get("stats"), COC_STATS)
        if any(v < COC_MIN or v > COC_MAX for v in stats.values()):
            raise SheetError(f"每项属性要在 {COC_MIN} 到 {COC_MAX} 之间")
        if sum(stats.values()) > COC_POOL:
            raise SheetError(f"属性总和不能超过 {COC_POOL}")
        pool = stats["edu"] * 2 + stats["int"] * 2
        given = raw.get("skills") if isinstance(raw.get("skills"), dict) else {}
        skills, spent = dict(COC_SKILLS), 0
        for key, value in given.items():
            if key not in COC_SKILLS:
                continue
            try:
                added = int(value)
            except (TypeError, ValueError):
                raise SheetError(f"{key}要填数字") from None
            if added < 0 or COC_SKILLS[key] + added > 90:
                raise SheetError(f"{key}最高 90")
            skills[key] += added
            spent += added
        if spent > pool:
            raise SheetError(f"技能点超了，一共只有 {pool} 点")
        hp = max(1, (stats["con"] + stats["siz"]) // 10)
        return {**sheet, "stats": stats, "skills": skills, "used": [], "hp": hp, "max_hp": hp, "san": stats["pow"], "max_san": stats["pow"]}
    stats = _ints(raw.get("stats"), SIMPLE_STATS)
    if any(v < 1 or v > 10 for v in stats.values()):
        raise SheetError("每项属性要在 1 到 10 之间")
    if sum(stats.values()) != 15:
        raise SheetError("五项属性加起来要正好 15")
    return {**sheet, "stats": stats, "hp": 100, "max_hp": 100}


def random_sheet(rule: str, name: str, raw: Any = None) -> dict[str, Any]:
    """随机一张合规的卡（简易规则开局、AI 写错时用）。"""
    extra = {k: v for k, v in (raw if isinstance(raw, dict) else {}).items() if k in ("character", "appearance")}
    if rule == "dnd":
        stats, left = {k: 8 for k in DND_STATS}, DND_POINTS
        while True:
            options = [k for k in stats if stats[k] < 15 and DND_COST[stats[k] + 1] - DND_COST[stats[k]] <= left]
            if not options:
                break
            key = random.choice(options)
            left -= DND_COST[stats[key] + 1] - DND_COST[stats[key]]
            stats[key] += 1
        klass = random.choice(list(DND_CLASSES))
        return build_sheet(rule, {**extra, "stats": stats, "class": klass, "skills": random.sample(list(DND_SKILLS), DND_CLASSES[klass]["picks"])}, name)
    if rule == "coc":
        stats = {k: 50 for k in COC_STATS}
        for _ in range(60):
            up, down = random.sample(list(COC_STATS), 2)
            if stats[up] < 80 and stats[down] > 30:
                stats[up] += 5
                stats[down] -= 5
        stats = {k: min(COC_MAX, v) for k, v in stats.items()}
        pool, picks = stats["edu"] * 2 + stats["int"] * 2, random.sample(list(COC_SKILLS), 6)
        each = min(45, pool // 6)
        return build_sheet(rule, {**extra, "stats": stats, "skills": {k: each for k in picks}}, name)
    stats = {k: 1 for k in SIMPLE_STATS}
    for _ in range(10):
        key = random.choice([k for k in stats if stats[k] < 10])
        stats[key] += 1
    return build_sheet(rule, {**extra, "stats": stats}, name)


def sheet_prompt(rule: str) -> str:
    """让 AI 给自己捏人时的说明和 JSON 格式。"""
    if rule == "dnd":
        return ("这局用 DnD 风规则。给自己捏一个角色：六项属性 str/dex/con/int/wis/cha 每项 8 到 15，购点一共 27 点"
                f"（价格 {DND_COST}）。职业和可选技能数：{ {k: v['picks'] for k, v in DND_CLASSES.items()} }。技能从这里选：{'、'.join(DND_SKILLS)}。\n"
                '输出 JSON：{"class":"职业","stats":{"str":10,"dex":14,"con":12,"int":10,"wis":13,"cha":12},"skills":["隐匿"],"appearance":"外貌一句话","character":"性格和来历一句话"}')
    return (f"这局用 CoC 风规则。给自己捏一个调查员：八项属性 {COC_STATS} 每项 {COC_MIN} 到 {COC_MAX}，总和不超过 {COC_POOL}。"
            f"技能点总数 = 教育×2 + 智力×2，加到这些技能上（括号里是初始值，单项最高 90）：{COC_SKILLS}。\n"
            '输出 JSON：{"stats":{"str":50,"con":50,"siz":50,"dex":60,"app":60,"int":70,"pow":60,"edu":70},"skills":{"侦查":40,"心理学":40},"appearance":"外貌一句话","character":"职业和来历一句话"}')


def sheet_line(rule: str, p: dict[str, Any]) -> str:
    names = stat_names(rule)
    stats = " ".join(f"{names[k]}{v}" for k, v in p["stats"].items())
    head = f"{p['name']}（HP {p['hp']}/{p['max_hp']}"
    if rule == "dnd":
        head += f"，{p['class']} {p['level']} 级，熟练：{'、'.join(p['skills'])}"
    if rule == "coc":
        top = sorted(p["skills"].items(), key=lambda kv: -kv[1])[:6]
        head += f"，理智 {p['san']}/{p['max_san']}，" + "、".join(f"{k}{v}" for k, v in top)
    line = f"{head}，{stats}，道具：{'、'.join(p['items']) or '无'}）"
    for text in (p.get("appearance"), p.get("character")):
        if text:
            line += f"：{text}"
    return line


def check_prompt(rule: str) -> str:
    if rule == "dnd":
        return f'skill 从这些选：{"、".join(DND_SKILLS)}；dc 5 到 25。JSON：{{"checks":[{{"name":"角色名","skill":"隐匿","dc":13}}]}}'
    if rule == "coc":
        return f'skill 从这些选：{"、".join(COC_SKILLS)}，或者属性 {"、".join(COC_STATS.values())}；difficulty 是 regular/hard/extreme。JSON：{{"checks":[{{"name":"角色名","skill":"侦查","difficulty":"regular"}}]}}'
    return f'stat 只能是 {"/".join(SIMPLE_STATS)}，dc 在 8 到 18 之间。JSON：{{"checks":[{{"name":"角色名","stat":"dex","dc":12}}]}}'


def _clamp_dc(value: Any, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def roll_check(rule: str, p: dict[str, Any], given: dict[str, Any]) -> dict[str, Any]:
    """程序掷骰。模型只能给「查什么、多难」，给错了用默认值。"""
    if rule == "dnd":
        skill = given.get("skill") if given.get("skill") in DND_SKILLS else "察觉"
        dc = _clamp_dc(given.get("dc"), 5, 25, 13)
        die = random.randint(1, 20)
        prof = (2 + (p["level"] - 1) // 4) if skill in p["skills"] else 0
        bonus = mod(p["stats"][DND_SKILLS[skill]]) + prof
        total = die + bonus
        success = die == 20 or (die != 1 and total >= dc)
        return {"die": die, "total": total, "dc": dc, "success": success, "used": skill,
                "text": f"{skill}检定：{die} {bonus:+d} = {total}（难度 {dc}）{'成功' if success else '失败'}"}
    if rule == "coc":
        wanted = given.get("skill")
        by_name = {v: k for k, v in COC_STATS.items()}
        if wanted in p["skills"]:
            base, used = p["skills"][wanted], wanted
        elif wanted in by_name:
            base, used = p["stats"][by_name[wanted]], ""
        else:
            wanted, base, used = "侦查", p["skills"]["侦查"], "侦查"
        label, divide = COC_DIFFICULTY.get(given.get("difficulty"), COC_DIFFICULTY["regular"])
        target = max(1, base // divide)
        die = random.randint(1, 100)
        success = die <= target and die != 100
        return {"die": die, "total": die, "dc": target, "success": success, "used": used,
                "text": f"{wanted}检定（{label}）：掷 {die}，需要 ≤{target}，{'成功' if success else '失败'}"}
    stat = given.get("stat") if given.get("stat") in SIMPLE_STATS else "lck"
    dc = _clamp_dc(given.get("dc"), 8, 18, 12)
    die = random.randint(1, 20)
    bonus = p["stats"][stat] // 2
    total = die + bonus
    success = total >= dc
    return {"die": die, "total": total, "dc": dc, "success": success, "used": "",
            "text": f"{SIMPLE_STATS[stat]}检定：{die} + {bonus} = {total}（难度 {dc}）{'成功' if success else '失败'}"}


def note_success(rule: str, p: dict[str, Any], result: dict[str, Any]) -> None:
    if rule == "coc" and result["success"] and result["used"] and result["used"] not in p["used"]:
        p["used"].append(result["used"])


def apply_change(rule: str, p: dict[str, Any], change: dict[str, Any]) -> None:
    def number(key: str) -> int:
        try:
            return int(change.get(key) or 0)
        except (TypeError, ValueError):
            return 0
    # DnD、CoC 血量少：DM 给的增减按血量上限比例封顶，一下打不死
    scale = p["max_hp"] / 100
    p["hp"] = max(0, min(p["max_hp"], p["hp"] + max(-max(1, round(HP_LOSS * scale)), min(max(1, round(HP_GAIN * scale)), number("hp")))))
    if rule == "coc":
        p["san"] = max(0, min(p["max_san"], p["san"] + max(-15, min(5, number("san")))))


def alive(p: dict[str, Any]) -> bool:
    return p["hp"] > 0 and p.get("san", 1) > 0


def grow(rule: str, p: dict[str, Any], round_no: int) -> str:
    """每 3 轮成长。返回给牌桌看的一句话，没变化就是空串。"""
    if round_no % GROW_EVERY or not alive(p):
        return ""
    if rule == "dnd" and p["level"] < MAX_LEVEL:
        gain = max(1, DND_CLASSES[p["class"]]["hit"] // 2 + 1 + mod(p["stats"]["con"]))
        p.update(level=p["level"] + 1, max_hp=p["max_hp"] + gain, hp=p["hp"] + gain, pending_skills=p["pending_skills"] + 1)
        return f"{p['name']} 升到 {p['level']} 级，生命 +{gain}，可以学一个新技能。"
    if rule == "coc" and p["used"]:
        grown = []
        for skill in p["used"]:
            if random.randint(1, 100) > p["skills"][skill]:
                add = random.randint(1, 10)
                p["skills"][skill] = min(90, p["skills"][skill] + add)
                grown.append(f"{skill}+{add}")
        p["used"] = []
        return f"{p['name']} 的经验：{'、'.join(grown)}。" if grown else ""
    return ""


def learn(rule: str, p: dict[str, Any], skill: Any) -> str:
    """DnD 升级学技能。不合法或学过就随便挑一个没学的。返回学到的技能。"""
    if rule != "dnd" or p.get("pending_skills", 0) <= 0:
        return ""
    options = [s for s in DND_SKILLS if s not in p["skills"]]
    if not options:
        p["pending_skills"] = 0
        return ""
    chosen = skill if skill in options else random.choice(options)
    p["skills"].append(chosen)
    p["pending_skills"] -= 1
    return chosen
