"""旅行地球和地图（11-08 小猫）：标记、次数、当时的记录；她去过的地方；世界窗目录。"""
import asyncio
from pathlib import Path

import pytest

import config
import travel_map as tm
import travel_places


def _card(actor, name, session, at, label="京都，日本", title="京都之旅"):
    return {"id": f"c{at}", "actor": actor, "actor_name": name, "session_id": session, "created_at": at, "title": title,
            "reflection": "很安静", "photo_path": "/uploads/travel/a.jpg", "tags": ["旅行"],
            "sources": [{"title": "京都", "place": label, "weather": "晴", "photos": [{"path": "/uploads/travel/b.jpg"}]}]}


GEO = {"京都，日本": {"lat": 35.0, "lng": 135.7}}


def test_visits_are_counted_per_trip_and_family_counts_once():
    cards = [_card("aion", "小克", "travel_1_aion", 100), _card("ai3", "狗蛋", "travel_1_ai3", 100),
             _card("aion", "小克", "travel_2_aion", 200)]
    pins, missing = tm.build_pins(cards, GEO, [])
    assert missing == [] and len(pins) == 1
    pin = pins[0]
    assert pin["count"] == 2 and pin["lat"] == 35.0 and {p["id"] for p in pin["people"]} == {"aion", "ai3"}
    assert pin["visits"][0]["at"] == 200
    family = pin["visits"][1]
    assert [p["name"] for p in family["people"]] == ["小克", "狗蛋"] and len(family["parts"]) == 2
    assert family["photos"] == ["/uploads/travel/a.jpg", "/uploads/travel/b.jpg"] and family["weather"] == "晴"


def test_places_without_coordinates_wait_for_backfill():
    pins, missing = tm.build_pins([_card("aion", "小克", "t", 1, label="大理，中国")], GEO, [])
    assert pins == [] and missing == ["大理，中国"]


def test_place_that_cannot_be_found_is_not_retried():
    pins, missing = tm.build_pins([_card("aion", "小克", "t", 1, label="无名")], {"无名": {"none": True}}, [])
    assert pins == [] and missing == []


def test_her_places_are_grouped_with_their_own_count():
    mine = [{"id": "a", "name": "京都", "label": "京都，日本", "lat": 35, "lng": 135, "note": "秋天", "date": "2024-11-01", "at": 1},
            {"id": "b", "name": "京都", "label": "京都，日本", "lat": 35, "lng": 135, "note": "", "date": "", "at": 2}]
    pins, _ = tm.build_pins([], {}, mine)
    assert pins[0]["kind"] == "me" and pins[0]["count"] == 2 and pins[0]["visits"][0]["id"] == "b"


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    class Ctx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False
    monkeypatch.setattr(travel_places, "_client", lambda: Ctx())
    return tmp_path


def _osm(monkeypatch, found=True):
    async def finder(client, name):
        return travel_places.Place(name=name, country="日本", latitude=35.0, longitude=135.0) if found else None
    monkeypatch.setattr(travel_places, "_geocode_osm", finder)

    async def none(client, name):
        return None
    monkeypatch.setattr(travel_places, "_geocode_open_meteo", none)


def test_add_and_delete_my_place(data, monkeypatch):
    _osm(monkeypatch)
    record = asyncio.run(tm.add_my_place("京都", note="秋天", date="2024-11-01"))
    assert record["lat"] == 35.0 and record["note"] == "秋天"
    assert tm._read("travel_my_places.json", [])[0]["id"] == record["id"]
    assert tm.delete_my_place(record["id"]) is True and tm.delete_my_place(record["id"]) is False


@pytest.mark.parametrize("kwargs,word", [({"name": ""}, "地名"), ({"name": "京都", "date": "昨天"}, "日期")])
def test_bad_place_input(data, monkeypatch, kwargs, word):
    _osm(monkeypatch)
    with pytest.raises(tm.PlaceError, match=word):
        asyncio.run(tm.add_my_place(**kwargs))


def test_unknown_place(data, monkeypatch):
    _osm(monkeypatch, found=False)
    with pytest.raises(tm.PlaceError, match="没找到"):
        asyncio.run(tm.add_my_place("不存在"))


def test_remember_and_backfill(data, monkeypatch):
    _osm(monkeypatch)
    monkeypatch.setattr(tm, "GEO_GAP", 0)
    tm.remember("巴黎，法国", 48.8, 2.3)
    asyncio.run(tm._fill_missing(["京都，日本"]))
    geo = tm._read("travel_geo.json", {})
    assert geo["巴黎，法国"]["lat"] == 48.8 and geo["京都，日本"]["lat"] == 35.0


def test_backfill_marks_not_found(data, monkeypatch):
    _osm(monkeypatch, found=False)
    monkeypatch.setattr(tm, "GEO_GAP", 0)
    asyncio.run(tm._fill_missing(["无名"]))
    assert tm._read("travel_geo.json", {})["无名"] == {"none": True}


INDEX = '<a href="webcam/japan.html"></a><a href="webcam/italia.html"></a><a href="webcam/atlantis-land.html"></a>'
JAPAN = ('<a href="webcam/japan/kanto/tokyo/shinjuku.html"></a><a href="webcam/japan/kanto/tokyo/shibuya.html"></a>'
         '<a href="webcam/japan/kansai/osaka/dotonbori.html"></a><a href="webcam/italia/lazio/roma/x.html"></a>')


def _site(monkeypatch):
    import world_window
    urls = []

    async def text(client, url, **k):
        urls.append(url)
        return INDEX if url.endswith("/webcam.html") else JAPAN
    monkeypatch.setattr(world_window, "_text", text)
    return urls


def test_camera_directory_and_cache(data, monkeypatch):
    urls = _site(monkeypatch)
    countries = asyncio.run(tm.camera_countries())
    assert countries[0]["name"] in ("日本", "意大利") and {"slug": "atlantis-land", "name": "Atlantis Land"} in countries
    cities = asyncio.run(tm.camera_cities("japan"))
    assert cities == [{"slug": "osaka", "name": "Osaka", "cams": 1}, {"slug": "tokyo", "name": "Tokyo", "cams": 2}]
    asyncio.run(tm.camera_cities("japan"))
    assert len(urls) == 2  # 第二次走缓存


@pytest.mark.parametrize("country", ["../secret", "narnia"])
def test_only_known_countries_are_fetched(data, monkeypatch, country):
    urls = _site(monkeypatch)
    with pytest.raises(tm.PlaceError):
        asyncio.run(tm.camera_cities(country))
    assert not any(country in u for u in urls)


def test_vendor_files_are_in_the_repo():
    root = Path(__file__).parent / "static"
    for name in ("d3-array.min.js", "d3-geo.min.js", "topojson-client.min.js", "land-110m.json", "leaflet.js", "leaflet.css"):
        assert (root / "vendor" / "globe" / name).exists(), name
    html = (root / "travel.html").read_text(encoding="utf-8")
    assert "TravelMap.init" in html and 'id="tm-globe"' in html
