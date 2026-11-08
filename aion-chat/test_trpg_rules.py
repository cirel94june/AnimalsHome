"""跑团规则、捏人、升级、模组（11-08 小猫）。"""
import asyncio
import json
from pathlib import Path

import pytest

import actors
import config
import family_trpg as t
import memory_hub_jobs
import trpg_modules as modules
import trpg_rules as r
import world_window

DND = {"class": "盗贼", "stats": {"str": 8, "dex": 15, "con": 14, "int": 12, "wis": 10, "cha": 10}, "skills": ["隐匿", "巧手", "察觉", "欺瞒"]}
COC = {"stats": {"str": 50, "con": 60, "siz": 50, "dex": 60, "app": 60, "int": 60, "pow": 60, "edu": 60}, "skills": {"侦查": 40}}


def test_dnd_sheet():
    sheet = r.build_sheet("dnd", {**DND, "appearance": "银发"}, "阿猫")
    assert sheet["level"] == 1 and sheet["hp"] == 8 + 2 and sheet["appearance"] == "银发"


@pytest.mark.parametrize("change,word", [
    ({"stats": {**DND["stats"], "dex": 16}}, "8 到 15"),
    ({"stats": {k: 15 for k in DND["stats"]}}, "点数超了"),
    ({"class": "厨师"}, "职业"),
    ({"skills": ["隐匿"]}, "4 个技能"),
])
def test_dnd_rejects_cheating(change, word):
    with pytest.raises(r.SheetError, match=word):
        r.build_sheet("dnd", {**DND, **change}, "阿猫")


def test_coc_sheet():
    sheet = r.build_sheet("coc", COC, "阿猫")
    assert sheet["skills"]["侦查"] == 65 and sheet["san"] == 60 and sheet["hp"] == 11


@pytest.mark.parametrize("change,word", [
    ({"stats": {**COC["stats"], "str": 95}}, "15 到 90"),
    ({"stats": {k: 90 for k in COC["stats"]}}, "总和"),
    ({"skills": {"侦查": 80}}, "最高 90"),
    ({"skills": {"侦查": 60, "聆听": 70, "格斗": 65, "射击": 70, "急救": 60, "潜行": 70, "闪避": 60}}, "技能点超了"),
])
def test_coc_rejects_cheating(change, word):
    with pytest.raises(r.SheetError, match=word):
        r.build_sheet("coc", {**COC, **change}, "阿猫")


@pytest.mark.parametrize("stats", [{"str": 15, "dex": 0, "int": 0, "cha": 0, "lck": 0}, {"str": 3, "dex": 3, "int": 3, "cha": 3, "lck": 4}])
def test_simple_rejects_wrong_totals(stats):
    with pytest.raises(r.SheetError):
        r.build_sheet("simple", {"stats": stats}, "阿猫")


@pytest.mark.parametrize("rule", ["simple", "dnd", "coc"])
def test_random_sheets_are_always_legal(rule):
    for _ in range(40):
        assert r.random_sheet(rule, "x")["hp"] > 0


def _die(monkeypatch, value):
    monkeypatch.setattr(r.random, "randint", lambda a, b: value)


def test_dnd_proficiency_and_clamped_dc(monkeypatch):
    sheet = r.build_sheet("dnd", DND, "阿猫")
    _die(monkeypatch, 10)
    with_skill = r.roll_check("dnd", sheet, {"skill": "隐匿", "dc": 999})
    assert with_skill["total"] == 10 + 2 + 2 and with_skill["dc"] == 25 and not with_skill["success"]
    without = r.roll_check("dnd", sheet, {"skill": "杂技", "dc": 12})
    assert without["total"] == 12 and without["success"]


def test_dnd_natural_rolls(monkeypatch):
    sheet = r.build_sheet("dnd", DND, "阿猫")
    _die(monkeypatch, 20)
    assert r.roll_check("dnd", sheet, {"skill": "运动", "dc": 25})["success"]
    _die(monkeypatch, 1)
    assert not r.roll_check("dnd", sheet, {"skill": "隐匿", "dc": 1})["success"]


def test_coc_roll_under_and_difficulty(monkeypatch):
    sheet = r.build_sheet("coc", COC, "阿猫")
    _die(monkeypatch, 40)
    assert r.roll_check("coc", sheet, {"skill": "侦查", "difficulty": "regular"})["success"]
    hard = r.roll_check("coc", sheet, {"skill": "侦查", "difficulty": "hard"})
    assert hard["dc"] == 32 and not hard["success"]
    assert r.roll_check("coc", sheet, {"skill": "侦查", "difficulty": "extreme"})["dc"] == 13
    assert r.roll_check("coc", sheet, {"skill": "力量"})["used"] == ""  # 属性检定不算技能成长


def test_coc_sanity_can_take_you_out():
    sheet = r.build_sheet("coc", COC, "阿猫")
    sheet["san"] = 3
    r.apply_change("coc", sheet, {"san": -999})
    assert sheet["san"] == 0 and not r.alive(sheet)


def test_hp_limit_scales_with_small_hp():
    sheet = r.build_sheet("dnd", DND, "阿猫")
    r.apply_change("dnd", sheet, {"hp": -999})
    assert sheet["hp"] == 6  # 10 点血，一次最多扣 4


def test_dnd_levels_every_three_rounds_and_learns():
    sheet = r.build_sheet("dnd", DND, "阿猫")
    assert r.grow("dnd", sheet, 2) == ""
    assert "升到 2 级" in r.grow("dnd", sheet, 3) and sheet["pending_skills"] == 1 and sheet["max_hp"] > 10
    assert r.learn("dnd", sheet, "运动") == "运动" and "运动" in sheet["skills"] and sheet["pending_skills"] == 0
    assert r.learn("dnd", sheet, "杂技") == ""  # 没有名额


def test_learning_an_invalid_skill_picks_a_new_one():
    sheet = r.build_sheet("dnd", DND, "阿猫")
    sheet["pending_skills"] = 1
    learned = r.learn("dnd", sheet, "隐匿")  # 已经会了
    assert learned in r.DND_SKILLS and learned != "隐匿"


def test_coc_used_skills_can_improve(monkeypatch):
    sheet = r.build_sheet("coc", COC, "阿猫")
    r.note_success("coc", sheet, {"success": True, "used": "侦查"})
    _die(monkeypatch, 99)
    assert "侦查+" in r.grow("coc", sheet, 3) and sheet["skills"]["侦查"] == 90 and sheet["used"] == []


# ── 一局 ──

@pytest.fixture
def table(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setitem(config.MODELS, "dm-model", {"provider": "custom_openai", "model": "m"})
    monkeypatch.setattr(actors, "list_actors", lambda include_disabled=True: [{"id": "aion", "name": "小克"}, {"id": "ai3", "name": "狗蛋"}])
    monkeypatch.setattr(memory_hub_jobs, "_persona_messages", lambda a: [])
    monkeypatch.setattr(world_window, "model_for", lambda a: f"model-{a}")
    monkeypatch.setattr(t, "_running", {})
    seen = []
    sheets = {"model-aion": COC, "model-ai3": {"stats": {"str": 99}}}

    async def complete(model, messages):
        last = messages[-1]["content"]
        seen.append((model, messages[0]["content"] if messages else "", last))
        if "捏" in last:
            return json.dumps(sheets[model])
        if model.startswith("model-"):
            return json.dumps({"action": "搜查", "say": ""})
        if "开一局" in last:
            return json.dumps({"title": "T", "scenario": "s", "narration": "开场"})
        if "给每个人定检定" in last:
            return json.dumps({"checks": [{"name": "小克", "skill": "侦查", "difficulty": "regular"}]})
        return json.dumps({"narration": "结果", "changes": [{"name": "小克", "san": -3}], "ended": False})
    monkeypatch.setattr(t, "_complete", complete)
    return seen


def test_coc_game_ais_build_their_own_and_bad_sheets_are_replaced(table):
    game = t.create(idea="x", dm_model="dm-model", actors=["aion", "ai3"], my_name="阿猫", ruleset="coc", sheet=COC)
    asyncio.run(t.advance(game["id"]))
    game = t.load(game["id"])
    assert game["phase"] == "wait_user" and game["players"]["aion"]["skills"]["侦查"] == 65
    assert any("换成了随机" in e["text"] for e in game["log"])


def test_her_sheet_must_be_legal_for_dnd_and_coc(table):
    with pytest.raises(t.GameError, match="捏好"):
        t.create(idea="x", dm_model="dm-model", actors=["aion"], my_name="猫", ruleset="dnd")
    with pytest.raises(t.GameError, match="点数超了"):
        t.create(idea="x", dm_model="dm-model", actors=["aion"], my_name="猫", ruleset="dnd",
                 sheet={**DND, "stats": {k: 15 for k in DND["stats"]}})


def test_module_goes_to_the_dm_only(table):
    game = t.create(idea="", dm_model="dm-model", actors=["aion"], my_name="猫", ruleset="coc", sheet=COC, module_id="builtin-snow-inn")
    asyncio.run(t.advance(game["id"]))
    game = t.load(game["id"])
    secret = "十年前的雪崩"
    assert game["module_text"] and secret in game["module_text"]
    dm_calls = [s for s in table if s[0] == "dm-model"]
    assert all(secret in c[1] for c in dm_calls)
    assert not any(secret in c[1] + c[2] for c in table if c[0].startswith("model-"))
    shown = json.dumps(t.public(game), ensure_ascii=False)
    assert secret not in shown and "module_text" not in shown and '"scenario"' not in shown


def test_unknown_module(table):
    with pytest.raises(t.GameError, match="模组"):
        t.create(idea="x", dm_model="dm-model", actors=["aion"], my_name="猫", module_id="nope")


def test_my_modules_add_list_delete_without_leaking_text(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    item = modules.add("我的模组", "coc", "秘密真相")
    listed = modules.listing()
    assert any(m["id"] == item["id"] and m["mine"] for m in listed) and "秘密真相" not in json.dumps(listed, ensure_ascii=False)
    assert modules.get(item["id"])["text"] == "秘密真相"
    assert modules.remove(item["id"]) and modules.get(item["id"]) is None
    assert modules.remove("builtin-snow-inn") is False


@pytest.mark.parametrize("title,rule,text", [("", "coc", "x"), ("a", "bad", "x"), ("a", "coc", "x" * 40001)],
                         ids=["no-title", "bad-rule", "too-long"])
def test_bad_modules(tmp_path, monkeypatch, title, rule, text):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with pytest.raises(ValueError):
        modules.add(title, rule, text)


def test_builtin_modules_match_rules():
    assert {m["rule"] for m in modules.BUILTIN} == {"coc", "dnd"} and len(modules.BUILTIN) == 4


def test_page_has_builder():
    html = (Path(__file__).parent / "static" / "trpg.html").read_text(encoding="utf-8")
    assert "collectSheet" in html and 'id="m-add"' in html and 'id="learn"' in html
