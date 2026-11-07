"""全家海龟汤（docs/施工方案-一起玩.md 第一节）。

11-07 小猫：「问」开头就算提问，担心全角半角对不上；「问一下」「问问」「问题是」「问你个事」不算。
4399 实测：ask 回 judgment yes/no/unrelated；guess 回 correct + score；游客不能锁房；每次连接都是新游客。
"""
import asyncio
import json
import sqlite3
import time

import pytest

import turtle_soup as ts


# ── 认不认得出 ──

@pytest.mark.parametrize("line,expect", [
    ("问：他是男的吗", ("ask", "他是男的吗")),
    ("问:他是男的吗", ("ask", "他是男的吗")),
    ("问 他是男的吗", ("ask", "他是男的吗")),
    ("问　他是男的吗", ("ask", "他是男的吗")),      # 全角空格
    ("问 ：他是男的吗", ("ask", "他是男的吗")),
    ("问他是男的吗", ("ask", "他是男的吗")),
    ("  **问：** 他死了吗？", ("ask", "他死了吗？")),  # AI 爱加粗
    ("> 问：笔是自己动的吗", ("ask", "笔是自己动的吗")),
    ("「问：老妇人是鬼吗」", ("ask", "老妇人是鬼吗」")),
    ("猜：他们三个都已经死了", ("guess", "他们三个都已经死了")),
    ("猜:他们三个都已经死了", ("guess", "他们三个都已经死了")),
    ("提示", ("hint", "")),
    ("给个提示吧", ("hint", "")),
    ("要一点提示！", ("hint", "")),
    ("海龟汤结束", ("end", "")),
    ("不玩海龟汤了", ("end", "")),
])
def test_lines_that_count(line, expect):
    assert ts.parse_line(line) == expect


@pytest.mark.parametrize("line", [
    "问一下，你们晚上吃啥", "问问狐狸", "问题是她没说", "问你个事", "问下狗蛋", "问我干嘛",
    "我想问：他是男的吗",          # 不在行首
    "猜猜我是谁", "我猜他是在做梦",  # 「猜」要带冒号
    "提示音好吵", "问", "问：", "海龟汤结束了吗？",
])
def test_lines_that_do_not_count(line):
    assert ts.parse_line(line) is None


@pytest.mark.parametrize("text,kind", [
    ("来一局海龟汤", "any"), ("开个海龟汤吧", "any"), ("玩海龟汤！", "any"), ("海龟汤开局", "any"),
    ("来一碗清汤", "clear"), ("来1局海龟汤", "any"),
])
def test_start_words(text, kind):
    assert ts.wants_start(text) == kind


@pytest.mark.parametrize("text", ["海龟汤好好玩", "昨天那局海龟汤", "喝汤"])
def test_not_start_words(text):
    assert ts.wants_start(text) is None


# ── 一局怎么走 ──

@pytest.fixture
def game(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ts, "_state", {})
    monkeypatch.setattr(ts, "_locks", {})
    monkeypatch.setattr(ts, "_loaded", False)
    calls, posts, answers = [], [], {}

    async def play(action, params):
        calls.append((action, params))
        answer = answers.get(action)
        if callable(answer):
            answer = answer(params)
        return answer if answer is not None else (None, "没设")
    monkeypatch.setattr(ts, "_play", play)

    async def post(text):
        posts.append(text)

    def say(sender, text, can_start=None):
        who = {"user": "小猫", "aion": "小克", "connor": "Lucien", "ai3": "狗蛋"}[sender]
        return asyncio.run(ts.on_message("g1", sender, who, text, post,
                                         can_start=(sender == "user") if can_start is None else can_start))
    answers["create_random"] = ({"id": "R1", "title": "笔仙", "tags": "变格，黑汤", "surface": "笔仙笔仙……"}, "")
    return say, calls, posts, answers, tmp_path


def test_only_she_can_start_and_the_surface_is_posted(game):
    say, calls, posts, _, tmp = game
    assert say("aion", "来一局海龟汤") is False and calls == []
    assert say("user", "来一局海龟汤") is True
    assert calls == [("create_random", {})]
    assert "《笔仙》（变格·黑汤）" in posts[-1] and "笔仙笔仙……" in posts[-1] and "「问」开头" in posts[-1]
    saved = json.loads((tmp / "turtle_soup.json").read_text(encoding="utf-8"))
    assert saved["g1"]["room"] == "R1"


def test_clear_soup_picks_from_the_clear_list(game):
    say, calls, _, answers, _ = game
    answers["list_puzzles"] = ({"items": [{"id": 52, "title": "吃饱之后"}]}, "")
    say("user", "来一碗清汤")
    assert calls[0] == ("list_puzzles", {"tag": "清汤", "page_size": 50})
    assert calls[1] == ("create_random", {"puzzle_id": 52})


def test_questions_from_anyone_go_to_the_judge(game):
    say, calls, posts, answers, _ = game
    say("user", "来一局海龟汤")
    answers["ask"] = lambda p: ({"judgment": {"男": "yes", "鬼": "no"}.get(p["content"][0], "unrelated")}, "")
    say("user", "问：男的吗")
    say("ai3", "我觉得有点怪\n问 鬼吗\n大家怎么看")
    say("connor", "问：今天下雨吗")
    assert [c[1]["content"] for c in calls if c[0] == "ask"] == ["男的吗", "鬼吗", "今天下雨吗"]
    assert all(c[1]["room_id"] == "R1" for c in calls if c[0] == "ask")
    assert posts[-3:] == ["🐢 汤主：小猫问「男的吗」→ 是", "🐢 汤主：狗蛋问「鬼吗」→ 不是",
                          "🐢 汤主：Lucien问「今天下雨吗」→ 无关"]


def test_no_game_no_relay(game):
    say, calls, posts, _, _ = game
    assert say("user", "问：他是男的吗") is False
    assert say("aion", "猜：都死了") is False
    assert calls == [] and posts == []


def test_one_message_cannot_flood(game):
    say, calls, _, answers, _ = game
    say("user", "来一局海龟汤")
    answers["ask"] = ({"judgment": "no"}, "")
    say("aion", "\n".join(f"问：第{i}个" for i in range(8)))
    assert sum(1 for c in calls if c[0] == "ask") == ts.MAX_PER_MESSAGE


def test_correct_guess_ends_the_game(game):
    say, calls, posts, answers, _ = game
    say("user", "来一局海龟汤")
    answers["guess"] = ({"correct": False, "score": 3}, "")
    say("aion", "猜：他在做梦")
    assert posts[-1] == "🐢 汤主：小克的还原 → 还没对上。还原度 3%"
    answers["guess"] = ({"correct": True, "score": 92}, "")
    say("ai3", "猜：三个人都是鬼")
    assert "狗蛋猜中了" in posts[-1] and "92%" in posts[-1]
    assert say("user", "问：还在吗") is False  # 这局已经结束


def test_hints_capped_at_three(game):
    say, calls, posts, answers, _ = game
    say("user", "来一局海龟汤")
    answers["hint_request"] = ({"hint_text": "和镜子有关"}, "")
    for _ in range(4):
        say("user", "提示")
    assert sum(1 for c in calls if c[0] == "hint_request") == 3
    assert "用完了" in posts[-1]


def test_only_she_can_end_it(game):
    say, calls, posts, answers, _ = game
    say("user", "来一局海龟汤")
    say("aion", "海龟汤结束")
    assert ts.active("g1")
    answers["reveal_answer"] = (None, "还没到 50 题")
    say("user", "海龟汤结束")
    assert ts.active("g1") is None and "问满 50 个" in posts[-1]


def test_starting_again_mid_game_is_refused(game):
    say, calls, posts, _, _ = game
    say("user", "来一局海龟汤")
    say("user", "再来一局海龟汤")
    assert sum(1 for c in calls if c[0] == "create_random") == 1 and "还没结束" in posts[-1]


def test_idle_game_lapses(game, monkeypatch):
    say, calls, _, _, _ = game
    say("user", "来一局海龟汤")
    ts._state["g1"]["last"] = time.time() - ts.IDLE_SECONDS - 1
    assert say("user", "问：还在吗") is False
    assert not any(c[0] == "ask" for c in calls)


def test_judge_error_is_shown_not_invented(game):
    say, _, posts, answers, _ = game
    say("user", "来一局海龟汤")
    answers["ask"] = (None, "4399 这会儿连不上，等一下再试")
    say("ai3", "问：是意外吗")
    assert posts[-1] == "🐢 汤主：狗蛋这个问题没问出去：4399 这会儿连不上，等一下再试"


def test_unknown_judgment_is_shown_as_is(game):
    say, _, posts, answers, _ = game
    say("user", "来一局海龟汤")
    answers["ask"] = ({"judgment": "partial", "hint_text": "方向对了"}, "")
    say("user", "问：和钱有关吗")
    assert "（裁判说：partial）" in posts[-1] and "💡 方向对了" in posts[-1]


# ── 用的是游客连接，不是谁的账号 ──

def test_uses_the_guest_connection(monkeypatch):
    import mcp_outings
    monkeypatch.setattr(mcp_outings, "_cfg", lambda: {"servers": [
        {"name": "狐狸4399", "url": "https://toy.cedarstar.org/TOKEN_CANARY/mcp"},
        {"name": "4399", "url": "https://toy.cedarstar.org/mcp"},
    ]})
    assert ts._guest_server()["name"] == "4399"
    monkeypatch.setattr(mcp_outings, "_cfg", lambda: {"servers": [
        {"name": "狐狸4399", "url": "https://toy.cedarstar.org/TOKEN_CANARY/mcp"}]})
    assert ts._guest_server() is None


# ── 群聊里接上了 ──

def test_chatroom_hook_only_starts_in_group_rooms(monkeypatch, tmp_path):
    import config
    from routes import chatroom
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ts, "_state", {})
    monkeypatch.setattr(ts, "_loaded", True)
    seen = []

    async def on_message(*a, **k):
        seen.append(a[0])
        return True
    monkeypatch.setattr(ts, "on_message", on_message)

    async def room_type(room_id):
        return {"g1": "group"}.get(room_id, "seat_1v1")
    monkeypatch.setattr(chatroom, "_get_room_type", room_type)
    q = asyncio.Queue()
    assert asyncio.run(chatroom._turtle_soup("s1", "user", "来一局海龟汤", q, can_start=True)) is False
    assert asyncio.run(chatroom._turtle_soup("g1", "aion", "来一局海龟汤", q)) is False  # AI 开不了
    assert asyncio.run(chatroom._turtle_soup("g1", "user", "今天好累", q, can_start=True)) is False
    assert asyncio.run(chatroom._turtle_soup("g1", "user", "来一局海龟汤", q, can_start=True)) is True
    assert seen == ["g1"]


def test_hooks_are_wired_for_everyone():
    from pathlib import Path
    src = (Path(__file__).parent / "routes" / "chatroom.py").read_text(encoding="utf-8")
    assert 'await _turtle_soup(room_id, "user", body.content, _q, can_start=True)' in src
    assert 'await _turtle_soup(room_id, "aion", clean_text, _q)' in src
    assert 'await _turtle_soup(room_id, "connor", clean_text, _q)' in src
    assert "await _turtle_soup(room_id, actor_id, clean_text, _q)" in src


def test_seat_sees_the_host_but_not_other_system_messages(tmp_path, monkeypatch):
    import database
    import seat_chat
    path = tmp_path / "chat.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    asyncio.run(database.init_db())
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            INSERT INTO chatroom_rooms (id,title,type,actor,created_at,updated_at) VALUES ('g1','家','group','',1,1);
            INSERT INTO chatroom_messages (id,room_id,sender,content,created_at) VALUES
              ('m1','g1','user','问：男的吗',10),
              ('m2','g1','system','🐢 汤主：小猫问「男的吗」→ 是',11),
              ('m3','g1','system','（小克去搜了一下）',12);
        """)
    rows = asyncio.run(seat_chat.merged_messages("ai3", 50))
    assert [r["content"] for r in rows] == ["问：男的吗", "🐢 汤主：小猫问「男的吗」→ 是"]
    history = seat_chat._history("ai3", rows, 50, group=True, user_name="小猫")
    assert "【系统】🐢 汤主" in history[-1]["content"]
