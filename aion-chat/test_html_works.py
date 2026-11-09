import random
from datetime import date

import pytest

import festivals
import html_works


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(html_works, "DB_PATH", tmp_path / "w.db")
    monkeypatch.setattr(festivals, "_personal", lambda: {})


DOC = "<!doctype html><html><body>hi</body></html>"


def test_weekly_limit_is_hard_and_rolls():
    assert html_works.save_work("aion", "小克", "a", "", DOC, "", now=1000)
    assert html_works.save_work("aion", "小克", "b", "", DOC, "", now=1001)
    assert html_works.save_work("aion", "小克", "c", "", DOC, "", now=1002) is None
    assert html_works.save_work("ai3", "狗蛋", "x", "", DOC, "", now=1002)
    assert html_works.save_work("aion", "小克", "d", "", DOC, "", now=1000 + 7 * 86400 + 5)


def test_parse_reply():
    title, note, html = html_works.parse_reply("标题：雪\n赠言：给你\n" + DOC + "\n以上")
    assert (title, note) == ("雪", "给你") and html == DOC
    assert html_works.parse_reply("不做") is None
    assert html_works.parse_reply("<html>" + "x" * html_works.MAX_HTML_CHARS) is None


def test_plan_runs_once_a_day():
    day = date(2026, 11, 9)
    now = 1794000000.0
    html_works.plan_today("aion", day, now, rng=random.Random(1))
    assert html_works.claim_plan("aion", day, now + 90000) is True
    assert html_works.claim_plan("aion", day, now + 90000) is False


def test_occasion_is_used_once_then_mostly_skipped():
    day = date(2026, 8, 18)
    wanted, occasion = html_works.choose_occasion("aion", day)
    assert wanted and occasion.startswith("七夕")
    html_works.save_work("aion", "小克", "a", "", DOC, "七夕")
    class Never:
        def random(self):
            return 0.99
    assert html_works.choose_occasion("aion", day, rng=Never()) == (False, "")


def test_prompt_warns_about_cost_and_external_resources():
    text = html_works.build_prompt("小猫", "", "", 2)
    assert "费 token" in text and "不做" in text and "外部资源" in text


def test_viewer_is_sandboxed_without_same_origin():
    page = (html_works.BASE_DIR / "static" / "works.html").read_text(encoding="utf-8")
    assert 'sandbox="allow-scripts"' in page and "allow-same-origin" not in page.replace("不给 allow-same-origin", "")
    assert "default-src 'none'" in page and "innerHTML" not in page


def test_opened_mark_and_delete():
    work = html_works.save_work("aion", "小克", "a", "", DOC, "", now=1)
    assert html_works.list_works()[0]["opened_at"] is None
    assert html_works.get_work(work, now=5)["html"] == DOC
    assert html_works.list_works()[0]["opened_at"] == 5
    assert html_works.delete_work(work) and html_works.list_works() == []


def test_festivals():
    assert festivals.on(date(2026, 8, 19), {}) == ["七夕"]
    assert festivals.on(date(2027, 2, 5), {}) == ["除夕"]
    assert festivals.on(date(2026, 2, 14), {}) == ["情人节"]
    assert festivals.on(date(2026, 3, 9), {"03-09": ["小猫的生日"]}) == ["小猫的生日"]
    assert festivals.hint(date(2026, 8, 19)) == "（今天是七夕）"
    assert festivals.hint(date(2026, 8, 17)) == "（再过2天是七夕）"
    assert festivals.hint(date(2026, 7, 7)) == ""


def test_deleting_does_not_refund_quota():
    work = html_works.save_work("aion", "小克", "a", "", DOC, "七夕", now=1000)
    html_works.delete_work(work)
    assert html_works.recent_count("aion", now=1001) == 1
    assert html_works.occasion_done("aion", "七夕", now=1001)


def test_outer_page_blocks_frames_from_navigating_away():
    page = (html_works.BASE_DIR / "static" / "works.html").read_text(encoding="utf-8")
    assert "frame-src 'none'" in page[:400]


def test_seat_disabled_while_reading_hub_is_not_called(monkeypatch):
    import asyncio
    import sys
    import types

    state = {"ready": True, "calls": 0}
    seat = {"id": "ai3", "name": "狗蛋", "model": "m", "persona": ""}
    hub = types.ModuleType("hub_day")

    async def day_text(*args):
        state["ready"] = False
        return ""
    hub.day_text = day_text
    story = types.ModuleType("story_spark")
    story.ready_seats = lambda: [dict(seat)] if state["ready"] else []
    provider = types.ModuleType("ai_providers")
    provider.CLI_STATUS_PREFIX = "x"

    async def stream(messages, model):
        state["calls"] += 1
        yield "不做"
    provider.stream_ai = stream
    for name, module in (("hub_day", hub), ("story_spark", story), ("ai_providers", provider)):
        monkeypatch.setitem(sys.modules, name, module)
    asyncio.run(html_works.consider(seat, "", "小猫"))
    assert state["calls"] == 0
