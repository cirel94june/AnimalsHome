"""家里的记忆 · 待确认：小猫审批 Memory Hub 先记下或等她点头的记忆（Hub #150，接口说明第 8 节）。

- 审批只认主人钥匙（MEMORY_HUB_OWNER_SECRET），只在服务器这边带上，网页拿不到。共用钥匙 Hub 会回 403。
- 数字和小红点用 /api/proposals/summary，共用钥匙就能读。
- 返回码按接口说明分开处理：409 要她刷新、200 带 error 也是失败、503 再点一次。
- 这些接口给小猫的页面用，不进任何 AI 的能力里。
"""

from __future__ import annotations

import os
import re
from typing import Any, Literal
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import memory_hub_bridge

router = APIRouter(prefix="/api/hub-review", tags=["hub-review"])

_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_ITEM_FIELDS = ("id", "content", "proposed_room", "proposer_ai_id", "owner_ai", "category", "triage_reason",
                "evidence_excerpt", "verified_quote", "event_date", "created_at", "status", "applied_memory_id")
_TARGET_FIELDS = ("id", "content", "room", "status", "updated_at")


def _hub(owner: bool) -> tuple[str, dict[str, str]]:
    cfg = memory_hub_bridge.load_config()
    if not memory_hub_bridge._is_active(cfg):
        raise HTTPException(503, "还没接上 Memory Hub")
    base = str(cfg["url"]).rstrip("/")
    if base.endswith("/mcp"):
        base = base[: -len("/mcp")]
    if owner:
        key = os.environ.get("MEMORY_HUB_OWNER_SECRET", "").strip()
        if not key:
            raise HTTPException(503, "服务器上还没配主人钥匙，审批用不了")
        cfg = {**cfg, "token": key, "headers": {k: v for k, v in (cfg.get("headers") or {}).items()
                                                if str(k).lower() != "authorization"}}
    return base, memory_hub_bridge._headers(cfg)


async def _send(method: str, path: str, *, owner: bool, **kwargs) -> tuple[int, Any]:
    base, headers = _hub(owner)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.request(method, base + path, headers=headers, **kwargs)
    except httpx.HTTPError as error:
        raise HTTPException(502, f"连不上 Memory Hub（{type(error).__name__}）")
    try:
        data = response.json()
    except ValueError:
        data = None
    return response.status_code, data


def _detail(data: Any) -> str:
    detail = data.get("detail") if isinstance(data, dict) else ""
    return str(detail)[:200] if isinstance(detail, str) else ""


def _hub_names() -> dict[str, str]:
    try:
        import actors
        return {str(a.get("memory_hub") or "").lower(): a.get("name") or a["id"]
                for a in actors.list_actors(include_disabled=True) if a.get("memory_hub")}
    except Exception:
        return {}


def _pick(item: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {key: item.get(key) for key in fields if key in item} if isinstance(item, dict) else {}


@router.get("/summary")
async def summary():
    status, data = await _send("GET", "/api/proposals/summary", owner=False)
    if status != 200 or not isinstance(data, dict):
        raise HTTPException(502, "读不到待确认的数量")
    return {key: data.get(key) for key in ("waiting", "review_after", "oldest_at")}


@router.get("/list")
async def list_items(status: Literal["pending", "review_after"] = "pending", page: int = 1):
    code, data = await _send("GET", "/api/proposals", owner=True,
                             params={"status": status, "page": max(1, int(page)), "per_page": 50})
    if code == 403:
        raise HTTPException(502, "Hub 不认这把主人钥匙，要在服务器上检查")
    if code != 200 or not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise HTTPException(502, "读不到待确认的记忆")
    names = _hub_names()
    items = []
    for raw in data["items"]:
        item = _pick(raw, _ITEM_FIELDS)
        hub_id = str(item.get("proposer_ai_id") or item.get("owner_ai") or "")
        item["who"] = names.get(hub_id.lower(), hub_id)  # 页面上显示名字，不显示 Hub 编号
        targets = raw.get("conflict_targets") if isinstance(raw, dict) else None
        item["conflict_targets"] = [_pick(t, _TARGET_FIELDS) for t in targets or [] if isinstance(t, dict)]
        items.append(item)
    return {"total": data.get("total"), "page": data.get("page"), "items": items}


class RetireTarget(BaseModel):
    id: str = Field(max_length=120)
    updated_at: str = Field(max_length=80)


class Review(BaseModel):
    action: Literal["approve", "reject", "seen"]
    content: str = Field(default="", max_length=2000)
    reject_reason: str = Field(default="", max_length=300)
    retire: bool = False
    retire_ids: list[RetireTarget] = Field(default_factory=list, max_length=20)


@router.post("/{proposal_id}")
async def review(proposal_id: str, body: Review):
    if not _ID.match(proposal_id or ""):
        raise HTTPException(400, "编号不对")
    payload: dict[str, Any] = {"action": body.action}
    if body.content.strip():
        payload["content"] = body.content.strip()
    if body.action == "reject":
        payload["reject_reason"] = body.reject_reason.strip()
    if body.retire:
        payload["retire"] = True
    if body.retire_ids:
        payload["retire_ids"] = [{"id": t.id, "updated_at": t.updated_at} for t in body.retire_ids]  # 原样带页面上的版本
    code, data = await _send("POST", f"/api/proposals/{quote(proposal_id, safe='')}/review", owner=True, json=payload)
    if code == 200:
        if not isinstance(data, dict):
            raise HTTPException(502, "Hub 的回应看不懂，这次不算保存")
        error = data.get("error")
        if error:  # 200 里带 error 也是失败
            if error == "legacy_operator_only":
                raise HTTPException(422, "这条要在服务器上处理")
            raise HTTPException(422, f"没保存：{str(error)[:120]}")
        return {"ok": True, "already": bool(data.get("already")), "status": data.get("status")}
    if code == 409:
        raise HTTPException(409, "这条刚被改过或正在处理，刷新一下再看")
    if code == 503:
        raise HTTPException(503, "这次没保存，再点一次")
    if code == 404:
        raise HTTPException(404, "Hub 里没有这条了，刷新一下")
    if code == 400:
        raise HTTPException(400, _detail(data) or "请求不对")
    if code == 403:
        raise HTTPException(502, "Hub 不认这把主人钥匙，要在服务器上检查")
    raise HTTPException(502, f"Memory Hub 返回错误（{code}），这次不算保存")
