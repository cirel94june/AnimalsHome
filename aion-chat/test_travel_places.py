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


async def _stream_body(data: bytes, size: int = 64 * 1024):
    for i in range(0, len(data), size):
        yield data[i:i + size]


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
            # 用流式 body：bytes 形式的 Response 会在构造时被 httpx 直接读完，和真实网络不一样
            return httpx.Response(200, content=_stream_body(PNG + path.encode()), headers={"content-type": "image/png"})
        return httpx.Response(404)
    return handle


@pytest.fixture
def net(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "TRAVEL_PHOTO_DIR", tmp_path / "travel")
    monkeypatch.setattr(tp, "_amap_key", lambda: "")  # 不读本机的定位设置

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


def _streaming_client(sent: list, *, chunks=20, ctype="image/jpeg", length=None):
    async def body():
        for _ in range(chunks):
            sent.append(1)
            yield b"0" * (1024 * 1024)

    def handle(request):
        headers = {"content-type": ctype}
        if length is not None:
            headers["content-length"] = str(length)
        return httpx.Response(200, headers=headers, content=body())
    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def test_download_stops_as_soon_as_it_passes_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "TRAVEL_PHOTO_DIR", tmp_path / "travel")

    async def run(**kw):
        sent = []
        async with _streaming_client(sent, **kw) as client:
            with pytest.raises(ValueError):
                await tp._download(client, "https://upload.example/big.jpg")
        return len(sent)

    assert asyncio.run(run()) <= 7  # 20MB 的流，读到 6MB 出头就停
    assert asyncio.run(run(length=50 * 1024 * 1024)) == 0  # 声明的大小超限，一个字节都不读
    assert asyncio.run(run(ctype="image/gif")) == 0  # 不支持的类型不读
    assert asyncio.run(run(ctype="text/html")) == 0
    assert not (tmp_path / "travel").exists() or not list((tmp_path / "travel").iterdir())


def test_compressed_image_is_refused_before_any_decompression(tmp_path, monkeypatch):
    import gzip
    monkeypatch.setattr(tp, "TRAVEL_PHOTO_DIR", tmp_path / "travel")
    bomb = gzip.compress(b"\0" * (32 * 1024 * 1024))  # 不到 64KB，解压是 32MB
    decoded = []
    real_decode = httpx._decoders.GZipDecoder.decode

    def spy(self, data):
        out = real_decode(self, data)
        decoded.append(len(out))
        return out

    monkeypatch.setattr(httpx._decoders.GZipDecoder, "decode", spy)
    seen_headers = {}

    def handle(request):
        seen_headers.update(request.headers)
        return httpx.Response(200, headers={"content-type": "image/jpeg", "content-encoding": "gzip"},
                              content=_stream_body(bomb))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(ValueError, match="压缩"):
                await tp._download(client, "https://upload.example/bomb.jpg")

    asyncio.run(run())
    assert seen_headers.get("accept-encoding") == "identity"
    assert decoded == []  # 一个字节都没解压


def test_unknown_place_is_an_error(net):
    net(found=False)
    with pytest.raises(ValueError, match="没找到"):
        asyncio.run(tp.lookup("不存在的地方"))
    with pytest.raises(ValueError):
        asyncio.run(tp.lookup("  "))


def _amap_net(monkeypatch, *, geo=None, poi=None, fail=False):
    seen = []

    def handle(request):
        seen.append(request.url.host + request.url.path)
        if request.url.host == "restapi.amap.com":
            if fail:
                return httpx.Response(500)
            if request.url.path == "/v3/geocode/geo":
                return httpx.Response(200, json={"status": "1", "geocodes": geo or []})
            return httpx.Response(200, json={"status": "1", "pois": poi or []})
        if request.url.host == "nominatim.openstreetmap.org":
            return httpx.Response(200, json=[{"name": "大理市", "lat": "25.6", "lon": "100.26",
                                              "address": {"state": "云南省", "country": "中国"}}])
        return _handler(weather=False, wiki=False, commons=False)(request)
    monkeypatch.setattr(tp, "_amap_key", lambda: "k")
    monkeypatch.setattr(tp, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    return seen


def test_amap_is_used_first_for_chinese_names_when_key_is_set(net, monkeypatch):
    seen = _amap_net(monkeypatch, geo=[{"location": "100.22,25.59", "province": "云南省", "city": "大理白族自治州"}])
    place = asyncio.run(tp.lookup("大理古城"))
    assert place.label == "大理古城，大理白族自治州，中国" and place.longitude == 100.22
    assert not any("nominatim" in u for u in seen)


def test_amap_falls_back_to_place_search_then_osm(net, monkeypatch):
    _amap_net(monkeypatch, poi=[{"name": "成都太古里", "location": "104.08,30.65", "cityname": "成都市", "pname": "四川省"}])
    assert asyncio.run(tp.lookup("太古里")).label == "成都太古里，成都市，中国"
    _amap_net(monkeypatch, fail=True)
    assert asyncio.run(tp.lookup("大理")).label == "大理市，云南省，中国"  # 高德出错就照旧用 OSM


def test_amap_is_skipped_for_non_chinese_names(net, monkeypatch):
    seen = _amap_net(monkeypatch)
    asyncio.run(tp.lookup("Dali"))
    assert not any("amap" in u for u in seen)
