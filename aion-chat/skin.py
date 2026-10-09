"""
外观皮肤（IB 亮色 · Internal）与美化工作台。

- 皮肤以独立的样式层叠加在原有页面上（static/ib/），不改动原页面文件；
  SkinInjectMiddleware 会在每个 HTML 页面的 </head> 前插入皮肤脚本，关掉皮肤即回到原版外观。
- 设置保存在 data/skin.json，手机 App 与浏览器共用同一份；背景图存放在 data/skin/。
- /skin 是美化工作台页面。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from config import BASE_DIR, DATA_DIR

router = APIRouter()

SKIN_PATH = DATA_DIR / "skin.json"
SKIN_ASSET_DIR = DATA_DIR / "skin"
STATIC_DIR = BASE_DIR / "static" / "ib"
ASSET_VERSION = "20261009"

MAX_SKIN_BYTES = 256 * 1024
MAX_BG_BYTES = 8 * 1024 * 1024
BG_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}

# 不注入皮肤的路径：访客/对外页面保持原样，运维页面也不需要
SKIP_PREFIXES = ("/lounge-board", "/visitor", "/ops/", "/static/ib/")

DEFAULT_SKIN: dict[str, Any] = {"version": 1, "enabled": True}


def load_skin() -> dict[str, Any]:
    if SKIN_PATH.exists():
        try:
            data = json.loads(SKIN_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return dict(DEFAULT_SKIN)


def save_skin(data: dict[str, Any]) -> None:
    SKIN_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SKIN_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SKIN_PATH)


@router.get("/api/skin")
async def get_skin():
    return load_skin()


@router.put("/api/skin")
async def put_skin(request: Request):
    raw = await request.body()
    if len(raw) > MAX_SKIN_BYTES:
        raise HTTPException(413, "外观设置过大")
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "格式不正确")
    if not isinstance(data, dict):
        raise HTTPException(400, "格式不正确")
    data["version"] = 1
    data["updated_at"] = time.time()
    save_skin(data)
    return data


@router.post("/api/skin/background")
async def upload_background(file: UploadFile = File(...)):
    ext = BG_TYPES.get((file.content_type or "").lower())
    if not ext:
        raise HTTPException(400, "只支持 jpg / png / webp / gif 图片")
    content = await file.read()
    if len(content) > MAX_BG_BYTES:
        raise HTTPException(413, "图片不能超过 8MB")
    SKIN_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(content).hexdigest()[:16] + ext
    (SKIN_ASSET_DIR / name).write_bytes(content)
    return {"url": f"/skin-assets/{name}"}


@router.get("/skin-assets/{name}")
async def skin_asset(name: str):
    if not re.fullmatch(r"[0-9a-f]{16}\.(jpg|png|webp|gif)", name):
        raise HTTPException(404)
    path = SKIN_ASSET_DIR / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/skin")
async def skin_workbench():
    return FileResponse(STATIC_DIR / "workbench.html", headers={"Cache-Control": "no-cache"})


_INJECT = (
    f'<link rel="stylesheet" href="/static/ib/ib-skin.css?v={ASSET_VERSION}" id="ib-skin-css">'
    f'<script src="/static/ib/ib-skin.js?v={ASSET_VERSION}"></script>'
).encode()


def inject_html(body: bytes) -> bytes:
    """在 </head> 前插入皮肤样式与脚本。中间件与 asset_manifest 共用，
    保证 Android App 校验的页面哈希与服务端实际返回的内容一致。"""
    index = body.lower().rfind(b"</head>")
    if index == -1 or b"ib-skin.js" in body:
        return body
    return body[:index] + _INJECT + body[index:]


class SkinInjectMiddleware:
    """在 HTML 响应的 </head> 前插入皮肤样式与脚本（纯 ASGI，不缓冲非 HTML 响应）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "GET" or scope.get("path", "").startswith(SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return

        start_message: dict | None = None
        chunks: list[bytes] = []
        passthrough = False

        async def wrapped_send(message):
            nonlocal start_message, passthrough
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers") or [])
                ctype = headers.get(b"content-type", b"").lower()
                encoding = headers.get(b"content-encoding", b"")
                if not ctype.startswith(b"text/html") or encoding or message.get("status") != 200:
                    passthrough = True
                    await send(message)
                    return
                start_message = message
                return
            if message["type"] == "http.response.body" and not passthrough and start_message is not None:
                chunks.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                body = inject_html(b"".join(chunks))
                headers = [(k, v) for k, v in start_message.get("headers", []) if k.lower() != b"content-length"]
                headers.append((b"content-length", str(len(body)).encode()))
                await send({**start_message, "headers": headers})
                await send({"type": "http.response.body", "body": body})
                return
            await send(message)

        await self.app(scope, receive, wrapped_send)
