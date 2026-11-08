"""全家跑团（11-08 小猫）：中立 DM、每位 AI 自己决定、等她行动、骰子由程序掷。"""
import asyncio
import json

import pytest

import actors
import config
import family_trpg as t
import memory_hub_jobs
import world_window


@pytest.fixture
def table(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setitem(config.MODELS, "dm-model", {"provider": "custom_openai", "model": "m"})
    monkeypatch.setattr(actors, "list_actors", lambda include_disabled=True: [{"id": "aion", "name": "小克"}, {"id": "ai3", "name": "狗蛋"}])
    monkeypatch.setattr(memory_hub_jobs, "_persona_messages", lambda a: [])
    monkeypatch.setattr(world_window, "model_for", lambda a: f"model-{a}")
    monkeypatch.setattr(t, "_running", {})
    monkeypatch.setattr(t, "_chatting", set())
    calls = []
    script = {"fail": False, "ended": False, "changes": []}

    async def complete(model, messages):
        calls.append(model)
        last = messages[-1]["content"]
        if script["fail"]:
            raise t.GameError("模型这次没有回话")
        if model.startswith("model-"):
            return json.dumps({"action": f"{model} 推开门", "say": "走吧"})
        if "开一局" in last:
            return json.dumps({"title": "雪夜", "scenario": "真相", "narration": "大雪封山。"})
        if "给每个人定检定" in last:
            return json.dumps({"checks": [{"name": "小克", "stat": "dex", "dc": 99}, {"name": "狗蛋", "stat": "bad", "dc": 10}]})
        return json.dumps({"narration": "门开了。", "changes": script["changes"], "ended": script["ended"]})
    monkeypatch.setattr(t, "_complete", complete)
    return calls, script


def _new(rounds=3):
    return t.create(idea="雪夜旅馆", dm_model="dm-model", actors=["aion", "ai3"], my_name="阿猫", rounds=rounds)


def _advance(game):
    asyncio.run(t.advance(game["id"]))
    return t.load(game["id"])


async def _noop():
    return None


def _act(game, text, monkeypatch):
    monkeypatch.setattr(t, "start", lambda game_id: None)
    t.act(game["id"], text)
    return _advance(game)


def test_opening_then_each_ai_acts_with_its_own_model_then_waits_for_her(table):
    calls, _ = table
    game = _advance(_new())
    assert game["title"] == "雪夜" and game["round"] == 1 and game["phase"] == "wait_user"
    assert calls == ["model-aion", "model-ai3", "dm-model", "model-aion", "model-ai3"]  # 先各自捏人，再开场、行动
    assert game["actions"] == {"aion": "model-aion 推开门", "ai3": "model-ai3 推开门"}
    assert not any(e["kind"] == "roll" for e in game["log"])  # 等她，不自己往下走


def test_her_action_rolls_dice_clamps_dc_and_moves_on(table, monkeypatch):
    game = _advance(_new())
    monkeypatch.setattr(t.random, "randint", lambda a, b: 10)
    game = _act(game, "我点亮蜡烛", monkeypatch)
    assert game["round"] == 2 and game["phase"] == "wait_user" and game["checks"] == []
    rolls = [e for e in game["log"] if e["kind"] == "roll"]
    assert len(rolls) == 3 and "难度 18" in rolls[0]["text"]  # 99 被限制到 18
    assert "幸运" in rolls[1]["text"]  # 乱写的属性按幸运


def test_model_cannot_decide_the_dice(table, monkeypatch):
    game = _advance(_new())
    monkeypatch.setattr(t.random, "randint", lambda a, b: 1)
    game = _act(game, "翻窗", monkeypatch)
    assert all(e["text"].endswith("失败") for e in game["log"] if e["kind"] == "roll")


def test_retry_does_not_reroll(table, monkeypatch):
    _, script = table
    game = _advance(_new())
    values = iter([7, 8, 9, 20, 20, 20])
    monkeypatch.setattr(t.random, "randint", lambda a, b: next(values))
    real = t._complete
    count = {"n": 0}

    async def flaky(model, messages):
        if "这一轮的结果" in messages[-1]["content"] and count["n"] == 0:
            count["n"] = 1
            raise t.GameError("模型这次没有回话")
        return await real(model, messages)
    monkeypatch.setattr(t, "_complete", flaky)
    game = _act(game, "翻窗", monkeypatch)
    assert game["phase"] == "resolve" and game["error"]
    first = [c["die"] for c in game["checks"]]
    assert first == [7, 8, 9]
    game = _advance(game)
    assert game["error"] == "" and game["round"] == 2
    assert [e["text"].split("：")[1][:1] for e in game["log"] if e["kind"] == "roll"] == ["7", "8", "9"]


def test_failure_shows_error_and_can_continue(table):
    _, script = table
    script["fail"] = True
    game = _advance(_new())
    assert game["phase"] == "sheets" and game["error"]
    script["fail"] = False
    assert _advance(game)["phase"] == "wait_user"


def test_hp_changes_are_limited_and_items_tracked(table, monkeypatch):
    _, script = table
    script["changes"] = [{"name": "阿猫", "hp": -999, "gain": ["钥匙"]}, {"name": "小克", "hp": 999}, {"name": "路人", "hp": -5}]
    game = _advance(_new())
    game = _act(game, "翻窗", monkeypatch)
    assert game["players"]["user"]["hp"] == 100 - t.HP_LOSS and game["players"]["user"]["items"] == ["钥匙"]
    assert game["players"]["aion"]["hp"] == 100


def test_downed_ai_is_skipped(table):
    calls, _ = table
    game = _new()
    game["players"]["ai3"]["hp"] = 0
    game["phase"] = "opening"  # 跳过捏人，不然这张卡会被换掉
    t.save(game)
    game = _advance(game)
    assert "model-ai3" not in calls and "ai3" not in game["actions"]


def test_last_round_ends(table, monkeypatch):
    game = _advance(_new(rounds=3))
    game["round"] = 3
    t.save(game)
    game = _act(game, "收尾", monkeypatch)
    assert game["phase"] == "done"


def test_dm_can_end_early(table, monkeypatch):
    _, script = table
    script["ended"] = True
    game = _act(_advance(_new()), "收尾", monkeypatch)
    assert game["phase"] == "done"


@pytest.mark.parametrize("kwargs,word", [({"idea": ""}, "点子"), ({"my_name": ""}, "名字"), ({"dm_model": "nope"}, "模型"), ({"actors": ["evil"]}, "至少")])
def test_bad_setup(table, kwargs, word):
    base = dict(idea="x", dm_model="dm-model", actors=["aion"], my_name="猫")
    with pytest.raises(t.GameError, match=word):
        t.create(**{**base, **kwargs})


def test_cannot_act_out_of_turn(table):
    game = _new()
    with pytest.raises(t.GameError, match="还没轮到"):
        t.act(game["id"], "抢先")


@pytest.mark.parametrize("bad", ["../../x", "abc", ""])
def test_game_id_cannot_escape(table, bad):
    with pytest.raises(t.GameError):
        t.load(bad)


def test_chat_mention_picks_who_replies(table, monkeypatch):
    import group_mentions
    calls, _ = table
    monkeypatch.setattr(group_mentions, "names_for", lambda a: {"aion": ["小克"], "ai3": ["狗蛋"]}[a])
    game = _advance(_new())
    calls.clear()

    async def go():
        t.chat(game["id"], "@狗蛋 你怕吗")
        await asyncio.sleep(0)
        await asyncio.sleep(0.05)
    asyncio.run(go())
    assert calls == ["model-ai3"]
    log = t.load(game["id"])["log"]
    assert log[-2]["who"] == "user" and log[-1]["who"] == "ai3"


def test_list_and_delete(table):
    game = _new()
    assert t.list_games()[0]["id"] == game["id"]
    t.delete(game["id"])
    assert t.list_games() == []
