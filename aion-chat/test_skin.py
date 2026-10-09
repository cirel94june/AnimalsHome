import hashlib
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import skin


def _app(tmp_path):
    page = tmp_path / "page.html"
    page.write_text("<html><head><title>t</title></head><body>" + "内容" * 800 + "</body></html>", encoding="utf-8")
    app = FastAPI()

    @app.get("/page")
    async def html_page():
        return FileResponse(page)

    @app.get("/lounge-board")
    async def visitor_page():
        return FileResponse(page)

    @app.get("/api/data")
    async def data():
        return JSONResponse({"html": "</head>"})

    app.add_middleware(skin.SkinInjectMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=100)
    return app, page


def test_inject_html_is_idempotent_and_before_head_close():
    body = b"<html><head><title>x</title></head><body></body></html>"
    once = skin.inject_html(body)
    assert once.index(b"ib-skin.js") < once.index(b"</head>")
    assert skin.inject_html(once) == once
    assert skin.inject_html(b"no head here") == b"no head here"


def test_middleware_injects_html_under_gzip_and_skips_others(tmp_path):
    app, page = _app(tmp_path)
    client = TestClient(app)
    response = client.get("/page", headers={"Accept-Encoding": "gzip"})
    assert response.headers.get("content-encoding") == "gzip"
    assert b"ib-skin.js" in response.content
    assert response.content == skin.inject_html(page.read_bytes())
    assert b"ib-skin.js" not in client.get("/lounge-board").content
    assert client.get("/api/data").json() == {"html": "</head>"}


def test_manifest_hash_matches_served_document(tmp_path, monkeypatch):
    import asset_manifest

    app, page = _app(tmp_path)
    served = TestClient(app).get("/page").content
    monkeypatch.setattr(asset_manifest, "_DOCUMENT_ROUTES", {"/page": page.name})
    base = tmp_path / "base"
    (base / "static").mkdir(parents=True)
    (base / "static" / page.name).write_bytes(page.read_bytes())
    monkeypatch.setattr(asset_manifest, "BASE_DIR", base)
    monkeypatch.setattr(asset_manifest, "PUBLIC_DIR", tmp_path / "public-empty")
    (tmp_path / "public-empty").mkdir()
    monkeypatch.setattr(asset_manifest, "_CACHED_SIGNATURE", None)
    entry = asset_manifest.get_client_asset_manifest()["files"]["/page"]
    assert entry["sha256"] == hashlib.sha256(served).hexdigest()
    assert entry["size"] == len(served)


def test_skin_api_roundtrip_and_upload_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(skin, "SKIN_PATH", tmp_path / "skin.json")
    monkeypatch.setattr(skin, "SKIN_ASSET_DIR", tmp_path / "skin")
    app = FastAPI()
    app.include_router(skin.router)
    client = TestClient(app)

    assert client.get("/api/skin").json()["enabled"] is True
    saved = client.put("/api/skin", json={"enabled": True, "preset": "sakura", "vars": {"bubble_r": 22}}).json()
    assert saved["preset"] == "sakura" and saved["version"] == 1
    assert client.get("/api/skin").json()["vars"]["bubble_r"] == 22
    assert client.put("/api/skin", content=b"[1,2]", headers={"content-type": "application/json"}).status_code == 400

    bad = client.post("/api/skin/background", files={"file": ("a.txt", b"hi", "text/plain")})
    assert bad.status_code == 400
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    url = client.post("/api/skin/background", files={"file": ("bg.png", png, "image/png")}).json()["url"]
    assert client.get(url).content == png
    assert client.get("/skin-assets/../../etc/passwd").status_code == 404
