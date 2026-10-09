"""
桌面便笺：你和每个 AI 都可以往桌面上留便笺。

- 你在桌面上点便笺即可写；
- AI 在回复里写 [NOTE:内容]，这条指令会从聊天正文里拿掉，内容出现在桌面便笺上（署名为该 AI）。
- 只保留最近 30 条，存 data/desk_notes.json。
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from config import DATA_DIR

router = APIRouter()

NOTES_PATH = DATA_DIR / "desk_notes.json"
MAX_NOTES = 30
MAX_TEXT = 300
NOTE_CMD_PATTERN = re.compile(r"\[NOTE[:：]\s*(.+?)\s*\]", re.S)


def load_notes() -> list[dict[str, Any]]:
    if NOTES_PATH.exists():
        try:
            data = json.loads(NOTES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _save(notes: list[dict[str, Any]]) -> None:
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = NOTES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(notes[-MAX_NOTES:], ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, NOTES_PATH)


def add_note(author: str, text: str) -> dict[str, Any] | None:
    text = str(text or "").strip()[:MAX_TEXT]
    if not text:
        return None
    note = {"id": uuid.uuid4().hex[:12], "author": author, "text": text, "at": time.time()}
    notes = load_notes()
    notes.append(note)
    _save(notes)
    return note


async def process_note_commands(text: str, author: str) -> str:
    """从 AI 回复里取出 [NOTE:…]，写进桌面便笺，并从正文中去掉指令。"""
    if not text or "[NOTE" not in text.upper():
        return text
    notes = [m.group(1) for m in NOTE_CMD_PATTERN.finditer(text)]
    if not notes:
        return text
    for content in notes[:2]:
        note = add_note(author, content)
        if note:
            try:
                from ws import manager
                await manager.broadcast({"type": "desk_note_created", "data": note})
            except Exception:
                pass
    return NOTE_CMD_PATTERN.sub("", text).strip()


@router.get("/api/desk/notes")
async def api_list_notes():
    return list(reversed(load_notes()))


@router.post("/api/desk/notes")
async def api_add_note(request: Request):
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "格式不正确")
    note = add_note("user", (body or {}).get("text", ""))
    if not note:
        raise HTTPException(400, "便笺不能为空")
    return note


@router.delete("/api/desk/notes/{note_id}")
async def api_delete_note(note_id: str):
    notes = load_notes()
    kept = [n for n in notes if n.get("id") != note_id]
    if len(kept) == len(notes):
        raise HTTPException(404, "没有这条便笺")
    _save(kept)
    return {"ok": True}
