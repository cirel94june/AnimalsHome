"""Read-only builder for the phone's Homecoming disaster snapshot."""

from __future__ import annotations

import base64
import json
from typing import Any

from .contracts import (
    MAX_MESSAGES_PER_TIMELINE,
    SEAT_OWNERS,
    TEXT_WINDOW_SECONDS,
    seat_timeline,
)


_PORTABLE_SETTING_KEYS = {
    "temperature",
    "main_memory_model",
    "memory_model",
    "memory_recall_backend",
    "memory_extraction_enabled",
    "ai_prompt_capabilities",
    "sentinel_base_url",
    "sentinel_model",
    "embedding_base_url",
    "embedding_model",
}

_SECRET_SETTING_KEYS = {
    "gemini_key",
    "gemini_free_key",
    "siliconflow_key",
    "aipro_key",
    "openrouter_key",
    "sentinel_api_key",
    "embedding_api_key",
}

_PORTABLE_MEMORY_COLUMNS = {
    "memories": (
        "id", "content", "type", "created_at", "source_conv", "keywords",
        "importance", "source_start_ts", "source_end_ts", "unresolved",
        "source_msg_id", "evidence_summary", "evidence_detail_level",
    ),
    "chatroom_memories": (
        "id", "room_id", "scope", "content", "keywords", "importance",
        "source_start_ts", "source_end_ts", "created_at", "unresolved",
        "source_msg_id", "evidence_summary", "evidence_detail_level",
        "memory_kind",
    ),
}


async def _rows(db, query: str, parameters: tuple = ()) -> list[dict]:
    cursor = await db.execute(query, parameters)
    result = await cursor.fetchall()
    columns = [item[0] for item in cursor.description or ()]
    return [dict(zip(columns, row)) for row in result]


async def _table_columns(db, table: str) -> set[str]:
    rows = await _rows(db, f"PRAGMA table_info({table})")
    return {str(row["name"]) for row in rows}


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _attachment_kind(item: dict) -> str:
    raw = str(
        item.get("kind")
        or item.get("type")
        or item.get("content_type")
        or item.get("mime")
        or ""
    ).lower()
    if "image" in raw:
        return "image"
    if "audio" in raw or "voice" in raw:
        return "audio"
    if "video" in raw:
        return "video"
    return "file"


def normalize_attachments(value: Any) -> list[dict]:
    normalized: list[dict] = []
    for raw in _json_list(value):
        item = raw if isinstance(raw, dict) else {}
        portable = {"kind": _attachment_kind(item)}
        transcript = item.get("transcript")
        if not isinstance(transcript, str) or not transcript.strip():
            transcript = item.get("text")
        if isinstance(transcript, str) and transcript.strip():
            portable["transcript"] = transcript.strip()
        normalized.append(portable)
    return normalized


def _portable_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {
            "encoding": "base64",
            "data": base64.b64encode(value).decode("ascii"),
        }
    return value


def _portable_row(row: dict) -> dict:
    return {key: _portable_value(value) for key, value in row.items()}


async def _timeline_messages(
    db,
    *,
    source: str,
    cutoff: float,
    actor: str = "",
) -> list[dict]:
    if actor:
        # 座位私聊：这位座位所有 seat_1v1 房间里的消息
        query = """
            SELECT m.id, m.room_id AS container_id, m.sender,
                   m.content, m.attachments, m.created_at
            FROM chatroom_messages m
            JOIN chatroom_rooms r ON r.id=m.room_id
            WHERE r.type='seat_1v1' AND r.actor=? AND m.created_at >= ?
            ORDER BY m.created_at DESC
            LIMIT ?
        """
        parameters = (actor, cutoff, MAX_MESSAGES_PER_TIMELINE)
    elif source == "main_private":
        query = """
            SELECT m.id, m.conv_id AS container_id, m.role AS sender,
                   m.content, m.attachments, m.created_at
            FROM messages m
            WHERE m.created_at >= ?
            ORDER BY m.created_at DESC
            LIMIT ?
        """
        parameters = (cutoff, MAX_MESSAGES_PER_TIMELINE)
    else:
        room_type = "connor_1v1" if source == "companion_private" else "group"
        query = """
            SELECT m.id, m.room_id AS container_id, m.sender,
                   m.content, m.attachments, m.created_at
            FROM chatroom_messages m
            JOIN chatroom_rooms r ON r.id=m.room_id
            WHERE r.type=? AND m.created_at >= ?
            ORDER BY m.created_at DESC
            LIMIT ?
        """
        parameters = (room_type, cutoff, MAX_MESSAGES_PER_TIMELINE)

    rows = await _rows(db, query, parameters)
    rows.reverse()
    return [
        {
            "id": row["id"],
            "container_id": row["container_id"],
            "sender": row["sender"],
            "content": row["content"] or "",
            "attachments": normalize_attachments(row.get("attachments")),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


async def _memory_rows(db, table: str) -> list[dict]:
    columns = await _table_columns(db, table)
    selected = [
        name for name in _PORTABLE_MEMORY_COLUMNS[table]
        if name in columns
    ]
    where = (
        " WHERE COALESCE(archive_state, 'active')='active'"
        if "archive_state" in columns
        else ""
    )
    rows = await _rows(
        db,
        f"SELECT {','.join(selected)} FROM {table}{where} ORDER BY created_at ASC",
    )
    return [_portable_row(row) for row in rows]


async def _metadata(db) -> tuple[list[dict], list[dict]]:
    conversations = await _rows(
        db,
        "SELECT id,title,model,created_at,updated_at "
        "FROM conversations ORDER BY updated_at ASC",
    )
    rooms = await _rows(
        db,
        "SELECT id,title,type,aion_persona,connor_persona,"
        "context_minutes,ai_chat_rounds,created_at,updated_at "
        "FROM chatroom_rooms WHERE type IN ('connor_1v1','group') "
        "ORDER BY updated_at ASC",
    )
    return conversations, rooms


async def _seat_rooms(db, actor: str) -> list[dict]:
    return await _rows(
        db,
        "SELECT id,title,type,context_minutes,ai_chat_rounds,created_at,updated_at "
        "FROM chatroom_rooms WHERE type='seat_1v1' AND actor=? "
        "ORDER BY updated_at ASC",
        (actor,),
    )


def _load_seats() -> tuple[list[dict], list[str]]:
    """准备好的座位（启用、有人设、有模型）和群里要发言的座位，跟聊天室平时的判断一致。"""
    import actors
    import seat_chat
    from persona_evolution import _compile_ai_persona_sections

    seats = []
    for actor in seat_chat.SEAT_ACTORS:
        try:
            seat_chat.check_ready(actor)
        except seat_chat.SeatNotReady:
            continue
        seats.append({
            "id": actor,
            "name": actors.display_name(actor),
            "persona": _compile_ai_persona_sections(actors.persona_sections(actor)),
            "model_key": seat_chat.seat_model(actor),
        })
    return seats, [seat["id"] for seat in seats]


_AVATAR_MAX_BYTES = 5_000_000      # 原图最大多大还肯读
_AVATAR_EDGE = 192                 # 缩成最长边 192 像素的 webp，一张通常十几 KB
_AVATAR_OUT_MAX = 80_000           # 缩完单张最多这么大（data 地址字符数）
_AVATAR_TOTAL_CHARS = 400_000      # 所有头像加起来的预算（Codex §105 M2：每张合规、合起来会撑爆手机上限）
_AVATAR_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".webp": "image/webp", ".gif": "image/gif"}
# 手机收快照的上限是 2MB（HomecomingBackupClient.MAX_COMPRESSED_BYTES），留出线路密文和外壳
PHONE_SNAPSHOT_BUDGET = 1_900_000


def _avatar_file(url: str):
    """头像地址 → 服务器上的文件。只认自己家的两处：换过的头像（/skin-assets/）和原版/替换图（/public/）。

    解析掉符号链接后还得在原来那个目录里，链出去的不认。
    """
    import re
    from config import DATA_DIR, PUBLIC_DIR
    from public_overrides import OVERRIDE_DIR, override_for

    url = str(url or "").split("?", 1)[0]
    path = root = None
    match = re.fullmatch(r"/skin-assets/([\w\-]+\.\w+)", url)
    if match:
        root = DATA_DIR / "skin"
        path = root / match.group(1)
    match = re.fullmatch(r"/public/([^/\\]+\.\w+)", url)
    if match and ".." not in match.group(1):
        override = override_for(match.group(1))
        root = OVERRIDE_DIR if override else PUBLIC_DIR
        path = override or PUBLIC_DIR / match.group(1)
    if path is None:
        return None
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        return None
    return resolved


def _shrink_avatar(raw: bytes) -> str:
    """缩成小 webp；缩不了返回空串。"""
    import io
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as image:
        image.thumbnail((_AVATAR_EDGE, _AVATAR_EDGE))
        image = image.convert("RGBA")
        out = io.BytesIO()
        image.save(out, "WEBP", quality=82)
    return "data:image/webp;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def avatar_data_uri(url: str) -> str:
    """把头像缩小后变成 data: 地址带进手机，断网也能显示；找不到、太大、不是图片就不带（手机上退回名字首字）。"""
    try:
        path = _avatar_file(url)
        if not path or path.suffix.lower() not in _AVATAR_TYPES or not path.is_file():
            return ""
        if path.stat().st_size > _AVATAR_MAX_BYTES:
            return ""
        value = _shrink_avatar(path.read_bytes())
        return value if len(value) <= _AVATAR_OUT_MAX else ""
    except Exception:  # noqa: BLE001 — 头像带不上不影响归巢
        return ""


def _load_avatars(seat_ids) -> dict[str, str]:
    """owner → 头像 data 地址；user 是小猫自己。和家里聊天页用的是同一份头像设置。

    按 小克、Lucien、座位、小猫自己 的顺序装，总量超预算的就不带。
    """
    try:
        import actors
        wanted = [(owner, (actors.get_actor(actor) or {}).get("avatar") or "")
                  for owner, actor in (("main", "aion"), ("second", "connor"), *((seat, seat) for seat in seat_ids))]
        wanted.append(("user", "/public/UserIcon.png"))
        avatars, used = {}, 0
        for owner, url in wanted:
            value = avatar_data_uri(url)
            if value and used + len(value) <= _AVATAR_TOTAL_CHARS:
                avatars[owner] = value
                used += len(value)
        return avatars
    except Exception:  # noqa: BLE001
        return {}


def without_avatars(sections: dict) -> dict:
    """同一份快照，只是头像都拿掉。"""
    identity = dict(sections.get("identity") or {})
    identity["user"] = {**(identity.get("user") or {}), "avatar": ""}
    identity["companions"] = {
        owner: {**value, "avatar": ""} for owner, value in (identity.get("companions") or {}).items()
    }
    return {**sections, "identity": identity}


def fit_phone_budget(sections: dict) -> dict:
    """快照压缩后要装得进手机；装不下时先把头像（可有可无）拿掉，保证记忆和聊天能带上。"""
    import gzip
    from .contracts import canonical_json_bytes

    if len(gzip.compress(canonical_json_bytes(sections), compresslevel=6)) <= PHONE_SNAPSHOT_BUDGET:
        return sections
    print("[homecoming] 快照太大，这次不带头像")
    return without_avatars(sections)


def _public_route_descriptors(settings: dict) -> list[dict]:
    descriptors: list[dict] = []
    for raw in settings.get("custom_model_routes") or []:
        if not isinstance(raw, dict):
            continue
        base_url = str(raw.get("base_url") or "").strip()
        if not base_url.lower().startswith("https://"):
            continue
        models = []
        for model in raw.get("models") or []:
            if isinstance(model, str):
                models.append({"key": model, "model": model})
            elif isinstance(model, dict):
                model_id = str(model.get("model") or model.get("model_id") or "").strip()
                model_key = str(model.get("key") or model.get("name") or model_id).strip()
                if model_id and model_key:
                    models.append({
                        "key": model_key,
                        "model": model_id,
                        "vision": bool(model.get("vision", True)),
                        "audio": model.get("audio") is True,
                    })
        if models:
            descriptors.append({
                "route_id": str(raw.get("id") or raw.get("name") or "custom"),
                "label": str(raw.get("name") or "Custom cloud route"),
                "provider": "custom_openai",
                "base_url": base_url,
                "models": models,
            })
    return descriptors


def _load_defaults() -> dict:
    from app_supervision_ai import state_cache
    from chatroom import load_chatroom_config
    from config import SETTINGS, load_cam_config, load_worldbook
    from location import (
        load_location_config,
        load_location_status,
    )

    try:
        from config import DIGEST_ANCHOR_PATH
        digest_anchor = json.loads(DIGEST_ANCHOR_PATH.read_text(encoding="utf-8"))
    except Exception:
        digest_anchor = {}
    supervision, _received_at = state_cache.read()
    return {
        "worldbook": load_worldbook(),
        "chatroom_config": load_chatroom_config(),
        "settings": dict(SETTINGS),
        "location_config": load_location_config(),
        "location_status": load_location_status(),
        "camera_config": load_cam_config(),
        "digest_anchor": digest_anchor,
        "supervision_snapshot": supervision,
    }


async def build_snapshot_sections(
    db,
    *,
    now: float,
    supervision_snapshot: dict | None = None,
    worldbook: dict | None = None,
    chatroom_config: dict | None = None,
    settings: dict | None = None,
    location_config: dict | None = None,
    location_status: dict | None = None,
    camera_config: dict | None = None,
    digest_anchor: dict | None = None,
    seats: list[dict] | None = None,
    group_seats: list[str] | None = None,
    hub_memories: dict[str, list[dict]] | None = None,
    avatars: dict[str, str] | None = None,
) -> dict:
    defaults: dict = {}
    if seats is None or group_seats is None:
        loaded_seats, loaded_group = _load_seats()
        seats = loaded_seats if seats is None else seats
        group_seats = loaded_group if group_seats is None else group_seats
    if "actor" not in await _table_columns(db, "chatroom_rooms"):
        seats = []  # 很旧的库还没有座位房间
    seats = [seat for seat in seats if seat.get("id") in SEAT_OWNERS and str(seat.get("name") or "").strip()]
    seat_ids = {seat["id"] for seat in seats}
    group_seats = [actor for actor in group_seats if actor in seat_ids]
    if any(value is None for value in (
        supervision_snapshot,
        worldbook,
        chatroom_config,
        settings,
        location_config,
        location_status,
        camera_config,
        digest_anchor,
    )):
        defaults = _load_defaults()

    worldbook = worldbook if worldbook is not None else defaults["worldbook"]
    chatroom_config = (
        chatroom_config
        if chatroom_config is not None
        else defaults["chatroom_config"]
    )
    settings = settings if settings is not None else defaults["settings"]
    location_config = (
        location_config
        if location_config is not None
        else defaults["location_config"]
    )
    location_status = (
        location_status
        if location_status is not None
        else defaults["location_status"]
    )
    camera_config = (
        camera_config
        if camera_config is not None
        else defaults["camera_config"]
    )
    digest_anchor = (
        digest_anchor
        if digest_anchor is not None
        else defaults["digest_anchor"]
    )
    supervision_snapshot = (
        supervision_snapshot
        if supervision_snapshot is not None
        else defaults["supervision_snapshot"]
    )

    hub_memories = hub_memories or {}
    if avatars is None:
        avatars = _load_avatars([seat["id"] for seat in seats])
    cutoff = float(now) - TEXT_WINDOW_SECONDS
    conversations, rooms = await _metadata(db)
    timelines = {
        source: {
            "messages": await _timeline_messages(db, source=source, cutoff=cutoff),
        }
        for source in ("main_private", "companion_private", "group")
    }
    timelines["main_private"]["containers"] = conversations
    timelines["companion_private"]["containers"] = [
        room for room in rooms if room["type"] == "connor_1v1"
    ]
    timelines["group"]["containers"] = [
        room for room in rooms if room["type"] == "group"
    ]
    for seat in seats:
        timelines[seat_timeline(seat["id"])] = {
            "messages": await _timeline_messages(db, source="seat_private", cutoff=cutoff, actor=seat["id"]),
            "containers": await _seat_rooms(db, seat["id"]),
        }
    # 手机按这份名单画会话列表；旧手机不认它，照旧只用 main / second
    roster = [
        {"owner": "main", "actor": "aion", "timeline": "main_private",
         "name": worldbook.get("ai_name") or "AI"},
        {"owner": "second", "actor": "connor", "timeline": "companion_private",
         "name": chatroom_config.get("connor_name") or "第二AI"},
    ] + [
        {"owner": seat["id"], "actor": seat["id"], "timeline": seat_timeline(seat["id"]),
         "name": seat["name"], "model_key": seat.get("model_key") or ""}
        for seat in seats
    ]

    schedules = await _rows(
        db,
        "SELECT * FROM schedules WHERE status='active' ORDER BY trigger_at ASC",
    )
    portable_settings = {
        key: value
        for key, value in settings.items()
        if key in _PORTABLE_SETTING_KEYS and key not in _SECRET_SETTING_KEYS
    }

    return {
        "identity": {
            "user": {
                "name": worldbook.get("user_name") or "用户",
                "persona": worldbook.get("user_persona") or "",
                "avatar": avatars.get("user", ""),
            },
            "companions": {
                "main": {
                    "name": worldbook.get("ai_name") or "AI",
                    "persona": worldbook.get("ai_persona") or "",
                    "avatar": avatars.get("main", ""),
                },
                "second": {
                    "name": chatroom_config.get("connor_name") or "第二AI",
                    "persona": chatroom_config.get("connor_persona") or "",
                    "avatar": avatars.get("second", ""),
                },
                **{
                    seat["id"]: {"name": seat["name"], "persona": seat.get("persona") or "",
                                 "avatar": avatars.get(seat["id"], "")}
                    for seat in seats
                },
            },
            "roster": roster,
            "group_members": ["main", "second", *group_seats],
            "system_prompt": worldbook.get("system_prompt") or "",
            "reply_order": chatroom_config.get("reply_order") or "random",
            "tts": {
                "enabled": bool(chatroom_config.get("tts_enabled")),
                "main_voice": chatroom_config.get("tts_aion_voice") or "",
                "second_voice": chatroom_config.get("tts_connor_voice") or "",
            },
        },
        # 本地记忆在前，后面接 Hub 的只读精简包（hub_pack.py；由路由在开库前拉好传进来）
        "memories": {
            "main": await _memory_rows(db, "memories") + list(hub_memories.get("main", [])),
            "second": await _memory_rows(db, "chatroom_memories") + list(hub_memories.get("second", [])),
            **{
                seat["id"]: list(hub_memories[seat["id"]])
                for seat in seats
                if hub_memories.get(seat["id"])
            },
        },
        "timelines": timelines,
        "schedules": [_portable_row(row) for row in schedules],
        "runtime_state": {
            "settings": portable_settings,
            "supervision": supervision_snapshot,
            "location": {
                "config": location_config,
                "status": location_status,
            },
            "camera": camera_config,
            "digest_anchor": digest_anchor,
        },
        "route_descriptors": _public_route_descriptors(settings),
    }
