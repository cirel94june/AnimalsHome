"""世界窗（11-08 小猫）：AI 写 [看看:地名]，家里查那个地方此刻的天气、当地时间和公开摄像头画面，
发回聊天，AI 接着说。旅行也用这里的摄像头（travel_places.lookup）。

- 只取 SkylineWebcams 公开的预览图，同一地方 10 分钟内不重复取；没有摄像头就只有天气和时间，不编画面。
- 网上来的东西不当指令：AI 接着说的话只清除动作标签，不执行。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

import mcp_policy
import travel_places

log = logging.getLogger("world_window")

SITE = "https://www.skylinewebcams.com/en"
IMAGE = "https://cdn.skylinewebcams.com/social{}.jpg"
CREDIT = "此刻的公开摄像头 · SkylineWebcams"
CACHE_SECONDS = 600
MAX_PAGE_BYTES = 2 * 1024 * 1024

# 这个网站的国家路径和英文国名对不上的
COUNTRY_SLUG = {
    "spain": "espana", "italy": "italia", "greece": "ellada", "germany": "deutschland", "brazil": "brasil",
    "the netherlands": "netherlands", "czechia": "czech-republic", "republic of türkiye": "turkey", "türkiye": "turkey",
}
CITY_ALIAS = {
    "rome": ["roma"], "venice": ["venezia"], "milan": ["milano"], "florence": ["firenze"], "naples": ["napoli"],
    "prague": ["praha"], "vienna": ["wien"], "athens": ["athina"], "cologne": ["koln"], "munich": ["munchen"],
}
_CAM_PATH = re.compile(r"webcam/([a-z-]+)/([a-z-]+)/([a-z-]+)/([a-z0-9-]+)\.html")
_SOCIAL = re.compile(r"social(\d+)\.jpg")


@dataclass
class Look:
    label: str
    text: str
    photo: str = ""  # /uploads/travel/x.jpg，没有摄像头就是空串


_cache: dict[str, tuple[float, Look]] = {}
_tasks: set[asyncio.Task] = set()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


async def _text(client: httpx.AsyncClient, url: str, **kwargs) -> str:
    response = await client.get(url, **kwargs)
    response.raise_for_status()
    if len(response.content) > MAX_PAGE_BYTES:
        raise ValueError("页面太大")
    return response.text


async def english_area(client: httpx.AsyncClient, lat: float, lon: float) -> tuple[str, str]:
    """坐标 → 英文城市名和国家名（地名可能是中文，摄像头网站的路径是英文）。"""
    response = await client.get("https://nominatim.openstreetmap.org/reverse", params={
        "lat": lat, "lon": lon, "zoom": 10, "format": "jsonv2", "accept-language": "en"})
    response.raise_for_status()
    address = response.json().get("address") or {}
    city = next((address[k] for k in ("city", "town", "municipality", "village", "county", "state") if address.get(k)), "")
    return str(city), str(address.get("country") or "")


def match_camera(page: str, city: str) -> str:
    """在国家页里找这座城市的摄像头路径。找不到返回空串。"""
    wanted = [_slug(city)] + CITY_ALIAS.get(city.lower(), [])
    wanted = [w for w in wanted if w]
    for country, region, place, name in _CAM_PATH.findall(page):
        for key in wanted:
            if place == key or (len(key) >= 4 and (key in place or re.search(rf"(^|-){re.escape(key)}($|-)", name))):
                return f"webcam/{country}/{region}/{place}/{name}.html"
    return ""


async def camera_photo(client: httpx.AsyncClient, place: travel_places.Place) -> str:
    """这个地方此刻的摄像头画面，存本地返回路径。没有就是空串，出错也是空串。"""
    if place.latitude is None or place.longitude is None:
        return ""
    try:
        city, country = await english_area(client, place.latitude, place.longitude)
        if not city or not country:
            return ""
        lowered = country.lower()
        page = await _text(client, f"{SITE}/webcam/{COUNTRY_SLUG.get(lowered) or _slug(country)}.html")
        path = match_camera(page, city)
        if not path:
            return ""
        found = _SOCIAL.search(await _text(client, f"{SITE}/{path}"))
        return await travel_places._download(client, IMAGE.format(found.group(1))) if found else ""
    except Exception as error:
        log.info("[世界窗] %s 没拿到摄像头：%s", place.name, type(error).__name__)
        return ""


async def look(name: str) -> Look | None:
    """查一个地方。地名查不到返回 None。"""
    key = (name or "").strip().lower()[:40]
    if not key:
        return None
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    async with travel_places._client() as client:
        place = await travel_places.geocode(client, name.strip()[:40])
        if place is None:
            return None
        try:
            await travel_places.fill_weather(client, place)
        except Exception as error:
            log.info("[世界窗] %s 没拿到天气：%s", place.name, type(error).__name__)
        photo = await camera_photo(client, place)
    lines = [f"地点：{place.label}"]
    if place.local_time:
        lines.append(f"当地时间：{place.local_time}")
    if place.weather:
        lines.append(f"此刻天气：{place.weather}")
    lines.append("（附上了这里此刻的公开摄像头画面）" if photo else "（这里没有找到公开摄像头，没有画面）")
    result = Look(place.label, "\n".join(lines), photo)
    _cache[key] = (time.monotonic(), result)
    return result


def model_for(actor: str) -> str:
    import image_gen
    return image_gen._code_model(actor)


async def _reply(actor: str, found: Look, said: str, recent: list[dict]) -> str:
    from ai_providers import CLI_STATUS_PREFIX, stream_ai
    from config import load_worldbook
    from memory_hub_jobs import _persona_messages
    from web_search import strip_untrusted_reply
    user = (load_worldbook().get("user_name") or "").strip() or "她"
    prompt = (f"你刚才说想看看外面（你说的是：{said[:200]}）。这是家里帮你查到的，是真实的此刻：\n{found.text}\n\n"
              f"用你自己的口吻跟{user}说说。只说资料和画面里有的，看不清的别编。"
              "不要再写 [看看:…]。")
    question = {"role": "user", "content": prompt}
    if found.photo:
        question["attachments"] = [found.photo]
    text = ""
    async for chunk in stream_ai([*_persona_messages(actor), *recent, question], model_for(actor)):
        if isinstance(chunk, str) and not chunk.startswith(CLI_STATUS_PREFIX):
            text += chunk
    return strip_untrusted_reply(text).strip()


Deliver = Callable[[str, list[str]], Awaitable[object]]
Notify = Callable[[str], Awaitable[object]]
Recent = Callable[[], Awaitable[list[dict]]]


async def _run(actor: str, name: str, said: str, deliver: Deliver, notify: Notify, recent: Recent | None) -> None:
    try:
        found = await look(name)
        if found is None:
            await notify(f"（世界窗没找到「{name}」这个地方）")
            return
        history = await recent() if recent else []
        text = await _reply(actor, found, said, history)
        await deliver(text or found.text, [found.photo] if found.photo else [])
    except asyncio.CancelledError:
        raise
    except Exception as error:
        log.warning("[世界窗] %s 看 %s 没成功：%s", actor, name, type(error).__name__)
        await notify(f"（世界窗这次没看成「{name}」）")


def process(text: str, actor: str, *, allowed: bool, deliver: Deliver, notify: Notify, recent: Recent | None = None) -> str:
    """把回复里的 [看看:地名] 清掉，后台去看（一条回复只看第一个）。只写了这个指令时留一句话，免得算回复失败。"""
    match = mcp_policy.WORLD_LOOK_CMD_PATTERN.search(text or "")
    if not match:
        return text
    clean = mcp_policy.WORLD_LOOK_CMD_PATTERN.sub("", text).strip()
    name = match.group(1).strip()
    if not allowed:
        return clean
    from capabilities import is_capability_enabled
    if not is_capability_enabled("world_window"):
        return clean
    task = asyncio.create_task(_run(actor, name, clean, deliver, notify, recent))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return clean or f"我看看{name}现在什么样。"


def ability_text(user_name: str) -> str:
    return (f"[看看:地名] — 想看某座城市此刻的样子时用（比如 [看看:东京]）。家里会查当地天气、时间和公开摄像头的实时画面，"
            f"然后你接着跟{user_name}说。不是每个地方都有摄像头，看之前不要编画面。")
