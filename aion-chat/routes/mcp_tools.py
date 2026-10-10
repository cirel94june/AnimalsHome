"""MCP 工具页：小猫自己接 MCP，所有 AI 都能去逛。"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

import mcp_outings
from config import BASE_DIR

router = APIRouter()
log = logging.getLogger("mcp_tools")
_running: set[tuple[str, str]] = set()
_tasks: set[asyncio.Task] = set()


def _actors_info() -> list[dict]:
    import actors
    return [{"id": a["id"], "name": a["name"], "tool_model": mcp_outings.tool_model_for(a["id"])}
            for a in actors.list_actors(include_disabled=False)]


@router.get("/mcp-tools")
async def mcp_tools_page():
    return FileResponse(BASE_DIR / "static" / "mcp-tools.html", headers={"Cache-Control": "no-cache"})


@router.get("/api/mcp-tools")
async def api_list():
    return {"tools": mcp_outings.list_tools(), "actors": _actors_info()}


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400, "格式不正确")
    if not isinstance(data, dict):
        raise HTTPException(400, "格式不正确")
    return data


@router.post("/api/mcp-tools")
async def api_add(request: Request):
    try:
        return mcp_outings.save_tool(await _body(request))
    except ValueError as error:
        raise HTTPException(400, str(error))


@router.put("/api/mcp-tools/{name}")
async def api_update(name: str, request: Request):
    try:
        return mcp_outings.save_tool(await _body(request), original_name=name)
    except ValueError as error:
        raise HTTPException(400, str(error))


@router.delete("/api/mcp-tools/{name}")
async def api_delete(name: str):
    from mcp_client import mcp_manager
    if mcp_manager.is_connected(name):  # 娱乐室里连着的话先断开
        await mcp_manager.disconnect(name)
    if not mcp_outings.delete_tool(name):
        raise HTTPException(404, "没有这个工具")
    return {"ok": True}


@router.post("/api/mcp-tools/{name}/test")
async def api_test(name: str):
    try:
        return await mcp_outings.test_tool(name)
    except ValueError as error:
        raise HTTPException(404, str(error))
    except Exception as error:  # 连不上：把原因告诉页面
        return {"ok": False, "error": str(error)[:300] or type(error).__name__}


async def go_out(actor: str, name: str, purpose: str = "") -> dict:
    """出门逛一趟：见闻发到 TA 的私聊，并记一条自主活动日志。"""
    import autonomy
    label = autonomy._actor_label(actor)
    try:
        result = await mcp_outings.run_outing(actor, name, purpose=purpose)
    except Exception as error:
        log.warning("[MCP] %s 去 %s 没成功：%s", actor, name, error)
        event = await autonomy.append_idle_event(
            actor, "mcp_outing", f"{label}想去「{name}」，但没去成", str(error)[:300],
            target_type="mcp", target_id=name,
        )
        return {"ok": False, "error": str(error)[:300], "event": event}
    message = await autonomy._save_private_message(actor, result["summary"], force_private=True)
    event = await autonomy.append_idle_event(
        actor, "mcp_outing", f"{label}去「{name}」逛了一趟", result["summary"][:300],
        target_type="mcp", target_id=name,
        result_type="message" if message else "", result_id=(message or {}).get("id", ""),
        metadata={"actions": result["actions"][:12], "model": result["model"]},
    )
    return {"ok": True, "message": message, "event": event}


@router.post("/api/mcp-tools/{name}/outing")
async def api_outing(name: str, request: Request):
    data = await _body(request)
    actor = str(data.get("actor") or "")
    if actor not in {a["id"] for a in _actors_info()}:
        raise HTTPException(400, "没有这位 AI")
    if not any(p["name"] == name for p in mcp_outings.places_for(actor, autonomy_only=False)):
        raise HTTPException(400, "这位 AI 不能去这个地方（工具未启用或没勾选 TA）")
    if not mcp_outings.tool_model_for(actor):
        raise HTTPException(400, "这位 AI 没有能调用工具的模型（相遇卡里选一条 API 线路）")
    key = (actor, name)
    if key in _running:
        raise HTTPException(409, "TA 已经在那里了")
    _running.add(key)

    async def run():
        try:
            await go_out(actor, name, str(data.get("purpose") or "")[:200])
        finally:
            _running.discard(key)

    task = asyncio.create_task(run())
    _tasks.add(task)  # 保留引用，避免后台任务被回收
    task.add_done_callback(_tasks.discard)
    return {"started": True}
