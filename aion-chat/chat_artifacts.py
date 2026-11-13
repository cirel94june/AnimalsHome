"""Collect HTML already written in an AI message. Never execute or serve it as a trusted page."""
from __future__ import annotations

import asyncio
import hashlib
import html as html_text
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Literal

from database import get_db
from request_limits import read_body_limited

router = APIRouter()
FENCE = re.compile(r"```[ \t]*(?:html|svg)[ \t]*\r?\n([\s\S]*?)```", re.I)
DOCUMENT = re.compile(r"(?:<!doctype\s+html[^>]*>\s*)?<html\b[\s\S]*?</html\s*>", re.I)


def extract_webpage(content: str) -> tuple[str, str] | None:
    match = FENCE.search(content or "")
    if match:
        body = match.group(1).strip()
    else:
        match = DOCUMENT.search(content or "")
        if not match:
            return None
        body = match.group(0)
    from html_works import MAX_HTML_CHARS
    if len(body) > MAX_HTML_CHARS or not re.search(r"<(?:html|body|div|main|svg|canvas|style)\b", body, re.I):
        return None
    title = re.search(r"<title[^>]*>([\s\S]*?)</title\s*>", body, re.I)
    name = html_text.unescape(re.sub(r"<[^>]+>", "", title.group(1))) if title else "聊天里的小网页"
    return " ".join(name.split())[:40], body


class CollectMessage(BaseModel):
    kind: Literal['conv', 'room']
    message_id: str = Field(min_length=1, max_length=128)


def require_chat_ui(request: Request):
    if request.headers.get('x-ah-request') != '1' or request.headers.get('sec-fetch-site') not in (None, 'same-origin'):
        raise HTTPException(403, '请从聊天里的作品卡打开')
    origin = request.headers.get('origin')
    if origin is not None and origin.split('://', 1)[-1] != request.headers.get('host'):
        raise HTTPException(403, '请从聊天里的作品卡打开')


@router.post('/api/chat-artifacts/collect')
async def collect(request: Request):
    require_chat_ui(request)
    try:
        body = CollectMessage.model_validate_json(await read_body_limited(request, 2048, '请求太长'))
    except ValueError:
        raise HTTPException(400, '请选择聊天里的作品') from None
    return await collect_saved_message(body.kind, body.message_id)


async def collect_saved_message(kind: str, message_id: str, *, dry_run: bool = False):
    """同一取数路径用于主人点击与明确点名的旧作品补收；不改原聊天。"""
    if kind not in ('conv','room') or not message_id or len(message_id)>128:
        raise HTTPException(400,'请选择聊天里的作品')
    async with get_db() as db:
        if kind == 'conv':
            cur = await db.execute('SELECT role, content FROM messages WHERE id=?', (message_id,))
        else:
            cur = await db.execute('SELECT sender, content FROM chatroom_messages WHERE id=?', (message_id,))
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, '这条消息还没保存好，稍后再点一次')
    actor = 'aion' if kind == 'conv' and row[0] == 'assistant' else row[0] if kind == 'room' else ''
    import actors
    if actor not in actors.SEAT_IDS:
        raise HTTPException(400, '只能收藏 AI 发来的小网页')
    parsed = extract_webpage(row[1])
    if not parsed:
        raise HTTPException(400, '这条消息里没有完整的小网页，或网页太长了')
    title, code = parsed
    if dry_run:
        return {'ok':True, 'actor':actor, 'html_chars':len(code), 'dry_run':True}
    source = hashlib.sha256(f'{kind}:{message_id}:{code}'.encode()).hexdigest()
    from html_works import save_chat_work
    work_id = await asyncio.to_thread(save_chat_work, source, actor, actors.display_name(actor), title, code)
    return {'ok': True, 'work_id': work_id, 'url': '/works?work=' + work_id}
