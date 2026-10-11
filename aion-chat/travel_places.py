"""
旅行用的真实资料：地名 → 位置、当地此刻的天气和时间、简介、真实照片。

都是免费的公开服务：
  OpenStreetMap / Open-Meteo（地理编码、天气）、维基百科摘要、维基共享资源（照片）。
  填了高德 Key（定位设置里那个）时，国内地名先用高德查，小地方、景点认得更准。
哪一项拿不到就留空，提示词里不写，不让 AI 编。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

from config import UPLOADS_DIR

log = logging.getLogger("travel_places")

TRAVEL_PHOTO_DIR = UPLOADS_DIR / "travel"
# 维基和 OpenStreetMap 要求 User-Agent 带联系方式，这里放项目主页（不放任何个人信息）
USER_AGENT = "AionsHome/1.0 (+https://github.com/cirel94june/AnimalsHome)"
TIMEOUT = 15.0
MAX_PHOTOS = 3
MAX_PHOTO_BYTES = 6 * 1024 * 1024

WEATHER_CODES = {
    0: "晴", 1: "大致晴朗", 2: "多云", 3: "阴",
    45: "有雾", 48: "雾凇", 51: "毛毛雨", 53: "毛毛雨", 55: "较大的毛毛雨",
    56: "冻毛毛雨", 57: "冻毛毛雨", 61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨", 71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "阵雨", 81: "阵雨", 82: "强阵雨", 85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷阵雨伴冰雹",
}


@dataclass
class Place:
    name: str
    country: str = ""
    region: str = ""
    latitude: float | None = None
    longitude: float | None = None
    timezone: str = ""
    local_time: str = ""
    weather: str = ""
    intro: str = ""
    wiki_url: str = ""
    photos: list[dict] = field(default_factory=list)  # {"path": "/uploads/travel/x.jpg", "credit": "..."}

    @property
    def label(self) -> str:
        parts = [self.name] + [p for p in (self.region, self.country) if p and p != self.name]
        return "，".join(parts)

    def brief(self) -> str:
        """给 AI 看的资料；没有的项不写。"""
        lines = [f"地点：{self.label}"]
        if self.local_time:
            lines.append(f"当地时间：{self.local_time}")
        if self.weather:
            lines.append(f"此刻天气：{self.weather}")
        if self.intro:
            lines.append(f"简介：{self.intro}")
        if self.photos:
            lines.append(f"（拿到了 {len(self.photos)} 张这里的真实照片，会附在游记里）")
        return "\n".join(lines)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


async def geocode(client: httpx.AsyncClient, name: str) -> Place | None:
    """有高德 Key 且是中文地名时先用高德；再用 OpenStreetMap（中文和「国家+城市」这种写法认得好），
    都查不到再用 Open-Meteo。"""
    key = _amap_key()
    if key and _has_cjk(name):
        try:
            place = await _geocode_amap(client, name, key)
            if place:
                return place
        except Exception as error:
            # 不记异常全文：高德的 Key 在请求地址里，HTTPStatusError 的文字会带上它
            status = getattr(getattr(error, "response", None), "status_code", "")
            log.info("[travel] 高德查地名失败：%s %s", type(error).__name__, status)
    try:
        place = await _geocode_osm(client, name)
        if place:
            return place
    except Exception as error:
        log.info("[travel] OSM 查地名失败：%s", error)
    return await _geocode_open_meteo(client, name)


def _amap_key() -> str:
    try:
        from location import load_location_config
        return str(load_location_config().get("amap_key") or "").strip()
    except Exception:
        return ""


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text or "")


def _amap_point(location: str) -> tuple[float, float] | None:
    try:
        lng, lat = (float(v) for v in str(location).split(",", 1))
        return lng, lat
    except (TypeError, ValueError):
        return None


async def _geocode_amap(client: httpx.AsyncClient, name: str, key: str) -> Place | None:
    """高德只覆盖国内：先按地址查（城市、区县、街道），查不到再按地点名查（景点、商圈）。
    坐标是高德的 GCJ-02，和真实位置差几百米，查天气够用。"""
    r = await client.get("https://restapi.amap.com/v3/geocode/geo", params={"key": key, "address": name})
    r.raise_for_status()
    data = r.json()
    for g in (data.get("geocodes") or []) if str(data.get("status")) == "1" else []:
        point = _amap_point(g.get("location"))
        if point:
            region = g.get("province") or ""
            city = g.get("city") if isinstance(g.get("city"), str) else ""
            return Place(name=name, country="中国", region=city or region,
                         latitude=point[1], longitude=point[0], timezone="Asia/Shanghai")
    r = await client.get("https://restapi.amap.com/v3/place/text",
                         params={"key": key, "keywords": name, "offset": 1, "page": 1})
    r.raise_for_status()
    data = r.json()
    for poi in (data.get("pois") or []) if str(data.get("status")) == "1" else []:
        point = _amap_point(poi.get("location"))
        if point:
            city = poi.get("cityname") if isinstance(poi.get("cityname"), str) else ""
            return Place(name=poi.get("name") or name, country="中国",
                         region=city or (poi.get("pname") if isinstance(poi.get("pname"), str) else ""),
                         latitude=point[1], longitude=point[0], timezone="Asia/Shanghai")
    return None


async def _geocode_osm(client: httpx.AsyncClient, name: str) -> Place | None:
    r = await client.get("https://nominatim.openstreetmap.org/search", params={
        "q": name, "format": "jsonv2", "limit": 1, "accept-language": "zh", "addressdetails": 1,
    })
    r.raise_for_status()
    results = r.json()
    if not results:
        return None
    g = results[0]
    addr = g.get("address") or {}
    return Place(
        name=g.get("name") or name, country=addr.get("country", ""),
        region=addr.get("state") or addr.get("province") or addr.get("region", ""),
        latitude=float(g["lat"]), longitude=float(g["lon"]),
    )


async def _geocode_open_meteo(client: httpx.AsyncClient, name: str) -> Place | None:
    r = await client.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": name, "count": 1, "language": "zh", "format": "json"})
    r.raise_for_status()
    results = r.json().get("results") or []
    if not results:
        return None
    g = results[0]
    return Place(name=g.get("name") or name, country=g.get("country") or "", region=g.get("admin1") or "",
                 latitude=g.get("latitude"), longitude=g.get("longitude"), timezone=g.get("timezone") or "")


async def fill_weather(client: httpx.AsyncClient, place: Place) -> None:
    r = await client.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": place.latitude, "longitude": place.longitude, "timezone": "auto",
        "current": "temperature_2m,weather_code,is_day,wind_speed_10m",
    })
    r.raise_for_status()
    data = r.json()
    cur = data.get("current") or {}
    if "temperature_2m" in cur:
        desc = WEATHER_CODES.get(int(cur.get("weather_code", -1)), "")
        night = "" if cur.get("is_day", 1) else "，夜里"
        place.weather = f"{desc}{night}，{round(cur['temperature_2m'])}°C".lstrip("，")
    offset = data.get("utc_offset_seconds")
    if offset is not None:
        local = datetime.now(timezone.utc) + timedelta(seconds=int(offset))
        place.local_time = local.strftime("%m月%d日 %H:%M")


async def fill_intro(client: httpx.AsyncClient, place: Place) -> str:
    """维基百科摘要；返回原图地址（如果有）作第一张照片。"""
    for lang in ("zh", "en"):
        r = await client.get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(place.name)}")
        if r.status_code != 200:
            continue
        data = r.json()
        if data.get("type") == "disambiguation" or not data.get("extract"):
            continue
        place.intro = data["extract"][:600]
        place.wiki_url = ((data.get("content_urls") or {}).get("desktop") or {}).get("page", "")
        return _sized_image(data)
    return ""


def _sized_image(summary: dict, width: int = 1280) -> str:
    """原图常有好几 MB；优先把缩略图地址换成 1280px 宽的版本。"""
    import re
    thumb = (summary.get("thumbnail") or {}).get("source", "")
    if thumb and re.search(r"/\d+px-", thumb):
        return re.sub(r"/\d+px-", f"/{width}px-", thumb, count=1)
    return (summary.get("originalimage") or {}).get("source", "") or thumb


async def commons_photos(client: httpx.AsyncClient, place: Place, limit: int) -> list[dict]:
    r = await client.get("https://commons.wikimedia.org/w/api.php", params={
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
        "gsrsearch": f"filetype:bitmap {place.name}", "gsrlimit": limit * 2,
        "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": 1280,
    })
    r.raise_for_status()
    pages = sorted(((r.json().get("query") or {}).get("pages") or {}).values(), key=lambda p: p.get("index", 0))
    out = []
    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("thumburl") or info.get("url")
        if not url:
            continue
        artist = ((info.get("extmetadata") or {}).get("Artist") or {}).get("value", "")
        out.append({"url": url, "credit": f"Wikimedia Commons{(' · ' + _strip_html(artist)) if artist else ''}"})
        if len(out) >= limit:
            break
    return out


def _strip_html(text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", text or "").strip()[:80]


PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
DOWNLOAD_SECONDS = 30.0


async def _download(client: httpx.AsyncClient, url: str) -> str:
    """边下边数：超过上限立刻停，不把整张大图先读进内存（服务器只有 1G）。"""
    async def fetch() -> tuple[bytes, str]:
        # 要求不压缩，并且只按原始字节读（aiter_raw 不解压）：压缩包解压出来的大块根本不会产生
        async with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as r:
            r.raise_for_status()
            encoding = r.headers.get("content-encoding", "").strip().lower()
            if encoding not in ("", "identity"):
                raise ValueError(f"图片被压缩传输（{encoding}），不收")
            ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype not in PHOTO_TYPES:
                raise ValueError(f"不是支持的图片类型：{ctype or '未知'}")
            declared = r.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > MAX_PHOTO_BYTES:
                raise ValueError("图片太大")
            chunks, total = [], 0
            async for chunk in r.aiter_raw(64 * 1024):
                total += len(chunk)
                if total > MAX_PHOTO_BYTES:
                    raise ValueError("图片太大")
                chunks.append(chunk)
            return b"".join(chunks), ctype

    content, ctype = await asyncio.wait_for(fetch(), timeout=DOWNLOAD_SECONDS)
    TRAVEL_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(content).hexdigest()[:16] + PHOTO_TYPES[ctype]
    (TRAVEL_PHOTO_DIR / name).write_bytes(content)
    return f"/uploads/travel/{name}"


async def lookup(name: str, *, photos: int = MAX_PHOTOS) -> Place:
    """查一个地方的全部资料。地名查不到时抛 ValueError；其他项失败只是留空。"""
    name = (name or "").strip()[:80]
    if not name:
        raise ValueError("没有地名")
    async with _client() as client:
        place = await geocode(client, name)
        if place is None:
            raise ValueError(f"没找到「{name}」这个地方")
        for step in (fill_weather,):
            try:
                await step(client, place)
            except Exception as error:
                log.info("[travel] %s 天气没拿到：%s", place.name, error)
        candidates: list[dict] = []
        try:
            lead = await fill_intro(client, place)
            if lead:
                candidates.append({"url": lead, "credit": "Wikipedia"})
        except Exception as error:
            log.info("[travel] %s 简介没拿到：%s", place.name, error)
        if photos > len(candidates):
            try:
                candidates += await commons_photos(client, place, photos - len(candidates))
            except Exception as error:
                log.info("[travel] %s 照片没拿到：%s", place.name, error)
        for c in candidates[:photos]:
            try:
                place.photos.append({"path": await _download(client, c["url"]), "credit": c["credit"]})
            except Exception as error:
                log.info("[travel] 照片下载失败：%s", error)
    return place
