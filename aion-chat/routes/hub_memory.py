"""记忆库里的「Hub 记忆」：小猫查看、修改、归档、删除每位 AI 在 Memory Hub 里的记忆。

狗蛋等座位的记忆只在 Hub 里（AionsHome 本地没有他们的记忆库），小克 / Lucien 在 Hub 里也有一份。
Hub 的管理接口要 HUB_SECRET（就是 MEMORY_HUB_TOKEN），只在服务器这边带上，浏览器永远拿不到。
Hub 上线「主人钥匙」后，在 /etc/aionshome.env 设 MEMORY_HUB_OWNER_SECRET：这一页就改用它，
小猫在这里改的记忆会被 Hub 记成「小猫亲手确认」（同样只放服务器）。
这些接口给小猫的页面用，不进任何 AI 的能力里（AI 读不到这里）。
"""

from __future__ import annotations

import os
import re
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

import actors
import memory_hub_bridge

router = APIRouter(prefix="/api/hub-memory", tags=["hub-memory"])

_MEMORY_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_FIELDS = ("id", "content", "category", "room", "layer", "owner_ai", "source_ai", "subject_name",
           "speaker_name", "importance", "created_at", "updated_at", "event_date", "status", "kind")


def _hub() -> tuple[str, dict[str, str]]:
    cfg = memory_hub_bridge.load_config()
    if not memory_hub_bridge._is_active(cfg) or not str(cfg.get("token") or "").strip():
        raise HTTPException(503, "还没接上 Memory Hub")
    base = str(cfg["url"]).rstrip("/")
    if base.endswith("/mcp"):
        base = base[: -len("/mcp")]
    owner = os.environ.get("MEMORY_HUB_OWNER_SECRET", "").strip()
    if owner:  # 小猫的主人钥匙：Hub 按钥匙认「小猫亲手改的」
        cfg = {**cfg, "token": owner, "headers": {k: v for k, v in (cfg.get("headers") or {}).items()
                                                  if str(k).lower() != "authorization"}}
    return base, memory_hub_bridge._headers(cfg)


def _hub_id(actor: str) -> str:
    if actor not in actors.SEAT_IDS:
        raise HTTPException(404, "没有这个位置")
    hub_id = actors.memory_hub_id(actor)
    if not hub_id:
        raise HTTPException(400, f"{actors.display_name(actor)} 还没有接 Memory Hub")
    return hub_id


def _memory_id(memory_id: str) -> str:
    if not _MEMORY_ID.match(memory_id or ""):
        raise HTTPException(400, "记忆编号不对")
    return quote(memory_id, safe="")


async def _call(method: str, path: str, **kwargs) -> dict:
    """调 Hub 管理接口。只认 JSON 对象；看不懂的回应（比如登录页）不当成功；Hub 的错误原文不往页面上传。"""
    base, headers = _hub()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.request(method, base + path, headers=headers, **kwargs)
    except httpx.HTTPError as error:
        raise HTTPException(502, f"连不上 Memory Hub（{type(error).__name__}）")
    if response.status_code == 404:
        raise HTTPException(404, "Hub 里没有这条记忆")
    if response.status_code >= 400:
        raise HTTPException(502, f"Memory Hub 返回错误（{response.status_code}）")
    try:
        data = response.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise HTTPException(502, "Memory Hub 的回应看不懂，这次没有确认成功")
    if data.get("error"):
        raise HTTPException(400, "Memory Hub 没能完成这次操作")
    return data


def _done(memory_id: str, data: dict, success: tuple[str, ...]) -> dict:
    """改动类操作：Hub 明确说成功了（对应的 status）才算成功；只回必要的几项，不把 Hub 的整条记录转给页面。
    Hub 的约定：改 updated、归档 archived / already_archived、找回 unarchived / already_active、删除 deleted；
    找不到 not_found；后台自动操作才会出现的 refused 算 Hub 拒绝。"""
    status = data.get("status")
    if status == "not_found":
        raise HTTPException(404, "Hub 里没有这条记忆")
    if data.get("ok") is False or status not in success:
        if isinstance(status, str) and status:  # Hub 明确拒绝了（比如安全检查没过）
            raise HTTPException(400, "Memory Hub 没能完成这次操作")
        raise HTTPException(502, "Memory Hub 的回应看不懂，这次没有确认成功")
    return {"ok": True, "id": memory_id, "status": status}


@router.get("/list")
async def list_memories(actor: str, status: str = Query("active", pattern="^(active|archived)$"),
                        page: int = Query(1, ge=1, le=10000), per_page: int = Query(20, ge=1, le=50)):
    hub_id = _hub_id(actor)
    data = await _call("GET", "/api/memory/list",
                       params={"ai_id": hub_id, "status": status, "page": page, "per_page": per_page})
    if not isinstance(data.get("items"), list):
        raise HTTPException(502, "Memory Hub 的回应看不懂")
    items = [{k: item.get(k) for k in _FIELDS} for item in data["items"] if isinstance(item, dict)]
    return {"total": int(data.get("total") or 0), "page": page, "per_page": per_page, "items": items,
            "hub_id": hub_id}


class MemoryEdit(BaseModel):
    content: str | None = Field(None, min_length=1, max_length=4000)
    importance: float | None = Field(None, ge=0, le=1)  # 和 Hub 一样是 0～1；页面上显示成 1～10 再换算回来
    category: str | None = Field(None, max_length=40)


@router.put("/{memory_id}")
async def edit_memory(memory_id: str, body: MemoryEdit):
    changes = body.model_dump(exclude_none=True)
    if "content" in changes:
        changes["content"] = changes["content"].strip()
        if not changes["content"]:
            raise HTTPException(400, "内容不能是空的")
    if not changes:
        raise HTTPException(400, "没有要改的内容")
    return _done(memory_id, await _call("PUT", f"/api/memory/{_memory_id(memory_id)}", json={**changes, "changed_by": "ceci"}),
                 ("updated",))


@router.post("/{memory_id}/archive")
async def archive_memory(memory_id: str):
    return _done(memory_id, await _call("POST", f"/api/memory/{_memory_id(memory_id)}/archive"),
                 ("archived", "already_archived"))


@router.post("/{memory_id}/unarchive")
async def unarchive_memory(memory_id: str):
    """找回：已归档（或自动淡出）的记忆恢复成正在用。"""
    return _done(memory_id, await _call("POST", f"/api/memory/{_memory_id(memory_id)}/unarchive"),
                 ("unarchived", "already_active"))


@router.delete("/{memory_id}")
async def delete_memory(memory_id: str, confirm: str = Query("")):
    if confirm != "delete":  # 彻底删除找不回来：页面二次确认后才带上
        raise HTTPException(400, "彻底删除需要确认")
    return _done(memory_id, await _call("DELETE", f"/api/memory/{_memory_id(memory_id)}"), ("deleted",))
