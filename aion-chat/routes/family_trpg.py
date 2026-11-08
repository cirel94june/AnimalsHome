"""全家跑团的页面和接口。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

import family_trpg as trpg
import trpg_modules
import trpg_rules
from config import BASE_DIR

router = APIRouter()


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400, "格式不正确")
    if not isinstance(data, dict):
        raise HTTPException(400, "格式不正确")
    return data


def _guard(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except trpg.GameError as error:
        raise HTTPException(400, str(error))


@router.get("/trpg")
async def trpg_page():
    return FileResponse(BASE_DIR / "static" / "trpg.html", headers={"Cache-Control": "no-cache"})


@router.get("/api/trpg/rules")
async def rule_info():
    return trpg_rules.describe()


@router.get("/api/trpg/modules")
async def modules():
    return {"modules": trpg_modules.listing()}


@router.post("/api/trpg/modules")
async def add_module(request: Request):
    body = await _body(request)
    try:
        return trpg_modules.add(body.get("title"), body.get("rule"), body.get("text"))
    except ValueError as error:
        raise HTTPException(400, str(error))


@router.delete("/api/trpg/modules/{module_id}")
async def delete_module(module_id: str):
    if not trpg_modules.remove(module_id):
        raise HTTPException(404, "没有这个模组")
    return {"ok": True}


@router.get("/api/trpg/games")
async def games():
    return {"games": trpg.list_games()}


@router.post("/api/trpg/games")
async def create_game(request: Request):
    body = await _body(request)
    actors = body.get("actors") if isinstance(body.get("actors"), list) else []
    try:
        rounds = int(body.get("rounds") or trpg.DEFAULT_ROUNDS)
    except (TypeError, ValueError):
        rounds = trpg.DEFAULT_ROUNDS
    game = _guard(trpg.create, idea=body.get("idea"), dm_model=str(body.get("dm_model") or ""), actors=[str(a) for a in actors],
                  my_name=body.get("my_name"), my_character=str(body.get("my_character") or ""), rounds=rounds,
                  ruleset=str(body.get("ruleset") or "simple"), module_id=str(body.get("module_id") or ""), sheet=body.get("sheet"))
    trpg.start(game["id"])
    return {"id": game["id"]}


@router.get("/api/trpg/games/{game_id}")
async def get_game(game_id: str):
    return trpg.public(_guard(trpg.load, game_id))


@router.delete("/api/trpg/games/{game_id}")
async def delete_game(game_id: str):
    _guard(trpg.delete, game_id)
    return {"ok": True}


@router.post("/api/trpg/games/{game_id}/act")
async def act(game_id: str, request: Request):
    body = await _body(request)
    _guard(trpg.act, game_id, body.get("text"), body.get("skill"))
    return {"ok": True}


@router.post("/api/trpg/games/{game_id}/chat")
async def chat(game_id: str, request: Request):
    body = await _body(request)
    _guard(trpg.chat, game_id, body.get("text"))
    return {"ok": True}


@router.post("/api/trpg/games/{game_id}/retry")
async def retry(game_id: str):
    _guard(trpg.load, game_id)
    trpg.start(game_id)
    return {"ok": True}
