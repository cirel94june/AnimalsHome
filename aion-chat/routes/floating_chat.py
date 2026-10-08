"""Floating chat uses the ordinary chat send routes and accepted user sends."""
import asyncio
import re
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from billiards.reports import last_active_window
from config import DEFAULT_MODEL
from database import get_db
from phone_screen import freeze_phone_screen
from ws import manager

router = APIRouter(prefix="/api/floating-chat")
SCREEN_TIMEOUT = 12
_pending_screens: dict[str, asyncio.Future] = {}


class ScreenRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=128)


def screen_uploaded(meta: dict):
    """Freeze this request's upload before another capture replaces the cache."""
    match = re.search(r"floating:([a-f0-9]{32})$", str(meta.get("reason") or ""))
    future = _pending_screens.get(match[1]) if match else None
    if future is None or future.done():
        return
    try:
        attachment = freeze_phone_screen(Path(meta["path"]), event_id=f"floating_{match[1]}")
        future.set_result(attachment)
    except (OSError, KeyError) as error:
        future.set_exception(error)


def get_chatroom_names():
    from chatroom import get_chatroom_names as names
    return names()


def get_chatroom_config():
    from chatroom import load_chatroom_config
    return load_chatroom_config()


@router.get("/context")
async def get_context():
    target = await last_active_window()
    if not target:
        raise HTTPException(409, "请先在聊天窗口发送一条消息")
    private = target["type"] == "private"
    async with get_db() as db:
        if private:
            cursor = await db.execute("SELECT title, model FROM conversations WHERE id=?", (target["id"],))
        else:
            cursor = await db.execute("SELECT title, type FROM chatroom_rooms WHERE id=?", (target["id"],))
        row = await cursor.fetchone()
    if not row:
        raise HTTPException(409, "聊天窗口已不存在，请回到 App 选择窗口")
    user_name, ai_name, companion_name = get_chatroom_names()
    options = {"model": row[1]}
    if not private:
        room_config = get_chatroom_config()
        options = {key: value for key, value in room_config.items()
                   if key in {"connor_model", "tts_enabled", "tts_aion_voice", "tts_connor_voice"}}
        options["model"] = room_config.get("aion_model") or DEFAULT_MODEL
    encoded_id = quote(target["id"], safe="")
    return {
        "target": target,
        "title": row[0] or "聊天",
        "send_url": (f"/api/conversations/{encoded_id}/send" if private
                     else f"/api/chatroom/rooms/{encoded_id}/send"),
        "send_options": options,
        "actors": {
            "user": {"name": user_name, "avatar": "/public/UserIcon.png"},
            "assistant": {"name": ai_name, "avatar": "/public/AIIcon.png"},
            "aion": {"name": ai_name, "avatar": "/public/gropicon1.png"},
            "connor": {"name": companion_name, "avatar": "/public/codexicon.png"},
        },
    }


@router.post("/screen")
async def capture_screen(body: ScreenRequest):
    request_id = uuid.uuid4().hex
    future = asyncio.get_running_loop().create_future()
    _pending_screens[request_id] = future
    try:
        await manager.broadcast({"type": "floating_screen_capture", "data": {
            "request_id": request_id, "client_id": body.client_id,
        }})
        attachment = await asyncio.wait_for(future, timeout=SCREEN_TIMEOUT)
        return {"attachment": attachment}
    except (TimeoutError, OSError):
        raise HTTPException(409, "当前画面读取失败，请检查屏幕共享或无障碍授权")
    finally:
        _pending_screens.pop(request_id, None)
