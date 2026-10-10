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
import time
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from config import BASE_DIR, DATA_DIR, load_worldbook
from request_limits import read_body_limited, read_upload_limited

router = APIRouter()

ACTORS_PATH = DATA_DIR / "actors.json"
AVATAR_DIR = DATA_DIR / "skin"
SEAT_IDS = ["aion", "connor", "ai3", "ai4", "ai5", "ai6"]
EDITABLE = ("name", "avatar", "sign", "color", "memory_hub", "meet_since", "enabled", "model")
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
            # 该 AI 自己的模型：座位私聊、交接卡、做梦都用它；没设就跳过，不换别的模型
            "model": "",
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
        elif key == "model":
            from config import MODELS
            value = str(value or "").strip()
            if value and value not in MODELS:
                raise HTTPException(400, "没有这个模型，请先在设置里添加线路")
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
    content = await read_upload_limited(file, 4 * 1024 * 1024, "头像不能超过 4MB")
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


# ── 人设包：种子 + 起始性格 + 关于用户 ──
# 种子分区（核心身份、关系锚点、边界与禁令）在人设进化中完全锁定，其余分区会慢慢生长。
SEED_LOCKED_SECTIONS = ["identity_core", "relationship_core", "boundaries_and_forbidden"]
USER_SECTION_LABELS = {
    "basic_profile": "基础资料",
    "life_context": "生活与作息",
    "preferences_and_relationship": "偏好与关系期待",
    "additional_notes": "补充",
}


def seed_locked_sections(actor_id: str) -> list[str]:
    seat = (_load_raw().get("seats") or {}).get(actor_id) or {}
    keys = seat.get("seed_locked_sections")
    return list(keys) if isinstance(keys, list) else list(SEED_LOCKED_SECTIONS)


def persona_sections(actor_id: str) -> dict[str, str]:
    """座位 3～6 的人设分区（前两位以世界书 / 聊天室设置为准）。"""
    seat = (_load_raw().get("seats") or {}).get(actor_id) or {}
    sections = seat.get("persona_sections")
    return {k: str(v) for k, v in sections.items()} if isinstance(sections, dict) else {}


MAX_PACK_BYTES = 256 * 1024


def _clean_sections(raw: Any, allowed: set[str], *, who: str, required: str = "") -> dict[str, str]:
    """校验一组人设分区。缺失、为空、类型不对都拒绝，避免一个写错的人设包把已有身份清空。"""
    if not isinstance(raw, dict) or not raw:
        raise HTTPException(400, f"{who}：分区必须是非空的对象")
    unknown = set(raw) - allowed
    if unknown:
        raise HTTPException(400, f"{who}：不认识的分区 {', '.join(sorted(unknown))}")
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            raise HTTPException(400, f"{who}：分区 {key} 必须是文字")
        cleaned[key] = value.strip()[:6000]
    if not any(cleaned.values()):
        raise HTTPException(400, f"{who}：分区内容全是空的")
    if required and not cleaned.get(required):
        raise HTTPException(400, f"{who}：缺少 {required}")
    return cleaned


@router.post("/api/actors/persona-pack")
async def api_import_persona_pack(request: Request):
    """导入人设包：写入世界书（主 AI + 关于用户）、聊天室设置（第二位）和登记表（3～6 号）。

    整包先全部校验，有任何问题就 400、什么都不写；通过后先备份再写入。
    """
    import uuid
    from datetime import datetime
    from config import save_worldbook
    from persona_evolution import _AI_SECTION_KEYS, _compile_ai_persona_sections

    raw = await read_body_limited(request, MAX_PACK_BYTES, "人设包过大")
    try:
        pack = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "格式不正确")
    if not isinstance(pack, dict) or set(pack) - {"about_user", "actors", "note", "version"}:
        raise HTTPException(400, "格式不正确：顶层只能有 about_user / actors")
    actors_pack = pack.get("actors")
    if actors_pack is not None and (not isinstance(actors_pack, dict) or set(actors_pack) - set(SEAT_IDS)):
        raise HTTPException(400, "actors 里只能是 aion / connor / ai3～ai6")
    actors_pack = actors_pack or {}
    sections_by_actor: dict[str, dict[str, str]] = {}
    for aid, entry in actors_pack.items():
        if not isinstance(entry, dict):
            raise HTTPException(400, f"{aid}：必须是带 sections 的对象")
        sections_by_actor[aid] = _clean_sections(entry.get("sections"), set(_AI_SECTION_KEYS), who=aid, required="identity_core")
    about = None
    if "about_user" in pack:
        about = _clean_sections(pack["about_user"], set(USER_SECTION_LABELS), who="about_user")
    if not sections_by_actor and about is None:
        raise HTTPException(400, "人设包是空的")

    from chatroom import load_chatroom_config, save_chatroom_config
    wb = load_worldbook()
    cfg = load_chatroom_config()
    backup = {
        "at": time.time(),
        "worldbook": {k: wb.get(k) for k in ("ai_persona", "ai_persona_sections", "user_persona", "user_persona_sections")},
        "chatroom": {k: cfg.get(k) for k in ("connor_persona", "connor_persona_sections")},
        "seats": _load_raw().get("seats", {}),
    }
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = DATA_DIR / f"persona_pack_backup_{stamp}_{uuid.uuid4().hex[:6]}.json"
    backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")

    if about is not None:
        wb["user_persona_sections"] = about
        wb["user_persona"] = "\n\n".join(f"[{USER_SECTION_LABELS[k]}]\n{v}" for k, v in about.items() if v)
    if "aion" in sections_by_actor:
        wb["ai_persona_sections"] = sections_by_actor["aion"]
        wb["ai_persona"] = _compile_ai_persona_sections(sections_by_actor["aion"], actor="main_ai")
    save_worldbook(wb)
    if "connor" in sections_by_actor:
        cfg["connor_persona_sections"] = sections_by_actor["connor"]
        cfg["connor_persona"] = _compile_ai_persona_sections(sections_by_actor["connor"], actor="connor")
        save_chatroom_config(cfg)
    data = _load_raw()
    for aid, sections in sections_by_actor.items():
        if aid in ("aion", "connor"):
            continue
        data.setdefault("seats", {}).setdefault(aid, {})["persona_sections"] = sections
    _save_raw(data)
    return {"ok": True, "imported": sorted(sections_by_actor), "about_user": about is not None, "backup": backup_path.name}
