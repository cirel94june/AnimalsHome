"""MCP 工具页：小猫自己接 MCP，所有 AI 都能去逛。"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

import mcp_outings
import mcp_policy
from config import BASE_DIR

router = APIRouter()
log = logging.getLogger("mcp_tools")
_running: set[tuple[str, str]] = set()
_out_now: set[str] = set()  # 正在外面的 AI：页面、聊天、自主活动共用
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
    except Exception as error:  # 连不上：把原因告诉页面（test_tool 已遮过钥匙，这里再过一遍）
        return {"ok": False, "error": mcp_outings.redact_everything(str(error))[:300] or type(error).__name__}


async def go_out(actor: str, name: str, purpose: str = "", source: str = "auto") -> dict:
    """出门逛一趟：见闻发到 TA 的私聊，并记一条自主活动日志。
    页面、聊天、自主活动都走这里：同一位 AI 同时只能出门一趟，锁放在这一层。"""
    if actor in _out_now:
        return {"ok": False, "error": "TA 已经在外面了"}
    _out_now.add(actor)
    trip = mcp_outings.trip_started(actor, name, source)
    try:
        result = await _go_out(actor, name, purpose)
        mcp_outings.trip_finished(trip, "ok" if result.get("ok") else "failed", result.get("counts"))
        return result
    except BaseException:
        mcp_outings.trip_finished(trip, "cancelled")
        raise
    finally:
        _out_now.discard(actor)


async def _go_out(actor: str, name: str, purpose: str) -> dict:
    import autonomy
    label = autonomy._actor_label(actor)
    try:
        result = await mcp_outings.run_outing(actor, name, purpose=purpose)
    except Exception as error:
        reason = mcp_outings.redact_everything(str(error))[:300] or type(error).__name__  # 先遮后截
        log.warning("[MCP] %s 去 %s 没成功：%s", actor, name, reason)
        event = await autonomy.append_idle_event(
            actor, "mcp_outing", f"{label}想去「{name}」，但没去成", reason,
            target_type="mcp", target_id=name,
        )
        return {"ok": False, "error": reason, "event": event}
    message = await autonomy._save_private_message(actor, result["summary"], force_private=True)
    event = await autonomy.append_idle_event(
        actor, "mcp_outing", f"{label}去「{name}」逛了一趟", result["summary"][:300],
        target_type="mcp", target_id=name,
        result_type="message" if message else "", result_id=(message or {}).get("id", ""),
        metadata={"actions": result["actions"][:12], "model": result["model"], "counts": result.get("counts")},
    )
    return {"ok": True, "message": message, "event": event, "counts": result.get("counts")}


def start_outing(actor: str, name: str, purpose: str, source: str) -> bool:
    """放到后台去，不占着聊天。TA 已经在外面就返回 False。"""
    if actor in _out_now or any(key[0] == actor for key in _running):
        return False
    key = (actor, name)
    _running.add(key)

    async def run():
        try:
            result = await go_out(actor, name, purpose, source=source)
            if source == "chat" and not result.get("ok"):
                await _tell_she_did_not_make_it(actor, name, result.get("error") or "")
        finally:
            _running.discard(key)

    task = asyncio.create_task(run())
    _tasks.add(task)  # 保留引用，避免后台任务被回收
    task.add_done_callback(_tasks.discard)
    return True


async def _tell_she_did_not_make_it(actor: str, name: str, reason: str) -> None:
    """聊天里说了要去，没去成要说一声：原来只写自主活动记录，她看不到。"""
    import autonomy
    try:
        await autonomy._save_private_message(actor, f"（我想去「{name}」，但没去成：{reason[:120]}）", force_private=True)
    except Exception as error:
        log.warning("[MCP] %s 没去成的提示没发出去：%s", actor, type(error).__name__)


async def process_outing_commands(text: str, actor: str, *, allowed: bool = True, notify=None) -> str:
    """从正式回复里取出 [出门:地方:想做什么]，从正文去掉，开始出门。一条回复最多一个。
    allowed=False（系统叫醒、环境音、AI 之间接话）只清除不执行。"""
    if not text or "出门" not in text:
        return text
    match = mcp_policy.OUTING_CMD_PATTERN.search(text)
    if not match:
        return text
    clean = mcp_policy.OUTING_CMD_PATTERN.sub("", text).strip()
    from capabilities import is_capability_enabled
    if not allowed or not is_capability_enabled("mcp_chat"):
        return clean
    message = _begin_outing(actor, match.group(1), match.group(2) or "")
    if notify and message:
        try:
            await notify(message)
        except Exception as error:
            log.warning("[MCP] 出门提示没发出去：%s", type(error).__name__)
    return clean


def _begin_outing(actor: str, wanted: str, purpose: str) -> str:
    import autonomy
    label = autonomy._actor_label(actor)
    def same(name) -> str:
        return " ".join(str(name or "").split()).lower()
    place = next((p["name"] for p in mcp_outings.places_for(actor, autonomy_only=False) if same(p["name"]) == same(wanted)), "")
    if not place:
        return f"{label}想出门，但不认识「{wanted[:40]}」这个地方"
    if not mcp_outings.tool_model_for(actor):
        return f"{label}想去「{place}」，但还没有能出门的模型"
    if not start_outing(actor, place, purpose[:200], "chat"):
        return f"{label}已经在外面了，这次没去「{place}」"
    return f"🚪 {label}去「{place}」了，回来会在私聊里讲"


@router.post("/api/mcp-tools/{name}/outing")
async def api_outing(name: str, request: Request):
    data = await _body(request)
    actor = str(data.get("actor") or "")
    if actor not in {a["id"] for a in _actors_info()}:
        raise HTTPException(400, "没有这位 AI")
    if not any(p["name"] == name for p in mcp_outings.places_for(actor, autonomy_only=False)):
        raise HTTPException(400, "这位 AI 不能去这个地方（工具未启用或没勾选 TA）")
    if not mcp_outings.tool_model_for(actor):
        raise HTTPException(400, "这位 AI 还没有能出门的模型（相遇卡里选一条线路）")
    if not start_outing(actor, name, str(data.get("purpose") or "")[:200], "page"):
        raise HTTPException(409, "TA 已经在外面了")
    return {"started": True}
