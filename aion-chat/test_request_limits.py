import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import request_limits


def _app(monkeypatch, limit=1024):
    monkeypatch.setattr(request_limits, "DEFAULT_MAX_BODY", limit)
    monkeypatch.setattr(request_limits, "LARGE_BODY_PREFIXES", {"/big": 4096})
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request):
        return {"n": len(await request.body())}

    @app.post("/big")
    async def big(request: Request):
        return {"n": len(await request.body())}

    app.add_middleware(request_limits.BodySizeLimitMiddleware)
    return TestClient(app)


def test_declared_oversize_is_rejected_before_the_endpoint(monkeypatch):
    client = _app(monkeypatch)
    assert client.post("/echo", content=b"x" * 1000).json() == {"n": 1000}
    res = client.post("/echo", content=b"x" * 2000)
    assert res.status_code == 413 and "请求过大" in res.json()["detail"]


def test_streamed_oversize_without_length_is_cut_off(monkeypatch):
    client = _app(monkeypatch)

    def chunks():
        for _ in range(10):
            yield b"x" * 500

    assert client.post("/echo", content=chunks()).status_code == 413


def test_large_upload_routes_get_their_own_limit(monkeypatch):
    client = _app(monkeypatch)
    assert client.post("/big", content=b"x" * 3000).json() == {"n": 3000}
    assert client.post("/big", content=b"x" * 5000).status_code == 413
