"""
三位 AI 在 Memory Hub 的整理情况（11-09 小猫）：只读，给记忆库页面看。

家里本地的「整理记忆」只服务最早的两个位置；Hub 这一层每位 AI 都一样，所以从 Hub 读。
Hub 连不上时把这位标成读取失败，不影响别的。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse

import memory_hub_bridge as bridge
from config import BASE_DIR

router = APIRouter()
log = logging.getLogger("uvicorn.error")

PER_PAGE = 50
MAX_CHARS = 600
TIMEOUT_SECONDS = 15.0


def pick_digests(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("items") if isinstance(payload.get("items"), list) else payload.get("memories")
    out = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("source_platform") == "daemon:digest":
            out.append({"at": str(row.get("created_at") or ""), "content": str(row.get("content") or "")[:MAX_CHARS]})
    out.sort(key=lambda r: r["at"], reverse=True)
    return out


async def _one(client, base: str, headers: dict[str, str], actor: dict[str, Any], hub_id: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": actor["id"], "name": actor["name"], "ok": False, "digests": []}
    try:
        response = await client.get(f"{base}/api/memory/list", headers=headers,
                                    params={"room": "dreams", "source_ai": hub_id, "per_page": PER_PAGE})
        if response.status_code == 200:
            entry.update(ok=True, digests=pick_digests(response.json()))
    except Exception as error:
        log.warning("[hub_organize] %s 读取失败：%s", actor["id"], type(error).__name__)
    return entry


@router.get("/memory-organize")
async def page():
    return FileResponse(BASE_DIR / "static" / "memory-organize.html", headers={"Cache-Control": "no-cache"})


@router.get("/api/hub-organize")
async def api_organize():
    import actors

    cfg = bridge.load_config()
    if not bridge._is_active(cfg):
        return {"ok": False, "reason": "记忆库现在没有连上", "people": []}
    people = [(a, bridge.source_ai_for(a["id"], cfg)) for a in actors.list_actors(include_disabled=False)]
    base, headers = bridge._rest_base(cfg), bridge._headers(cfg)
    async with bridge._http_client(TIMEOUT_SECONDS) as client:
        results = await asyncio.gather(*(_one(client, base, headers, a, hub) for a, hub in people if hub))
    return {"ok": True, "people": list(results)}
