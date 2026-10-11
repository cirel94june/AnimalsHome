"""旅行页：选谁去、去哪，出发；看游记。"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

import travel
from config import BASE_DIR

router = APIRouter()
log = logging.getLogger("travel_routes")
_lock = asyncio.Lock()
_tasks: set[asyncio.Task] = set()
_last: dict = {}


def _actors() -> list[dict]:
    import actors
    return [{"id": a["id"], "name": a["name"], "avatar": a["avatar"]} for a in actors.list_actors(include_disabled=False)]


@router.get("/travel")
async def travel_page():
    return FileResponse(BASE_DIR / "static" / "travel.html", headers={"Cache-Control": "no-cache"})


@router.get("/api/travel/options")
async def api_options():
    from location import load_location_status
    status = load_location_status()
    return {"actors": _actors(), "has_location": bool(status.get("lat") and status.get("lng")),
            "running": _lock.locked(), "last": _last}


@router.get("/api/travel/journals")
async def api_journals(limit: int = 30):
    from autonomy_niches import list_niche_cards
    cards = []
    names = {a["id"]: a["name"] for a in _actors()}
    for actor in names:
        for card in await list_niche_cards(actor, limit=60):
            if "旅行" in (card.get("tags") or []):
                cards.append({**card, "actor_name": names[actor]})
    cards.sort(key=lambda c: c.get("created_at", 0), reverse=True)
    return {"journals": cards[:max(1, min(100, limit))]}


@router.post("/api/travel")
async def api_go(request: Request):
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "格式不正确")
    if not isinstance(body, dict):
        raise HTTPException(400, "格式不正确")
    mode = str(body.get("mode") or "solo")
    actor_ids = [a for a in (body.get("actors") or []) if isinstance(a, str)]
    where = str(body.get("where") or "pick")
    enabled = {a["id"] for a in _actors()}
    if not actor_ids or any(a not in enabled for a in actor_ids):
        raise HTTPException(400, "请选要去的 AI")
    if mode not in travel.MODES or where not in travel.WHERE:
        raise HTTPException(400, "玩法或目的地选法不对")
    if (mode != "family" and len(actor_ids) != 1) or (mode == "family" and len(actor_ids) < 2):
        raise HTTPException(400, "独自和两人只能选一位 AI，全家至少两位")
    if where == "named" and not str(body.get("named") or "").strip():
        raise HTTPException(400, "请写想去的地方")
    if _lock.locked():
        raise HTTPException(409, "大家还在路上，等这一趟回来再出发")

    async def run():
        async with _lock:
            _last.clear()
            _last.update({"status": "running", "mode": mode, "actors": actor_ids})
            try:
                result = await travel.trip(mode, actor_ids, where=where, named=str(body.get("named") or ""),
                                           wish=str(body.get("wish") or ""))
                _last.update({"status": "done", "place": result["place"], "title": result["title"]})
            except Exception as error:
                log.warning("[travel] 这趟没成：%s", error)
                _last.update({"status": "failed", "error": str(error)[:300]})

    task = asyncio.create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"started": True}
