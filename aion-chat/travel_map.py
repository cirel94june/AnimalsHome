"""旅行地球和地图的数据（11-08 小猫）：去过哪里、去过几次、当时的记录；她自己去过的地方；世界窗目录。

- 老的旅行记录只存了地名，第一次打开地图时按地名慢慢补坐标（OSM 每秒最多一次）；新的旅行出发时直接记下坐标。
- 坐标、她去过的地方都只存在服务器 data/ 里。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
import uuid
from typing import Any

import travel_places

log = logging.getLogger("travel_map")

GEO_GAP = 1.2
DIRECTORY_SECONDS = 7 * 24 * 3600
COUNTRY_NAMES = {
    "italia": "意大利", "espana": "西班牙", "france": "法国", "ellada": "希腊", "deutschland": "德国", "united-kingdom": "英国",
    "united-states": "美国", "japan": "日本", "china": "中国", "thailand": "泰国", "brasil": "巴西", "australia": "澳大利亚",
    "austria": "奥地利", "canada": "加拿大", "czech-republic": "捷克", "hungary": "匈牙利", "iceland": "冰岛", "ireland": "爱尔兰",
    "maldives": "马尔代夫", "malta": "马耳他", "hrvatska": "克罗地亚", "netherlands": "荷兰", "norway": "挪威", "poland": "波兰",
    "portugal": "葡萄牙", "switzerland": "瑞士", "turkey": "土耳其", "egypt": "埃及", "mexico": "墨西哥", "israel": "以色列",
}
COUNTRY_NAMES.update({"south-korea": "韩国", "india": "印度", "indonesia": "印度尼西亚", "vietnam": "越南", "philippines": "菲律宾",
                      "morocco": "摩洛哥", "south-africa": "南非", "new-zealand": "新西兰", "denmark": "丹麦", "finland": "芬兰"})
# 总目录页没列全（日本、美国等都不在上面），这些是 11-08 逐个核对过页面上真有摄像头的
EXTRA_COUNTRIES = ("japan", "united-states", "united-kingdom", "thailand", "turkey", "mexico", "netherlands", "poland", "portugal",
                   "south-korea", "india", "indonesia", "vietnam", "philippines", "morocco", "south-africa", "new-zealand",
                   "denmark", "finland")
_COUNTRY_LINK = re.compile(r"webcam/([a-z-]+)\.html")
_lock = threading.Lock()
_filling: asyncio.Task | None = None
_looking: set[str] = set()
_tasks: set[asyncio.Task] = set()


def _file(name: str):
    from config import DATA_DIR
    return DATA_DIR / name


def _read(name: str, default: Any) -> Any:
    try:
        return json.loads(_file(name).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception as error:
        log.warning("[地图] 读 %s 失败：%s", name, type(error).__name__)
        return default


def _write(name: str, data: Any) -> None:
    path = _file(name)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def remember(label: str, lat: float | None, lng: float | None) -> None:
    """记下一个地方的坐标（出发时调用）。"""
    if not label or lat is None or lng is None:
        return
    with _lock:
        geo = _read("travel_geo.json", {})
        geo[label] = {"lat": lat, "lng": lng}
        _write("travel_geo.json", geo)


def _session_base(card: dict[str, Any]) -> str:
    session, actor = str(card.get("session_id") or card.get("id")), str(card.get("actor") or "")
    return session[: -len(actor) - 1] if actor and session.endswith(f"_{actor}") else session


def _photos(card: dict[str, Any], source: dict[str, Any]) -> list[str]:
    paths = [str(p.get("path") or "") for p in source.get("photos") or [] if isinstance(p, dict)]
    return list(dict.fromkeys(p for p in [str(card.get("photo_path") or ""), *paths] if p.startswith("/uploads/")))[:8]


async def _cards() -> list[dict[str, Any]]:
    import actors
    from autonomy_niches import list_niche_cards
    out = []
    for actor in actors.list_actors(include_disabled=False):
        for card in await list_niche_cards(actor["id"], limit=300):
            if "旅行" in (card.get("tags") or []):
                out.append({**card, "actor": actor["id"], "actor_name": actor["name"]})
    return out


def build_pins(cards: list[dict[str, Any]], geo: dict[str, Any], mine: list[dict[str, Any]]) -> tuple[list[dict], list[str]]:
    """把旅行记录按地方合起来。同一趟（全家出游）只算一次。返回（标记，还没有坐标的地名）。"""
    places: dict[str, dict[str, Any]] = {}
    for card in sorted(cards, key=lambda c: c.get("created_at") or 0):
        sources = card.get("sources") or []
        source = sources[0] if sources and isinstance(sources[0], dict) else {}
        label = str(source.get("place") or "").strip()
        if not label:
            continue
        place = places.setdefault(label, {"label": label, "name": str(source.get("title") or label), "visits": {}})
        visit = place["visits"].setdefault(_session_base(card), {"at": card.get("created_at") or 0, "people": [], "parts": [],
                                                                  "photos": [], "weather": source.get("weather") or ""})
        if card["actor"] not in [p["id"] for p in visit["people"]]:
            visit["people"].append({"id": card["actor"], "name": card["actor_name"]})
        visit["parts"].append({"who": card["actor_name"], "title": card.get("title") or "", "text": card.get("reflection") or ""})
        visit["photos"] = list(dict.fromkeys(visit["photos"] + _photos(card, source)))[:8]
    pins, missing = [], []
    for label, place in places.items():
        point = geo.get(label)
        if not point:
            missing.append(label)
            continue
        if point.get("none"):
            continue
        visits = sorted(place["visits"].values(), key=lambda v: v["at"], reverse=True)
        people: dict[str, dict] = {}
        for visit in visits:
            people.update({p["id"]: p for p in visit["people"]})
        pins.append({"kind": "ai", "label": label, "name": place["name"], "lat": point["lat"], "lng": point["lng"],
                     "count": len(visits), "people": list(people.values()), "visits": visits})
    grouped: dict[str, dict[str, Any]] = {}
    for item in mine:
        pin = grouped.setdefault(item["label"], {"kind": "me", "label": item["label"], "name": item["name"], "lat": item["lat"],
                                                 "lng": item["lng"], "count": 0, "people": [], "visits": []})
        pin["count"] += 1
        pin["visits"].append({"id": item["id"], "at": item.get("at") or 0, "date": item.get("date") or "", "note": item.get("note") or ""})
    for pin in grouped.values():
        pin["visits"].sort(key=lambda v: v["at"], reverse=True)
    return pins + list(grouped.values()), missing


async def _fill_missing(labels: list[str]) -> None:
    async with travel_places._client() as client:
        for label in labels:
            await asyncio.sleep(GEO_GAP)
            parts = [p for p in re.split(r"[，,]", re.sub(r";[^，]*", "", label)) if p.strip()]
            queries = list(dict.fromkeys([" ".join([parts[0], parts[-1]]), parts[0]])) if parts else []
            found = None
            for query in queries:
                try:
                    found = await travel_places._geocode_osm(client, query)
                except Exception as error:
                    log.info("[地图] 补坐标失败：%s", type(error).__name__)
                if found:
                    break
                await asyncio.sleep(GEO_GAP)
            with _lock:
                geo = _read("travel_geo.json", {})
                geo[label] = {"lat": found.latitude, "lng": found.longitude} if found else {"none": True}
                _write("travel_geo.json", geo)


async def map_data() -> dict[str, Any]:
    global _filling
    pins, missing = build_pins(await _cards(), _read("travel_geo.json", {}), _read("travel_my_places.json", []))
    if missing and (_filling is None or _filling.done()):
        _filling = asyncio.create_task(_fill_missing(missing))
    return {"pins": pins, "pending": len(missing)}


class PlaceError(Exception):
    """给小猫看的话。"""


async def add_my_place(name: str = "", *, near: bool = False, note: str = "", date: str = "") -> dict[str, Any]:
    import world_window
    if near:
        import travel
        name = await travel.near_user_city()
        if not name:
            raise PlaceError("定位里还没有你的位置")
    name = str(name or "").strip()[:40]
    if not name:
        raise PlaceError("写一下地名")
    async with travel_places._client() as client:
        place = await world_window._world_place(client, name)
    if place is None or place.latitude is None or place.longitude is None:
        raise PlaceError(f"没找到「{name}」这个地方")
    date = str(date or "").strip()
    if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise PlaceError("日期不对")
    record = {"id": uuid.uuid4().hex[:12], "name": place.name, "label": re.sub(r";[^，]*", "", place.label),
              "lat": place.latitude, "lng": place.longitude, "note": str(note or "").strip()[:200], "date": date, "at": time.time()}
    with _lock:
        mine = _read("travel_my_places.json", [])
        mine.append(record)
        _write("travel_my_places.json", mine)
    return record


def delete_my_place(place_id: str) -> bool:
    with _lock:
        mine = _read("travel_my_places.json", [])
        left = [m for m in mine if m.get("id") != place_id]
        if len(left) == len(mine):
            return False
        _write("travel_my_places.json", left)
    return True


# ── 世界窗目录 ──

def _title(slug: str) -> str:
    return slug.replace("-", " ").title()


async def camera_countries() -> list[dict[str, str]]:
    import world_window
    cached = _read("travel_cameras.json", {})
    fresh = cached.get("countries") and time.time() - cached.get("at", 0) < DIRECTORY_SECONDS
    if fresh and set(EXTRA_COUNTRIES) <= {c["slug"] for c in cached["countries"]}:
        return cached["countries"]
    async with travel_places._client() as client:
        page = await world_window._text(client, f"{world_window.SITE}/webcam.html")
    slugs = sorted(set(_COUNTRY_LINK.findall(page)))
    if not slugs:
        raise PlaceError("摄像头目录这会儿读不到")
    slugs = sorted(set(slugs) | set(EXTRA_COUNTRIES))
    countries = sorted(({"slug": s, "name": COUNTRY_NAMES.get(s, _title(s))} for s in slugs),
                       key=lambda c: (c["slug"] not in COUNTRY_NAMES, c["name"]))
    with _lock:
        data = _read("travel_cameras.json", {})
        data.update(countries=countries, at=time.time())
        _write("travel_cameras.json", data)
    return countries


async def camera_cities(country: str) -> list[dict[str, Any]]:
    import world_window
    if not re.fullmatch(r"[a-z-]{2,60}", country or ""):
        raise PlaceError("国家不对")
    known = {c["slug"] for c in await camera_countries()}
    if country not in known:
        raise PlaceError("这个国家没有摄像头")
    cached = (_read("travel_cameras.json", {}).get("cities") or {}).get(country)
    if cached and time.time() - cached.get("at", 0) < DIRECTORY_SECONDS:
        return cached["list"]
    async with travel_places._client() as client:
        page = await world_window._text(client, f"{world_window.SITE}/webcam/{country}.html")
    counts: dict[str, int] = {}
    for found_country, _region, city, name in world_window._CAM_PATH.findall(page):
        if found_country == country:
            counts.setdefault(city, 0)
            counts[city] += 1
    cities = sorted(({"slug": s, "name": _title(s), "cams": n} for s, n in counts.items()), key=lambda c: c["name"])
    with _lock:
        data = _read("travel_cameras.json", {})
        data.setdefault("cities", {})[country] = {"at": time.time(), "list": cities}
        _write("travel_cameras.json", data)
    return cities


def send_to_look(actor: str, place: str) -> None:
    """她在目录里点一座城市：这位 AI 去看，回来在私聊里说。"""
    import actors
    import autonomy
    import world_window
    from capabilities import is_capability_enabled
    place = str(place or "").strip()[:40]
    if actor not in {a["id"] for a in actors.list_actors(include_disabled=False)}:
        raise PlaceError("没有这位 AI")
    if not place:
        raise PlaceError("选一个地方")
    if not is_capability_enabled("world_window"):
        raise PlaceError("世界窗在能力开关里关着")
    if actor in _looking:
        raise PlaceError("TA 正在看别的地方，等一下")
    _looking.add(actor)

    async def deliver(reply: str, photos: list[str]) -> None:
        await autonomy._save_private_message(actor, reply, photos, force_private=True, auto_tts=False)

    async def notify(message: str) -> None:
        await autonomy._save_private_message(actor, message, force_private=True, auto_tts=False)

    async def run() -> None:
        try:
            await world_window._run(actor, place, f"想看看{place}", deliver, notify, None)
        finally:
            _looking.discard(actor)
    task = asyncio.create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
