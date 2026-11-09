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
    assert story["segments"] == [{"name": "狗蛋", "text": "正文"}]
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
    assert "memory_hub" not in src and "INSERT INTO messages" not in src and "INSERT INTO chatroom" not in src
