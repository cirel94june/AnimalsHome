"""服务器上换掉原作者的图：/public 同名替换、每位 AI 自己的自拍参考、App 离线清单按替换后的算。"""

import hashlib

from fastapi import FastAPI
from fastapi.testclient import TestClient

import asset_manifest
import image_gen
import public_overrides as po


def _setup(tmp_path, monkeypatch):
    pub = tmp_path / "public"
    pub.mkdir()
    (pub / "AIIcon.png").write_bytes(b"author-face")
    (pub / "other.png").write_bytes(b"other")
    over = tmp_path / "over"
    over.mkdir()
    monkeypatch.setattr(po, "OVERRIDE_DIR", over)
    monkeypatch.setattr(po, "SELFIE_DIR", tmp_path / "selfie")
    app = FastAPI()
    app.mount("/public", po.OverridableStaticFiles(directory=str(pub)), name="public")
    return TestClient(app), pub, over


def test_override_replaces_only_listed_files(tmp_path, monkeypatch):
    client, pub, over = _setup(tmp_path, monkeypatch)
    assert client.get("/public/AIIcon.png").content == b"author-face"
    (over / "AIIcon.png").write_bytes(b"our-face")
    (over / "other.png").write_bytes(b"sneaky")
    assert client.get("/public/AIIcon.png").content == b"our-face"
    assert client.get("/public/other.png").content == b"other"  # 不在名单里的不替换


def test_each_ai_uses_its_own_selfie_and_never_the_authors(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert not image_gen._selfie_reference_path("aion").exists()   # 没放：不带参考图
    (tmp_path / "selfie").mkdir()
    (tmp_path / "selfie" / "ai3.jpg").write_bytes(b"jasper")
    assert image_gen._selfie_reference_path("ai3").read_bytes() == b"jasper"
    assert not image_gen._selfie_reference_path("connor").exists()
    assert po.selfie_reference("../etc") is None


def test_app_offline_manifest_hashes_the_replaced_file(tmp_path, monkeypatch):
    _, pub, over = _setup(tmp_path, monkeypatch)
    (over / "AIIcon.png").write_bytes(b"our-face")
    monkeypatch.setattr(asset_manifest, "PUBLIC_DIR", pub)
    items = {url: path for url, path, _ in asset_manifest._iter_client_assets() if url.startswith("/public/")}
    assert items["/public/AIIcon.png"].read_bytes() == b"our-face"
    assert hashlib.sha256(items["/public/other.png"].read_bytes()).hexdigest()
