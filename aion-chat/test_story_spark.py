import asyncio

import pytest

import story_spark


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(story_spark, "DB_PATH", tmp_path / "s.db")


SEED = [{"name": "小猫", "text": "灵感"}]


def run_story(monkeypatch, replies, speakers):
    calls = []

    async def speak(prompt, model, persona="", name=""):
        calls.append({"prompt": prompt, "model": model, "persona": persona, "name": name})
        reply = replies[len(calls) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(story_spark, "_speak", speak)
    story_spark.insert_story("a", "x", "", SEED)
    asyncio.run(story_spark.write_story("a", speakers, SEED, "", "小猫"))
    return story_spark.get_story("a"), calls


def speakers(*names):
    return [{"id": n, "name": n, "model": "m-" + n, "persona": "p-" + n} for n in names]


def test_single_narrator_title_and_status(monkeypatch):
    story, calls = run_story(monkeypatch, ["标题：雪夜\n正文"], speakers("狗蛋"))
    assert story["status"] == "done" and story["title"] == "雪夜"
    assert story["segments"] == [{"name": "狗蛋", "text": "正文", "sid": "狗蛋", "model": "m-狗蛋"}]
    assert calls[0]["model"] == "m-狗蛋" and calls[0]["persona"] == "p-狗蛋"


def test_relay_passes_story_along_and_only_last_ends(monkeypatch):
    story, calls = run_story(monkeypatch, ["标题：接力\n第一段", "第二段", "第三段"], speakers("甲", "乙", "丙"))
    assert [s["name"] for s in story["segments"]] == ["甲", "乙", "丙"]
    assert "第一段" in calls[1]["prompt"] and "第二段" in calls[2]["prompt"]
    assert "不要收尾" in calls[1]["prompt"] and "收尾" in calls[2]["prompt"] and "不要收尾" not in calls[2]["prompt"]


def test_failure_keeps_finished_part(monkeypatch):
    story, _ = run_story(monkeypatch, ["第一段", RuntimeError("secret-url")], speakers("甲", "乙"))
    assert story["status"] == "partial" and len(story["segments"]) == 1
    assert "secret" not in story["error"]


def test_total_failure(monkeypatch):
    story, _ = run_story(monkeypatch, [RuntimeError("x")], speakers("甲"))
    assert story["status"] == "failed" and story["segments"] == []


def test_pick_seed_uses_only_chosen_messages_in_order():
    source = [{"id": str(i), "name": "n", "text": f"t{i}"} for i in range(5)]
    assert [m["text"] for m in story_spark.pick_seed(source, ["3", "1", "99"])] == ["t1", "t3"]


def test_stories_are_kept_and_deletable():
    story_spark.insert_story("a", "x", "", SEED, now=1)
    story_spark.insert_story("b", "x", "", SEED, now=2)
    assert [s["id"] for s in story_spark.list_stories()] == ["b", "a"]
    assert story_spark.delete_story("a") and not story_spark.get_story("a")


def test_module_never_touches_hub_or_chat_tables():
    src = (story_spark.BASE_DIR / "story_spark.py").read_text(encoding="utf-8")
    assert "INSERT INTO messages" not in src and "INSERT INTO chatroom" not in src


def test_builtin_seats_use_own_model_and_persona(monkeypatch):
    import actors
    import chatroom
    import config
    import memory_hub_jobs
    monkeypatch.setattr(actors, "get_actor", lambda a: {"enabled": True, "name": a})
    monkeypatch.setattr(actors, "display_name", lambda a: "名" + a)
    monkeypatch.setattr(memory_hub_jobs, "own_model", lambda a: "m-" + a)
    monkeypatch.setattr(config, "load_worldbook", lambda: {"ai_persona": "小克人设"})
    monkeypatch.setattr(chatroom, "_read_connor_persona", lambda: "L人设")
    assert story_spark._seat("aion") == {"id": "aion", "name": "名aion", "model": "m-aion", "persona": "小克人设"}
    assert story_spark._seat("connor")["persona"] == "L人设"


def test_builtin_seat_without_model_cannot_narrate(monkeypatch):
    import actors
    import memory_hub_jobs
    monkeypatch.setattr(actors, "get_actor", lambda a: {"enabled": True, "name": a})
    monkeypatch.setattr(memory_hub_jobs, "own_model", lambda a: "")
    with pytest.raises(Exception):
        story_spark._seat("aion")


def test_comments_extras_and_cleanup():
    story_spark.insert_story("a", "x", "", SEED)
    story_spark.add_comment("a", 0, 1, "小猫", "好看")
    assert story_spark.mark_remembered("a", "aion", "n") is True
    assert story_spark.mark_remembered("a", "aion", "n") is False
    extras = story_spark.story_extras("a")
    assert extras["comments"][0]["who"] == "小猫" and extras["remembered"] == ["aion"]
    story_spark.delete_story("a")
    assert story_spark.story_extras("a") == {"comments": [], "remembered": [], "lore": [], "chat": []}


def test_author_replies_but_not_to_self(monkeypatch):
    story_spark.insert_story("a", "x", "", SEED)
    story = {"id": "a", "title": "雪夜", "segments": [{"name": "狗蛋", "text": "第一句\n第二句", "sid": "ai3"}]}
    monkeypatch.setattr(story_spark, "_seat", lambda s: {"id": s, "name": "狗蛋", "model": "m", "persona": ""})
    prompts = []

    async def speak(prompt, model, persona="", name=""):
        prompts.append(prompt)
        return "谢谢"
    monkeypatch.setattr(story_spark, "_speak", speak)
    asyncio.run(story_spark._author_reply(story, 0, 1, "小猫", "喜欢", "小猫"))
    asyncio.run(story_spark._author_reply(story, 0, 1, "狗蛋", "自评", "小猫"))
    assert len(prompts) == 1 and "第二句" in prompts[0]
    assert [c["who"] for c in story_spark.story_extras("a")["comments"]] == ["狗蛋"]


def test_remember_job_marks_only_when_queued(monkeypatch):
    import memory_hub_bridge as bridge
    story_spark.insert_story("a", "x", "", SEED)
    story = {"id": "a", "title": "雪夜", "segments": [{"name": "狗蛋", "text": "正文"}]}
    seat = {"id": "ai3", "name": "狗蛋", "model": "m", "persona": ""}
    monkeypatch.setattr(bridge, "can_remember", lambda a: True)

    async def speak(prompt, model, persona="", name=""):
        return "一个雪夜的故事，我很安静"
    monkeypatch.setattr(story_spark, "_speak", speak)
    sent = []
    monkeypatch.setattr(bridge, "schedule_remember", lambda actor, items, request_key, outcome: sent.append(items) or [])
    asyncio.run(story_spark.remember_job(story, seat, "小猫"))
    assert not story_spark.is_remembered("a", "ai3")
    assert sent[0][0]["fiction"] is True and "room" not in sent[0][0]
    monkeypatch.setattr(bridge, "schedule_remember", lambda actor, items, request_key, outcome: ["ok"])
    asyncio.run(story_spark.remember_job(story, seat, "小猫"))
    assert story_spark.is_remembered("a", "ai3")


STORY = {"id": "a", "title": "雪夜", "status": "done", "segments": [
    {"name": "甲", "text": "一\n二", "sid": "aion"}, {"name": "乙", "text": "三", "sid": "ai3"}]}


def test_only_narrators_know_the_story():
    seats = [{"id": "aion", "name": "甲"}, {"id": "ai3", "name": "乙"}, {"id": "connor", "name": "丙"}]
    assert story_spark.audience(STORY, seats) == ["aion", "ai3"]
    private = {"segments": [{"name": "甲", "text": "x", "sid": "aion"}]}
    assert story_spark.audience(private, seats) == ["aion"]
    npc = {"segments": [{"name": "NPC", "text": "x", "sid": "npc"}]}
    assert story_spark.audience(npc, seats) == []


def test_private_story_is_never_browsed(monkeypatch):
    run_story(monkeypatch, ["标题：私下\n正文"], speakers("甲"))
    assert story_spark.claim_due_browse(now=10**12) == []


def test_relay_story_is_browsed_once_each(monkeypatch):
    run_story(monkeypatch, ["标题：接力\n一", "二"], speakers("甲", "乙"))
    assert sorted(a for _, a in story_spark.claim_due_browse(now=10**12)) == ["乙", "甲"]
    assert story_spark.claim_due_browse(now=10**12) == []


def test_parse_picks_limits_one_per_segment_and_drops_bad():
    raw = '好的 [{"seg":0,"para":1,"text":"a"},{"seg":0,"para":0,"text":"b"},{"seg":1,"para":9,"text":"c"},{"seg":5,"para":0,"text":"d"}]'
    assert story_spark.parse_picks(raw, STORY, {0, 1}) == [(0, 1, "a")]
    assert story_spark.parse_picks("[]", STORY, {0, 1}) == []
    assert story_spark.parse_picks("不评了", STORY, {0, 1}) == []


def test_browse_skips_own_and_already_commented(monkeypatch):
    story_spark.insert_story("a", "大家一起", "", SEED)
    story_spark.update_story("a", status="done", title="雪夜", segments=STORY["segments"])
    monkeypatch.setattr(story_spark, "_seat", lambda a: {"id": a, "name": "甲" if a == "aion" else "乙", "model": "m", "persona": ""})
    prompts = []

    async def speak(prompt, model, persona="", name=""):
        prompts.append(prompt)
        return '[{"seg":1,"para":0,"text":"好"}]' if len(prompts) == 1 else "谢"
    monkeypatch.setattr(story_spark, "_speak", speak)
    asyncio.run(story_spark.browse_job("a", "aion", "小猫"))
    assert "三" in prompts[0] and "第0段" not in prompts[0]
    assert [c["who"] for c in story_spark.story_extras("a")["comments"]] == ["甲", "乙"]
    count = len(prompts)
    asyncio.run(story_spark.browse_job("a", "aion", "小猫"))
    assert len(prompts) == count


def test_remember_is_open_now_hub_is_ready():
    assert story_spark.REMEMBER_PAUSED is False


def seeded(segments=None):
    story_spark.insert_story("a", "大家一起", "", SEED)
    story_spark.update_story("a", status="done", title="雪夜", segments=segments or [{"name": "甲", "text": "开头", "sid": "aion", "model": "m"}])


def fake_speak(monkeypatch, replies):
    prompts = []

    async def speak(prompt, model, persona="", name=""):
        prompts.append(prompt)
        return replies.pop(0)
    monkeypatch.setattr(story_spark, "_speak", speak)
    return prompts


SPK = [{"id": "aion", "name": "甲", "model": "m", "persona": ""}, {"id": "ai3", "name": "乙", "model": "n", "persona": ""}]


def test_continue_appends_and_pulls_new_lore_out_of_text(monkeypatch):
    seeded()
    prompts = fake_speak(monkeypatch, ["第二段\n【新设定】猫会说话", "第三段"])
    asyncio.run(story_spark.continue_job("a", SPK, "往北走"))
    story = story_spark.get_story("a")
    assert [s["text"] for s in story["segments"]] == ["开头", "第二段", "第三段"]
    assert "往北走" in prompts[0] and "第二段" in prompts[1] and "猫会说话" in prompts[1]
    assert [(x["who"], x["text"]) for x in story_spark.story_extras("a")["lore"]] == [("甲", "猫会说话")]


def test_talk_does_not_advance_the_story(monkeypatch):
    seeded()
    fake_speak(monkeypatch, ["好看", "我猜是他"])
    asyncio.run(story_spark.talk_job("a", SPK, "小猫"))
    assert len(story_spark.get_story("a")["segments"]) == 1
    assert [c["who"] for c in story_spark.story_extras("a")["chat"]] == ["甲", "乙"]


def test_lore_edit_delete_dedupe_and_scope():
    seeded()
    assert story_spark.add_lore("a", "小猫", "设定一") and not story_spark.add_lore("a", "甲", "设定一")
    lore_id = story_spark.story_extras("a")["lore"][0]["id"]
    assert story_spark.edit_lore("other", lore_id, "x") is False
    assert story_spark.edit_lore("a", lore_id, "改了") is True
    assert story_spark.story_extras("a")["lore"][0]["text"] == "改了"
    assert story_spark.delete_lore("a", lore_id) and not story_spark.story_extras("a")["lore"]


def test_recap_limits_context_and_keeps_latest(monkeypatch):
    long_text = "很长" * 2600
    seeded([{"name": "甲", "text": f"{i}{long_text}", "sid": "aion", "model": "m"} for i in range(5)])
    fake_speak(monkeypatch, ["前情"])
    asyncio.run(story_spark._maybe_recap("a", SPK[0]))
    assert story_spark.get_recap("a") == ("前情", 3)
    context = story_spark.world_context(story_spark.get_story("a"))
    assert "前情" in context and len(context) < story_spark.RECENT_CHARS + 500


def test_new_progress_can_be_remembered_again():
    seeded()
    assert story_spark.mark_remembered("a", "aion", "n", seg_count=1)
    assert story_spark.is_remembered("a", "aion", 1) and not story_spark.is_remembered("a", "aion", 2)
    assert story_spark.mark_remembered("a", "aion", "n2", seg_count=2)
    assert story_spark.mark_remembered("a", "aion", "n3", seg_count=1) is False


def test_user_passage_does_not_join_audience_or_narrators():
    seats = [{"id": "aion", "name": "甲", "model": "m", "persona": ""}, {"id": "ai3", "name": "乙", "model": "n", "persona": ""}]
    story = {"segments": [{"name": "甲", "text": "x", "sid": "aion"}, {"name": "小猫", "text": "y", "sid": "user"}]}
    assert [n["id"] for n in story_spark.narrators(story, seats)] == ["aion"]
    npc = {"segments": [{"name": "NPC", "text": "x", "sid": "npc", "model": "z"}]}
    assert story_spark.narrators(npc, seats) == [{"id": "npc", "name": "NPC", "model": "z", "persona": ""}]


def test_story_calls_are_text_only_and_flag_is_reset(monkeypatch):
    import sys
    import types
    seen = []
    provider = types.ModuleType("ai_providers")
    from text_only import TEXT_ONLY
    provider.CLI_STATUS_PREFIX = "\x00"

    async def stream(messages, model):
        seen.append(TEXT_ONLY.get())
        yield "好"
    provider.stream_ai = stream
    monkeypatch.setitem(sys.modules, "ai_providers", provider)
    assert asyncio.run(story_spark._speak("x", "m")) == "好"
    assert seen == [True] and TEXT_ONLY.get() is False


def test_legacy_story_with_duplicate_names_authorizes_nobody(monkeypatch):
    import actors
    monkeypatch.setattr(actors, "list_actors", lambda *a, **k: [])
    old = {"segments": [{"name": "甲", "text": "x"}]}
    twins = [{"id": "aion", "name": "甲"}, {"id": "ai3", "name": "甲"}]
    assert story_spark.audience(old, twins) == []
    assert story_spark.audience(old, [twins[0]]) == ["aion"]
    monkeypatch.setattr(actors, "list_actors", lambda *a, **k: [{"id": "ai5", "name": "甲"}])
    assert story_spark.audience(old, [twins[0]]) == []


def test_claim_admits_one_and_release_frees():
    from fastapi import HTTPException
    story = {"id": "c", "status": "done"}
    story_spark._claim(story)
    with pytest.raises(HTTPException):
        story_spark._claim(story)
    story_spark._release("c")
    story_spark._claim(story)
    story_spark._release("c")


def test_legacy_story_denied_when_registry_unreadable(monkeypatch):
    import actors

    def boom(*a, **k):
        raise OSError("x")
    monkeypatch.setattr(actors, "list_actors", boom)
    assert story_spark.audience({"segments": [{"name": "甲", "text": "x"}]}, [{"id": "aion", "name": "甲"}]) == []
    assert story_spark.audience({"segments": [{"name": "甲", "text": "x", "sid": "aion"}]}, [{"id": "aion", "name": "甲"}]) == ["aion"]
