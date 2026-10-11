import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import autonomy
import autonomy_niches
import travel
import travel_places

PLACE = travel_places.Place(name="雷克雅未克", country="冰岛", weather="阴，7°C", local_time="10月10日 17:02",
                            intro="冰岛的首都。", wiki_url="https://zh.wikipedia.org/wiki/x",
                            photos=[{"path": "/uploads/travel/a.jpg", "credit": "Wikipedia"},
                                    {"path": "/uploads/travel/b.jpg", "credit": "Commons"}])


@pytest.fixture
def env(monkeypatch):
    calls = {"written": [], "private": [], "group": [], "cards": [], "events": [], "lookup": []}

    async def lookup(name, **kw):
        calls["lookup"].append(name)
        return PLACE

    async def write(actor, messages):
        prompt = messages[-1]["content"]
        calls["written"].append((actor, prompt))
        if "[全家出游]" in prompt:
            return f"{actor} 写的一段"
        return f"{actor} 的游记正文\n标题：北边的风"

    async def private(actor, text, atts=None, **kw):
        calls["private"].append((actor, text, atts))
        return {"id": f"m-{actor}"}

    async def group(room, actor, text, **kw):
        calls["group"].append((room, actor, text, kw.get("attachments")))
        return {"id": "g"}

    async def card(**kw):
        calls["cards"].append(kw)
        return {"id": "c", **kw}

    async def event(actor, action, title, *a, **kw):
        calls["events"].append((actor, action, title))
        return {}

    monkeypatch.setattr(travel_places, "lookup", lookup)
    monkeypatch.setattr(autonomy, "_actor_context", AsyncMock(return_value=[{"role": "user", "content": "人设"}]))
    monkeypatch.setattr(autonomy, "_call_actor", write)
    monkeypatch.setattr(autonomy, "_save_private_message", private)
    monkeypatch.setattr(autonomy, "_save_autonomy_chatroom_message", group)
    monkeypatch.setattr(autonomy, "_latest_group_room_id", AsyncMock(return_value="room-group"))
    monkeypatch.setattr(autonomy, "append_idle_event", event)
    monkeypatch.setattr(autonomy, "_actor_label", lambda a: {"aion": "小克", "connor": "Lucien", "ai3": "Jasper"}.get(a, a))
    monkeypatch.setattr(autonomy_niches, "create_niche_card", card)
    monkeypatch.setattr(travel, "_user_name", lambda: "Ceci")
    return calls


def test_solo_trip_writes_feelings_and_plans_and_keeps_a_card(env):
    result = asyncio.run(travel.trip("solo", ["ai3"], where="named", named="冰岛雷克雅未克", wish="想看海"))
    assert env["lookup"] == ["冰岛雷克雅未克"]
    actor, prompt = env["written"][0]
    assert actor == "ai3" and "[独自旅行]" in prompt
    assert "感想" in prompt and "接下来想做什么" in prompt and "想看海" in prompt
    assert "此刻天气：阴，7°C" in prompt and "不要编" in prompt
    assert env["private"] == [("ai3", "ai3 的游记正文", ["/uploads/travel/a.jpg", "/uploads/travel/b.jpg"])]
    card = env["cards"][0]
    assert card["actor"] == "ai3" and card["title"] == "北边的风" and card["photo_path"] == "/uploads/travel/a.jpg"
    assert "旅行" in card["tags"] and result["title"] == "北边的风"


def test_duo_trip_is_a_scene_together(env):
    asyncio.run(travel.trip("duo", ["aion"], where="named", named="大理"))
    prompt = env["written"][0][1]
    assert "[两人同游]" in prompt and "你和Ceci" in prompt and "情景" in prompt
    assert env["cards"][0]["tags"][1] == "和Ceci"


def test_family_trip_each_ai_writes_its_own_part_in_turn(env):
    asyncio.run(travel.trip("family", ["aion", "connor", "ai3"], where="named", named="京都"))
    order = [actor for actor, _ in env["written"]]
    assert order == ["aion", "connor", "ai3"]  # 轮流，各用自己的模型
    prompts = [p for _, p in env["written"]]
    assert "你先开场" in prompts[0] and "接着前面的写" in prompts[1] and "你来收尾" in prompts[2]
    assert "小克：aion 写的一段" in prompts[1] and "Lucien：connor 写的一段" in prompts[2]
    assert all("不要替别人说话" in p for p in prompts)
    assert [(g[1], bool(g[3])) for g in env["group"]] == [("aion", True), ("connor", False), ("ai3", False)]
    assert {c["actor"] for c in env["cards"]} == {"aion", "connor", "ai3"}


def test_ai_picks_destination_when_asked(env, monkeypatch):
    ask = AsyncMock(return_value={"place": "挪威 罗弗敦群岛", "why": "想看极光"})
    monkeypatch.setattr(autonomy, "_ask_actor_json", ask)
    monkeypatch.setattr(travel, "_visited", AsyncMock(return_value=["京都"]))
    asyncio.run(travel.trip("solo", ["ai3"], where="pick"))
    assert env["lookup"] == ["挪威 罗弗敦群岛"]
    assert "最近去过：京都" in ask.await_args.args[1]


def test_near_me_only_gives_the_city(env, monkeypatch):
    ask = AsyncMock(return_value={"place": "杭州 西湖"})
    monkeypatch.setattr(autonomy, "_ask_actor_json", ask)
    monkeypatch.setattr(travel, "near_user_city", AsyncMock(return_value="杭州市"))
    asyncio.run(travel.trip("solo", ["ai3"], where="near"))
    prompt = ask.await_args.args[1]
    assert "小猫现在在杭州市" in prompt


def test_bad_combinations_are_rejected(env):
    with pytest.raises(ValueError):
        asyncio.run(travel.trip("family", ["aion"], where="pick"))
    with pytest.raises(ValueError):
        asyncio.run(travel.trip("solo", ["aion", "connor"], where="pick"))
    with pytest.raises(ValueError):
        asyncio.run(travel.trip("solo", ["aion"], where="named", named=" "))


def test_title_line_is_split_off():
    assert travel._split_title("正文\n第二行\n标题：海边", "默认") == ("正文\n第二行", "海边")
    assert travel._split_title("没有标题", "默认") == ("没有标题", "默认")
