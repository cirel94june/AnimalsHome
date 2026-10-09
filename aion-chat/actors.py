"""
角色登记表：六个 AI 座位。

- 座位 1 = 原「主 AI」（aion），座位 2 = 原「第二 AI」（connor）：名字仍以世界书 ai_name /
  聊天室 connor_name 为准（单一来源），这里只补充头像、签名、相遇日、Memory Hub 身份等。
- 座位 3～6 为新角色（ai3～ai6），默认座位 3 启用，其余留空位以后启用。
- 数据存 data/actors.json，手机 App 与浏览器共用。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from config import BASE_DIR, DATA_DIR, load_worldbook

router = APIRouter()

ACTORS_PATH = DATA_DIR / "actors.json"
AVATAR_DIR = DATA_DIR / "skin"
SEAT_IDS = ["aion", "connor", "ai3", "ai4", "ai5", "ai6"]
EDITABLE = ("name", "avatar", "sign", "color", "memory_hub", "meet_since", "enabled")
COLORS = ["#5c86c8", "#c98a5c", "#5ca88f", "#9a7bd0", "#c96f8f", "#6f9fc9"]
AVATAR_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}

_DEFAULTS: dict[str, dict[str, Any]] = {
    "aion": {"avatar": "/public/AIIcon.png", "memory_hub": "claude", "enabled": True},
    "connor": {"avatar": "/public/codexicon.png", "memory_hub": "lucien", "enabled": True},
    "ai3": {"name": "Jasper", "memory_hub": "jasper", "enabled": True},
    "ai4": {"enabled": False},
    "ai5": {"enabled": False},
    "ai6": {"enabled": False},
}


def _connor_name() -> str:
    try:
        from chatroom import load_chatroom_config
        return (load_chatroom_config().get("connor_name") or "").strip()
    except Exception:
        return ""


def _load_raw() -> dict[str, Any]:
    if ACTORS_PATH.exists():
        try:
            data = json.loads(ACTORS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _save_raw(data: dict[str, Any]) -> None:
    ACTORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ACTORS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, ACTORS_PATH)


def list_actors(include_disabled: bool = True) -> list[dict[str, Any]]:
    raw = _load_raw().get("seats", {})
    wb = load_worldbook()
    result = []
    for index, actor_id in enumerate(SEAT_IDS):
        item: dict[str, Any] = {
            "id": actor_id,
            "seat": index + 1,
            "name": "",
            "avatar": "",
            "sign": "",
            "color": COLORS[index],
            "memory_hub": "",
            "meet_since": "",
            "enabled": False,
            "builtin": actor_id in ("aion", "connor"),
        }
        item.update(copy.deepcopy(_DEFAULTS[actor_id]))
        stored = raw.get(actor_id) if isinstance(raw, dict) else None
        if isinstance(stored, dict):
            item.update({k: stored[k] for k in EDITABLE if k in stored})
        # 前两个座位的名字以原有设置为准
        if actor_id == "aion":
            item["name"] = (wb.get("ai_name") or "").strip() or item["name"] or "AI"
        elif actor_id == "connor":
            item["name"] = _connor_name() or item["name"] or "第二AI"
        elif not item["name"]:
            item["name"] = f"空位 {index + 1}"
        if include_disabled or item["enabled"]:
            result.append(item)
    return result


def get_actor(actor_id: str) -> dict[str, Any] | None:
    return next((a for a in list_actors() if a["id"] == actor_id), None)


def memory_hub_id(actor_id: str) -> str:
    actor = get_actor(actor_id)
    return (actor or {}).get("memory_hub", "").strip() if actor and actor.get("enabled") else ""


def display_name(actor_id: str) -> str:
    actor = get_actor(actor_id)
    return actor["name"] if actor else actor_id


@router.get("/api/actors")
async def api_list_actors():
    wb = load_worldbook()
    return {"user": {"name": (wb.get("user_name") or "").strip() or "你", "avatar": "/public/UserIcon.png"},
            "actors": list_actors()}


@router.put("/api/actors/{actor_id}")
async def api_update_actor(actor_id: str, request: Request):
    if actor_id not in SEAT_IDS:
        raise HTTPException(404, "没有这个座位")
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "格式不正确")
    if not isinstance(body, dict):
        raise HTTPException(400, "格式不正确")
    updates: dict[str, Any] = {}
    for key in EDITABLE:
        if key not in body:
            continue
        value = body[key]
        if key == "enabled":
            if actor_id == "aion":
                continue  # 主 AI 始终启用
            updates[key] = bool(value)
        elif key == "meet_since":
            value = str(value or "").strip()
            if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise HTTPException(400, "日期格式应为 YYYY-MM-DD")
            updates[key] = value
        elif key == "avatar":
            value = str(value or "").strip()
            if value and not re.fullmatch(r"/(public|skin-assets|static)/[\w\-./]+", value):
                raise HTTPException(400, "头像地址不正确")
            updates[key] = value
        else:
            updates[key] = str(value or "").strip()[:200]
    if actor_id in ("aion", "connor"):
        updates.pop("name", None)  # 前两位改名请在世界书 / 聊天室设置里改，保持单一来源
    data = _load_raw()
    seats = data.setdefault("seats", {})
    seats.setdefault(actor_id, {}).update(updates)
    _save_raw(data)
    return get_actor(actor_id)


@router.post("/api/actors/{actor_id}/avatar")
async def api_upload_avatar(actor_id: str, file: UploadFile = File(...)):
    if actor_id not in SEAT_IDS and actor_id != "user":
        raise HTTPException(404, "没有这个座位")
    ext = AVATAR_TYPES.get((file.content_type or "").lower())
    if not ext:
        raise HTTPException(400, "只支持 jpg / png / webp / gif 图片")
    content = await file.read()
    if len(content) > 4 * 1024 * 1024:
        raise HTTPException(413, "头像不能超过 4MB")
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(content).hexdigest()[:16] + ext
    (AVATAR_DIR / name).write_bytes(content)
    url = f"/skin-assets/{name}"
    if actor_id != "user":
        data = _load_raw()
        data.setdefault("seats", {}).setdefault(actor_id, {})["avatar"] = url
        _save_raw(data)
    return {"url": url}


@router.get("/ai-cards")
async def ai_cards_page():
    return FileResponse(BASE_DIR / "static" / "ib" / "cards.html", headers={"Cache-Control": "no-cache"})
