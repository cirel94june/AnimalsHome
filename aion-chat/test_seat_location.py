"""位置和周边：狗蛋等座位和小克一样看得到她的位置，在户外时能搜附近，由 TA 自己接着推荐。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

import capabilities
import location
import routes.chatroom as chatroom_routes
import seat_chat


def test_location_line_follows_the_switch(monkeypatch):
    monkeypatch.setattr(location, "load_location_config", lambda: {"enabled": True})
    monkeypatch.setattr(location, "format_location_for_prompt", lambda: "她在西湖边，晴 22℃")
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: name == "location_context")
    assert seat_chat.location_line() == "【位置信息】\n她在西湖边，晴 22℃"
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: False)
    assert seat_chat.location_line() == ""
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: True)
    monkeypatch.setattr(location, "load_location_config", lambda: {"enabled": False})
    assert seat_chat.location_line() == ""


def test_seat_context_carries_the_location(monkeypatch):
    import memory_hub_bridge
    monkeypatch.setattr(memory_hub_bridge, "context_block", AsyncMock(return_value=""))
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: name == "location_context")
    monkeypatch.setattr(location, "load_location_config", lambda: {"enabled": True})
    monkeypatch.setattr(location, "format_location_for_prompt", lambda: "她在西湖边")
    monkeypatch.setattr(seat_chat, "_persona_text", lambda a: "人设")
    monkeypatch.setattr(seat_chat.actors, "get_actor", lambda a: {"name": "狗蛋"})
    built = asyncio.run(seat_chat.build_context("ai3", [], group=False))
    assert "【位置信息】\n她在西湖边" in "\n".join(m["content"] for m in built)


@pytest.fixture
def room(monkeypatch):
    notes = AsyncMock(return_value={"id": "sys1"})
    monkeypatch.setattr(chatroom_routes, "_chatroom_sys_msg", notes)
    monkeypatch.setattr(chatroom_routes, "_name_for_identity", lambda a: "狗蛋")
    monkeypatch.setattr(chatroom_routes, "is_capability_enabled", lambda name: True)
    return notes


def _run(text):
    return asyncio.run(chatroom_routes._process_seat_commands(text, "s3", "ai3", "m1", asyncio.Queue()))


def test_seat_searches_nearby_only_when_outside(room, monkeypatch):
    monkeypatch.setattr(capabilities, "_is_outside_location", lambda: True)
    text, triggered = _run("我帮你看看[POI_SEARCH:餐饮美食]")
    assert text == "我帮你看看" and triggered["poi_search"] == ["餐饮美食"]
    assert "📍 狗蛋搜了搜附近的餐饮美食" in room.await_args.args[1]
    room.reset_mock()
    monkeypatch.setattr(capabilities, "_is_outside_location", lambda: False)
    text, triggered = _run("嗯[POI_SEARCH:餐饮美食]")
    assert text == "嗯" and "poi_search" not in triggered
    room.assert_not_called()


def test_prompt_offers_nearby_search_only_outside(monkeypatch):
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda name: name == "poi_search")
    monkeypatch.setattr(capabilities, "_is_outside_location", lambda: True)
    assert capabilities.poi_search_ability_text("小猫") in capabilities.seat_ability_texts("小猫")
    monkeypatch.setattr(capabilities, "_is_outside_location", lambda: False)
    assert not any(t.startswith("[POI_SEARCH") for t in capabilities.seat_ability_texts("小猫"))


@pytest.fixture
def followup(monkeypatch):
    import config
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "seat-m": {"provider": "custom_openai", "model": "m"}})
    monkeypatch.setattr(config, "load_worldbook", lambda: {"user_name": "小猫"})
    monkeypatch.setattr(seat_chat, "check_ready", lambda a: ({}, "seat-m"))
    monkeypatch.setattr(seat_chat, "_persona_text", lambda a: "本少爷人设")
    monkeypatch.setattr(chatroom_routes, "_name_for_identity", lambda a: "狗蛋")
    monkeypatch.setattr(chatroom_routes, "_load_room_and_messages", AsyncMock(return_value=({}, [])))
    monkeypatch.setattr(location, "format_location_for_prompt", lambda: "")
    monkeypatch.setattr(location, "search_nearby_pois_text", AsyncMock(return_value="【餐饮美食】\n  - 老王面馆（300m）"))
    seen = []

    async def stream(messages, model_key, *a, **k):
        seen.append((messages, model_key))
        yield "去老王面馆吧[转账：5元]"
    monkeypatch.setattr(chatroom_routes, "stream_ai", stream)
    saved = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_save_msg", saved)
    notes = AsyncMock()
    monkeypatch.setattr(chatroom_routes, "_chatroom_sys_msg", notes)
    return seen, saved, notes


def test_seat_recommends_from_the_results_in_its_own_voice(followup):
    seen, saved, notes = followup
    asyncio.run(chatroom_routes._chatroom_poi_search("s3", "ai3", "seat-m", ["餐饮美食"]))
    messages, model_key = seen[0]
    assert model_key == "seat-m" and "本少爷人设" in messages[0]["content"] and "老王面馆" in messages[-1]["content"]
    assert saved.await_args.args[:3] == ("s3", "ai3", "去老王面馆吧")  # 不执行的转账标签也去掉
    assert saved.await_args.kwargs["auto_tts"] is False


def test_seat_changed_during_search_does_not_reply(followup, monkeypatch):
    seen, saved, notes = followup
    calls = iter([({}, "seat-m"), ({}, "other-m")])
    monkeypatch.setattr(seat_chat, "check_ready", lambda a: next(calls))
    asyncio.run(chatroom_routes._chatroom_poi_search("s3", "ai3", "seat-m", ["餐饮美食"]))
    assert not seen and not saved.called and "设置刚刚变了" in notes.await_args.args[1]


def test_nothing_found_says_so(followup, monkeypatch):
    seen, saved, notes = followup
    monkeypatch.setattr(location, "search_nearby_pois_text", AsyncMock(return_value=""))
    asyncio.run(chatroom_routes._chatroom_poi_search("s3", "ai3", "seat-m", ["购物"]))
    assert not seen and "没搜到" in notes.await_args.args[1]


def test_shared_nearby_search_formats_results_and_refreshes_cache(monkeypatch):
    """抽出来的共用搜索（小克私聊也用它）：取最新坐标、按类型搜、更新缓存、格式化。"""
    status = {"lng": 120.1, "lat": 30.2}
    saved = []
    monkeypatch.setattr(location, "load_location_config", lambda: {
        "amap_key": "k", "poi_types": {"餐饮美食": "050000"}, "poi_radius": 1500})
    monkeypatch.setattr(location, "load_location_status", lambda: dict(status))
    monkeypatch.setattr(location, "save_location_status", saved.append)
    monkeypatch.setattr(location, "amap_regeo", AsyncMock(return_value={"address": "西湖边", "adcode": "330106"}))
    search = AsyncMock(return_value=[{"name": "老王面馆", "distance": "300", "rating": "4.5", "cost": "[]", "address": "x路"}])
    monkeypatch.setattr(location, "amap_poi_search", search)
    text = asyncio.run(location.search_nearby_pois_text(["餐饮美食", "不认识的类型"]))
    assert text == "【餐饮美食】\n  - 老王面馆（300m） ⭐4.5 | x路"
    assert search.await_args.args == (120.1, 30.2, "050000", "k", 1500)
    assert saved[0]["address"] == "西湖边" and saved[0]["nearby_pois"]["餐饮美食"][0]["name"] == "老王面馆"
    monkeypatch.setattr(location, "load_location_config", lambda: {"amap_key": ""})
    assert asyncio.run(location.search_nearby_pois_text(["餐饮美食"])) == ""
