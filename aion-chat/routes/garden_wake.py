"""花园唤醒桥的接口（garden_wake.py）。

- /api/garden-wake/{AI}/wake：只给本机的 injector 用。要本机密钥（GARDEN_WAKE_LOCAL_KEY，只在服务器配置里），
  经 Cloudflare 隧道进来的请求一律不收（隧道也是从本机连进来的，所以还要看 Cf-* 请求头）。
- 其余给小猫的 MCP 工具页用：看状态、连接 / 断开、「有人找他就去看」开关。
"""

from __future__ import annotations

import asyncio
import hmac
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import garden_wake

router = APIRouter(prefix="/api/garden-wake", tags=["garden-wake"])
_LOCAL = {"127.0.0.1", "::1"}


class Wake(BaseModel):
    reason: str = Field(max_length=garden_wake.MAX_REASON)
    message: str = Field(max_length=garden_wake.MAX_MESSAGE)


class Notify(BaseModel):
    on: bool


def _actor(actor: str) -> str:
    if actor not in garden_wake.ACTORS:
        raise HTTPException(404, "不认识这位 AI")
    return actor


@router.post("/{actor}/wake")
async def wake(actor: str, body: Wake, request: Request):
    key = os.environ.get("GARDEN_WAKE_LOCAL_KEY", "").strip()
    given = request.headers.get("x-garden-wake-key", "")
    from_tunnel = any(name.lower().startswith("cf-") for name in request.headers.keys())
    if (not key or from_tunnel or (request.client and request.client.host) not in _LOCAL
            or not hmac.compare_digest(given.encode(), key.encode())):
        raise HTTPException(403, "不收")
    try:
        result = garden_wake.receive(_actor(actor), body.reason, body.message)
    except ValueError as error:
        raise HTTPException(400, str(error))
    return {"accepted": True, "result": result}


@router.get("/status")
async def status():
    return {"actors": await asyncio.to_thread(garden_wake.status)}


@router.post("/{actor}/connect")
async def connect(actor: str):
    ok, error = await asyncio.to_thread(garden_wake.connect, _actor(actor))
    if not ok:
        raise HTTPException(502, f"没连上：{error or '服务器上还没装好唤醒桥'}")
    return {"ok": True}


@router.post("/{actor}/disconnect")
async def disconnect(actor: str):
    ok, error = await asyncio.to_thread(garden_wake.disconnect, _actor(actor))
    if not ok:
        raise HTTPException(502, f"没断开：{error or '服务器上还没装好唤醒桥'}")
    return {"ok": True}


@router.post("/{actor}/notify")
async def notify(actor: str, body: Notify):
    garden_wake.set_notify(_actor(actor), body.on)
    return {"ok": True, "notify": body.on}
