"""
请求体大小限制：防止误选大文件或异常客户端在返回 413 之前把小 VPS 的内存撑爆。

- BodySizeLimitMiddleware：全站兜底，按 Content-Length 提前拒绝，流式上传则边收边数；
  大多数接口上限 AIONSHOME_MAX_BODY_MB（默认 64MB），音乐站上传（本身分块落盘）单独放宽。
- read_upload_limited / read_body_limited：单个接口在全站上限之内再收紧，最多只读 limit+1 字节。
"""

from __future__ import annotations

import os

from fastapi import HTTPException, Request, UploadFile

MB = 1024 * 1024
DEFAULT_MAX_BODY = int(float(os.environ.get("AIONSHOME_MAX_BODY_MB") or 64) * MB)
# 这些接口自己分块写盘、有自己的上限
LARGE_BODY_PREFIXES = {"/api/music-station/upload": 320 * MB}


class _TooLarge(Exception):
    pass


def limit_for_path(path: str) -> int:
    for prefix, limit in LARGE_BODY_PREFIXES.items():
        if path.startswith(prefix):
            return limit
    return DEFAULT_MAX_BODY


class BodySizeLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = limit_for_path(scope.get("path") or "")
        for name, value in scope.get("headers") or []:
            if name == b"content-length":
                try:
                    if int(value) > limit:
                        return await _reject(send, limit)
                except ValueError:
                    return await _reject(send, limit, status=400)
        received = 0
        started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    raise _TooLarge()
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not started:
                await _reject(send, limit)


async def _reject(send, limit: int, status: int = 413):
    body = f'{{"detail":"请求过大（上限 {limit // MB}MB）"}}'.encode("utf-8") if status == 413 else b'{"detail":"bad request"}'
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


async def read_upload_limited(file: UploadFile, limit: int, message: str) -> bytes:
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(413, message)
    return content


async def read_body_limited(request: Request, limit: int, message: str = "请求过大") -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, message)
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, message)
        chunks.append(chunk)
    return b"".join(chunks)
