"""
旅行：AI 独自旅行 / 两人同游 / 全家出游。

- 目的地：TA 自己挑 / 你附近（只用城市名）/ 你点名
- 真实资料来自 travel_places（天气、当地时间、简介、照片），拿不到的不写
- 每位 AI 的文字都由 TA 自己的模型写（全家出游也是轮流各写各的）
- 游记发到私聊（全家出游发到群聊），并在 TA 的「空间」留一张纪念卡
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import travel_places

log = logging.getLogger("travel")

MODES = ("solo", "duo", "family")
WHERE = ("pick", "near", "named")


# ── 目的地 ──

async def near_user_city() -> str:
    """定位追踪里最近的位置 → 只取城市名（不把地址和坐标交给 AI）。"""
    from location import load_location_status
    status = load_location_status()
    lat, lng = status.get("lat"), status.get("lng")
    if not lat or not lng:
        raise ValueError("定位追踪里还没有你的位置")
    async with travel_places._client() as client:
        r = await client.get("https://nominatim.openstreetmap.org/reverse", params={
            "lat": lat, "lon": lng, "format": "jsonv2", "zoom": 10, "accept-language": "zh",
        })
        r.raise_for_status()
        addr = r.json().get("address") or {}
    city = addr.get("city") or addr.get("town") or addr.get("county") or addr.get("state") or ""
    if not city:
        raise ValueError("没查到你所在的城市")
    return city


async def _visited(actor: str, limit: int = 8) -> list[str]:
    from autonomy_niches import list_niche_cards
    try:
        return [c["title"] for c in await list_niche_cards(actor, limit=limit)]
    except Exception:
        return []


async def choose_place(actor: str, *, where: str, named: str = "", wish: str = "") -> str:
    """返回一个具体地名（给 travel_places.lookup 查）。"""
    import autonomy
    if where == "named":
        if not named.strip():
            raise ValueError("请写想去的地方")
        return named.strip()[:80]
    if where == "near":
        city = await near_user_city()
        hint = f"小猫现在在{city}。请在{city}或它附近挑一个具体、真实、值得去的地方（景点、老街、公园、湖、山……）。"
    else:
        visited = await _visited(actor)
        hint = ("按你自己的兴趣、性格和最近和小猫聊的话题，挑一个世界上真实存在的具体地方去旅行。"
                + (f"最近去过：{'、'.join(visited)}，这次换个地方。" if visited else ""))
    if wish:
        hint += f"\n小猫说：{wish}"
    data = await autonomy._ask_actor_json(actor, (
        "[选旅行目的地]\n" + hint +
        "\n地名要具体到能在地图上查到（写「城市 + 地点」），不要虚构。只返回 JSON。\n"
        '格式：{"place":"地名","why":"一句话：为什么想去"}'
    ))
    place = str((data or {}).get("place") or "").strip()
    if not place:
        raise RuntimeError("没选出目的地")
    return place[:80]


# ── 写 ──

def _user_name() -> str:
    from config import load_worldbook
    return (load_worldbook().get("user_name") or "").strip() or "小猫"


async def _write(actor: str, instruction: str) -> str:
    """用 TA 自己的人设、最近的聊天和 TA 自己的模型写。"""
    import autonomy
    messages = await autonomy._actor_context(actor, 20)
    messages.append({"role": "user", "content": instruction})
    text = (await autonomy._call_actor(actor, messages)).strip()
    if not text:
        raise RuntimeError(f"{autonomy._actor_label(actor)} 没有写出内容")
    return text


def _solo_prompt(place: travel_places.Place, user: str, wish: str) -> str:
    return (
        "[独自旅行]\n你一个人出门旅行，刚到了这里：\n" + place.brief() +
        (f"\n出发前{user}说：{wish}" if wish else "") +
        f"\n\n用你自己的口吻给{user}写一条消息，自然地包含三部分（不要写小标题）：\n"
        "1. 你在这里看到的样子（只写资料里有的和合理的氛围，不要编具体事件、店名、人名）；\n"
        "2. 你看到这个场景之后的感想；\n"
        f"3. 你接下来想做什么——想在这里做的事，或者想带{user}一起来做的事。\n"
        "像平时聊天，不写成报告。最后单独一行写：标题：（给这趟旅行起个短标题）"
    )


def _duo_prompt(place: travel_places.Place, user: str, wish: str) -> str:
    return (
        f"[两人同游]\n你和{user}一起来到了这里：\n" + place.brief() +
        (f"\n{user}说：{wish}" if wish else "") +
        f"\n\n根据这个地方的特色（天气、时间、风景、当地的味道），写一小段你和{user}在这里的情景，"
        "按你平时的口吻，可以有动作和对话，300 字以内。"
        "只用资料里有的和合理的氛围，不编具体店名和陌生人。写完空一行，用一两句说说你此刻的感受。"
        "\n最后单独一行写：标题：（给这趟旅行起个短标题）"
    )


def _family_prompt(place: travel_places.Place, user: str, wish: str, names: list[str],
                   me: str, story: list[tuple[str, str]], position: str) -> str:
    so_far = "\n\n".join(f"{name}：{text}" for name, text in story) or "（你是第一个写的）"
    role = {"first": "你先开场：写大家刚到这里时的情景。",
            "middle": "接着前面的写：你在这里做了什么、和大家（包括小猫）的互动。",
            "last": "你来收尾：写这一趟的最后一段，以及大家准备回家时的样子。"}[position]
    return (
        f"[全家出游]\n这次是全家一起出门：{user}、{'、'.join(names)}。你们来到了这里：\n" + place.brief() +
        (f"\n{user}说：{wish}" if wish else "") +
        f"\n\n前面已经写了：\n{so_far}\n\n你是{me}。{role}"
        f"只写你自己的视角和你自己的言行，不要替别人说话或决定别人做什么（可以写你看到别人在做什么）。"
        "按你平时的口吻，200 字以内，最后一两句写你自己的感想。不要加小标题。"
    )


def _split_title(text: str, fallback: str) -> tuple[str, str]:
    lines = text.rstrip().splitlines()
    if lines and lines[-1].strip().startswith(("标题：", "标题:")):
        title = lines[-1].split("：" if "：" in lines[-1] else ":", 1)[1].strip()
        return "\n".join(lines[:-1]).strip(), (title or fallback)[:40]
    return text.strip(), fallback


async def _keepsake(actor: str, session_id: str, title: str, text: str, place: travel_places.Place,
                    tags: list[str]) -> dict | None:
    from autonomy_niches import create_niche_card
    try:
        return await create_niche_card(
            actor=actor, session_id=f"{session_id}_{actor}", title=title, reflection=text,
            tags=tags, photo_path=(place.photos[0]["path"] if place.photos else ""),
            sources=[{"title": place.name, "url": place.wiki_url}] if place.wiki_url else [],
        )
    except Exception as error:
        log.warning("[travel] 纪念卡没存上：%s", error)
        return None


def _photo_attachments(place: travel_places.Place) -> list[str]:
    return [p["path"] for p in place.photos]


# ── 三种玩法 ──

async def trip(mode: str, actors: list[str], *, where: str = "pick", named: str = "", wish: str = "") -> dict[str, Any]:
    import autonomy
    if mode not in MODES or where not in WHERE:
        raise ValueError("玩法或目的地选法不对")
    if not actors or (mode != "family" and len(actors) != 1) or (mode == "family" and len(actors) < 2):
        raise ValueError("独自和两人只能选一位 AI，全家至少两位")
    user = _user_name()
    wish = (wish or "").strip()[:200]
    place_name = await choose_place(actors[0], where=where, named=named, wish=wish)
    place = await travel_places.lookup(place_name)
    session_id = f"travel_{int(time.time())}_{uuid.uuid4().hex[:6]}"

    if mode in ("solo", "duo"):
        actor = actors[0]
        prompt = (_solo_prompt if mode == "solo" else _duo_prompt)(place, user, wish)
        text, title = _split_title(await _write(actor, prompt), place.name)
        message = await autonomy._save_private_message(actor, text, _photo_attachments(place), force_private=True)
        tag = "独自旅行" if mode == "solo" else f"和{user}"
        card = await _keepsake(actor, session_id, title, text, place, ["旅行", tag, place.name])
        await autonomy.append_idle_event(
            actor, "travel", f"{autonomy._actor_label(actor)}去了{place.name}" + ("" if mode == "solo" else f"（和{user}一起）"),
            text[:300], target_type="place", target_id=place.label,
            result_type="message" if message else "", result_id=(message or {}).get("id", ""),
            metadata={"mode": mode, "place": place.label, "session_id": session_id},
        )
        return {"place": place.label, "title": title, "parts": [{"actor": actor, "text": text}],
                "photos": place.photos, "cards": [card] if card else []}

    # 全家出游：轮流写，各写各的，发到群聊
    names = [autonomy._actor_label(a) for a in actors]
    room_id = await autonomy._latest_group_room_id()
    if not room_id:
        raise ValueError("还没有群聊，先在聊天室建一个群聊")
    story: list[tuple[str, str]] = []
    parts, cards = [], []
    for i, actor in enumerate(actors):
        position = "first" if i == 0 else ("last" if i == len(actors) - 1 else "middle")
        text = await _write(actor, _family_prompt(place, user, wish, names, names[i], story, position))
        story.append((names[i], text))
        await autonomy._save_autonomy_chatroom_message(
            room_id, actor, text, attachments=_photo_attachments(place) if i == 0 else [], auto_tts=False,
        )
        parts.append({"actor": actor, "text": text})
        card = await _keepsake(actor, session_id, f"全家去{place.name}", text, place, ["旅行", "全家", place.name])
        if card:
            cards.append(card)
    for actor in actors:
        await autonomy.append_idle_event(
            actor, "travel", f"全家去了{place.name}", "", target_type="place", target_id=place.label,
            metadata={"mode": mode, "place": place.label, "session_id": session_id, "with": actors},
        )
    return {"place": place.label, "title": f"全家去{place.name}", "parts": parts, "photos": place.photos, "cards": cards}
