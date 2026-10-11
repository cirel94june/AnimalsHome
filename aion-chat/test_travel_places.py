import asyncio
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import travel_places as tp

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _handler(*, weather=True, wiki=True, commons=True, found=True):
    def handle(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "nominatim.openstreetmap.org":
            return httpx.Response(200, json=[])  # OSM 查不到时退到 Open-Meteo
        if host == "geocoding-api.open-meteo.com":
            results = [{"name": "京都市", "country": "日本", "admin1": "京都府", "latitude": 35.0,
                        "longitude": 135.7, "timezone": "Asia/Tokyo"}] if found else []
            return httpx.Response(200, json={"results": results})
        if host == "api.open-meteo.com":
            if not weather:
                return httpx.Response(500)
            return httpx.Response(200, json={"utc_offset_seconds": 32400,
                                             "current": {"temperature_2m": 18.4, "weather_code": 61, "is_day": 0}})
        if host.endswith("wikipedia.org"):
            if not wiki or host.startswith("en."):
                return httpx.Response(404)
            return httpx.Response(200, json={"type": "standard", "extract": "京都是日本的古都。",
                                             "content_urls": {"desktop": {"page": "https://zh.wikipedia.org/wiki/京都市"}},
                                             "originalimage": {"source": "https://upload.example/lead.png"}})
        if host == "commons.wikimedia.org":
            if not commons:
                return httpx.Response(500)
            return httpx.Response(200, json={"query": {"pages": {
                "2": {"index": 2, "imageinfo": [{"thumburl": "https://upload.example/b.png"}]},
                "1": {"index": 1, "imageinfo": [{"thumburl": "https://upload.example/a.png",
                                                 "extmetadata": {"Artist": {"value": "<a>某摄影师</a>"}}}]},
            }}})
        if host == "upload.example":
            return httpx.Response(200, content=PNG + path.encode(), headers={"content-type": "image/png"})
        return httpx.Response(404)
    return handle


@pytest.fixture
def net(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "TRAVEL_PHOTO_DIR", tmp_path / "travel")

    def use(**kw):
        monkeypatch.setattr(tp, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(_handler(**kw))))
    return use


def test_lookup_collects_weather_intro_and_photos(net, tmp_path):
    net()
    place = asyncio.run(tp.lookup("京都"))
    assert place.label == "京都市，京都府，日本"
    assert place.weather == "小雨，夜里，18°C" and place.local_time
    assert place.intro == "京都是日本的古都。"
    assert [p["credit"] for p in place.photos] == ["Wikipedia", "Wikimedia Commons · 某摄影师", "Wikimedia Commons"]
    assert all(p["path"].startswith("/uploads/travel/") for p in place.photos)
    assert len(list((tmp_path / "travel").iterdir())) == 3
    brief = place.brief()
    assert "此刻天气：小雨" in brief and "3 张" in brief


def test_missing_parts_are_left_out_not_invented(net):
    net(weather=False, wiki=False, commons=False)
    place = asyncio.run(tp.lookup("京都"))
    assert place.weather == "" and place.intro == "" and place.photos == []
    assert place.brief() == "地点：京都市，京都府，日本"


def test_osm_result_is_preferred(net, monkeypatch):
    def handle(request):
        if request.url.host == "nominatim.openstreetmap.org":
            return httpx.Response(200, json=[{"name": "大理市", "lat": "25.6", "lon": "100.26",
                                              "address": {"state": "云南省", "country": "中国"}}])
        return _handler(weather=False, wiki=False, commons=False)(request)
    monkeypatch.setattr(tp, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    place = asyncio.run(tp.lookup("大理"))
    assert place.label == "大理市，云南省，中国" and place.latitude == 25.6


def test_lead_photo_uses_a_1280px_version():
    summary = {"thumbnail": {"source": "https://upload.wikimedia.org/a/thumb/x/320px-City.jpg"},
               "originalimage": {"source": "https://upload.wikimedia.org/a/x/City.jpg"}}
    assert tp._sized_image(summary) == "https://upload.wikimedia.org/a/thumb/x/1280px-City.jpg"
    assert tp._sized_image({"originalimage": {"source": "https://o.example/a.png"}}) == "https://o.example/a.png"


def test_unknown_place_is_an_error(net):
    net(found=False)
    with pytest.raises(ValueError, match="没找到"):
        asyncio.run(tp.lookup("不存在的地方"))
    with pytest.raises(ValueError):
        asyncio.run(tp.lookup("  "))
