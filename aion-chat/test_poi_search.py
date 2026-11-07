"""附近搜索（11-06 小猫：高德 POI 一直没发挥效果）。"""
import asyncio
from pathlib import Path

import capabilities
import context_builder
import location


def _setup(monkeypatch, *, key="k", lng=121.0, lat=31.0):
    saved = []
    monkeypatch.setattr(location, "load_location_config", lambda: {
        "amap_key": key, "enabled": True, "poi_radius": 2000, "poi_types": {"餐饮美食": "050000"}})
    monkeypatch.setattr(location, "load_location_status", lambda: {"lng": lng, "lat": lat, "state": "home"})
    monkeypatch.setattr(location, "save_location_status", saved.append)

    async def no_geo(*a):
        return None
    monkeypatch.setattr(location, "amap_regeo", no_geo)
    return saved


def _search(monkeypatch, result=None):
    calls = []

    async def fake(lng, lat, types, key, radius=2000, *, keywords="", offset=10):
        calls.append({"types": types, "keywords": keywords})
        return [{"name": "某店", "distance": "120"}] if result is None else result
    monkeypatch.setattr(location, "amap_poi_search", fake)
    return calls


def test_a_free_word_is_searched_not_dropped(monkeypatch):
    saved = _setup(monkeypatch)
    calls = _search(monkeypatch)
    text = asyncio.run(location.search_nearby_pois_text(["奶茶"]))
    assert calls == [{"types": "", "keywords": "奶茶"}]
    assert "【奶茶】" in text and "某店（120m）" in text
    assert "奶茶" not in saved[0].get("nearby_pois", {})  # 缓存只留四大类


def test_a_category_still_uses_its_code_and_is_cached(monkeypatch):
    saved = _setup(monkeypatch)
    calls = _search(monkeypatch)
    asyncio.run(location.search_nearby_pois_text(["餐饮美食"]))
    assert calls == [{"types": "050000", "keywords": ""}]
    assert saved[0]["nearby_pois"]["餐饮美食"]


def test_at_most_three_searches(monkeypatch):
    _setup(monkeypatch)
    calls = _search(monkeypatch)
    asyncio.run(location.search_nearby_pois_text(["a", "b", " ", "c", "d"]))
    assert [c["keywords"] for c in calls] == ["a", "b", "c"]


def test_nothing_nearby_is_said_out_loud(monkeypatch):
    _setup(monkeypatch)
    _search(monkeypatch, result=[])
    assert "附近暂无相关结果" in asyncio.run(location.search_nearby_pois_text(["奶茶"]))


def test_no_key_or_coords_is_empty_so_callers_say_they_failed(monkeypatch):
    _setup(monkeypatch, key="")
    assert asyncio.run(location.search_nearby_pois_text(["奶茶"])) == ""
    _setup(monkeypatch, lng=0, lat=0)
    assert asyncio.run(location.search_nearby_pois_text(["奶茶"])) == ""


class _Response:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


def _amap(monkeypatch, data, seen):
    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            seen.append(params)
            if isinstance(data, Exception):
                raise data
            return _Response(data)
    monkeypatch.setattr(location.httpx, "AsyncClient", Client)
    monkeypatch.setattr(location, "record_amap_usage", lambda s: None)


def test_amap_request_has_keyword_or_type(monkeypatch):
    seen = []
    _amap(monkeypatch, {"status": "1", "pois": [{"name": "店", "biz_ext": {}}]}, seen)
    assert asyncio.run(location.amap_poi_search(1, 2, "", "k", keywords="奶茶"))[0]["name"] == "店"
    assert seen[0]["keywords"] == "奶茶" and "types" not in seen[0]
    asyncio.run(location.amap_poi_search(1, 2, "050000", "k"))
    assert seen[1]["types"] == "050000" and "keywords" not in seen[1]


def test_refusals_are_logged_without_the_key(monkeypatch, capsys):
    _amap(monkeypatch, {"status": "0", "info": "USER_DAILY_QUERY_OVER_LIMIT", "infocode": "10044"}, [])
    assert asyncio.run(location.amap_poi_search(1, 2, "050000", "secret-key")) == []
    out = capsys.readouterr().out
    assert "10044" in out and "secret-key" not in out
    _amap(monkeypatch, RuntimeError("https://restapi.amap.com/?key=secret-key"), [])
    assert asyncio.run(location.amap_poi_search(1, 2, "050000", "secret-key")) == []
    assert "secret-key" not in capsys.readouterr().out


def test_address_lookup_before_the_search_does_not_leak_the_key(monkeypatch, capsys):
    """狐狸 §179：每次搜索前的逆地理编码、天气也不能打出 key。"""
    _amap(monkeypatch, RuntimeError("https://restapi.amap.com/?key=FAKE_SECRET_CANARY"), [])
    assert asyncio.run(location.amap_regeo(1, 2, "FAKE_SECRET_CANARY")) is None
    asyncio.run(location.amap_weather("310000", "FAKE_SECRET_CANARY"))
    assert "FAKE_SECRET_CANARY" not in capsys.readouterr().out


def test_voice_never_reads_the_new_tag_forms():
    import tts
    for tag in ("[POI_SEARCH:奶茶]", "[POI_SEARCH：奶茶]", "[poi_search:奶茶]"):
        assert any(p.search(tag) for p in tts._STRIP_PATTERNS), tag


def test_fullwidth_colon_is_recognised_and_hidden():
    assert context_builder.POI_SEARCH_PATTERN.findall("[POI_SEARCH：奶茶]") == ["奶茶"]
    assert context_builder.strip_tool_commands("我查查[POI_SEARCH：奶茶]") == "我查查"
    from routes import chat
    assert chat.POI_SEARCH_PATTERN.findall("[POI_SEARCH：奶茶][POI_SEARCH:药店]") == ["奶茶", "药店"]


def test_offered_at_home_too(monkeypatch):
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda key: key == "poi_search")
    _setup(monkeypatch)
    assert any("[POI_SEARCH:类型或关键词]" in t for t in capabilities.seat_ability_texts("小猫"))
    assert capabilities.capability_available("poi_search")[0] is True
    _setup(monkeypatch, lng=0, lat=0)
    assert not any("POI_SEARCH" in t for t in capabilities.seat_ability_texts("小猫"))


def test_all_paths_use_the_shared_search_and_say_when_it_failed():
    root = Path(__file__).parent
    room = (root / "routes" / "chatroom.py").read_text(encoding="utf-8")
    start = room.index("async def _chatroom_poi_check")
    body = room[start:room.index("\nasync def ", start + 10)]
    assert "search_nearby_pois_text(categories, room_key=room_id) or POI_FAILED_TEXT" in body and "amap_poi_search" not in body
    chat = (root / "routes" / "chat.py").read_text(encoding="utf-8")
    assert "search_nearby_pois_text(categories) or POI_FAILED_TEXT" in chat
    assert "_is_outside_location" not in room


def _many(monkeypatch, count=25):
    async def fake(lng, lat, types, key, radius=2000, *, keywords="", offset=10):
        return [{"name": f"店{i}", "distance": str(10 * i)} for i in range(min(count, offset))]
    monkeypatch.setattr(location, "amap_poi_search", fake)


def _names(text):
    import re
    return re.findall(r"- (店\d+)", text)


def test_three_in_a_group_get_different_shops(monkeypatch):
    """11-07 小猫：群里三个人搜到的高度同质化。"""
    _setup(monkeypatch)
    _many(monkeypatch)
    monkeypatch.setattr(location, "_room_seen", {})
    first = _names(asyncio.run(location.search_nearby_pois_text(["奶茶"], room_key="g1")))
    second_text = asyncio.run(location.search_nearby_pois_text(["奶茶"], room_key="g1"))
    second = _names(second_text)
    assert len(first) == 10 and len(second) == 10 and not set(first) & set(second)
    assert "别和他们重复" in second_text
    other_room = _names(asyncio.run(location.search_nearby_pois_text(["奶茶"], room_key="g2")))
    assert other_room == first  # 别的群不受影响


def test_concurrent_searches_in_a_group_still_differ(monkeypatch):
    _setup(monkeypatch)
    _many(monkeypatch)
    monkeypatch.setattr(location, "_room_seen", {})

    async def both():
        return await asyncio.gather(*(location.search_nearby_pois_text(["奶茶"], room_key="g1") for _ in range(3)))
    texts = asyncio.run(both())
    sets = [set(_names(t)) for t in texts]
    assert all(sets)
    assert not (sets[0] & sets[1]) and not (sets[1] & sets[2]) and not (sets[0] & sets[2])


def test_when_everything_was_taken_they_still_get_something(monkeypatch):
    _setup(monkeypatch)
    _many(monkeypatch, count=5)
    monkeypatch.setattr(location, "_room_seen", {})
    asyncio.run(location.search_nearby_pois_text(["奶茶"], room_key="g1"))
    assert _names(asyncio.run(location.search_nearby_pois_text(["奶茶"], room_key="g1")))


def test_private_chat_is_unchanged(monkeypatch):
    _setup(monkeypatch)
    _many(monkeypatch)
    monkeypatch.setattr(location, "_room_seen", {})
    one = _names(asyncio.run(location.search_nearby_pois_text(["奶茶"])))
    assert one == _names(asyncio.run(location.search_nearby_pois_text(["奶茶"]))) and len(one) == 10
