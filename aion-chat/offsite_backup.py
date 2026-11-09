"""
异地备份（11-09 小猫）：每天把 data/ 打包、用小猫的备份密码加密，传到 Cloudflare R2。

- 数据库用 SQLite 的备份接口拷一致的快照，不直接复制正在写的文件。
- 钥匙和密码由小猫在页面上填，存在 data/offsite_backup.json（权限 600），接口只回「填过没有」，不写日志、不进备份包。
- 文件在本机加密后才离开服务器，R2 上看不到内容。没有备份密码就打不开。
- 只删自己前缀下、符合命名规则的旧备份，并且只在这次上传成功之后。
- 恢复演练：下载最新一份到临时目录，解密、解包、检查数据库完整，全程不碰线上数据。
  真要恢复时用 tools/offsite_restore.py。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import sqlite3
import struct
import tarfile
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator
from urllib.parse import quote
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from config import BASE_DIR, DATA_DIR
from request_limits import read_body_limited

router = APIRouter()
log = logging.getLogger("uvicorn.error")

CONFIG_PATH = DATA_DIR / "offsite_backup.json"
STATUS_PATH = DATA_DIR / "offsite_backup_status.json"
PREFIX = "aionshome/"
KEEP = 14
MIN_PASSPHRASE = 16
RUN_HOUR, RUN_MINUTE = 4, 30
TICK_SECONDS = 600
HTTP_TIMEOUT = 600.0
MAX_BODY_BYTES = 8 * 1024
CHUNK_SIZE = 1024 * 1024
MAGIC = b"AHBK1"
BEIJING = ZoneInfo("Asia/Shanghai")
_ACCOUNT = re.compile(r"^[0-9a-f]{32}$")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
_KEY_ID = re.compile(r"^[A-Za-z0-9]{16,128}$")
_NAME = re.compile(r"^aionshome/\d{8}T\d{6}Z\.tar\.gz\.enc$")
_SKIP_SUFFIX = ("-wal", "-shm", "-journal", ".tmp")
_lock = threading.Lock()
_tasks: set[asyncio.Task] = set()


class BackupError(Exception):
    """message 只放给人看的分类，不含钥匙和路径。"""


# ── 配置和状态 ──

def load_config() -> dict[str, str]:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_private(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False)
    os.replace(tmp, path)


def is_ready(cfg: dict[str, str]) -> bool:
    return all(cfg.get(k) for k in ("account_id", "access_key_id", "secret_access_key", "bucket", "passphrase"))


def merge_config(current: dict[str, str], body: dict[str, Any]) -> dict[str, str]:
    """留空的项保持原值，这样改一项不用重填全部。"""
    merged = dict(current)
    for key in ("account_id", "access_key_id", "secret_access_key", "bucket", "passphrase"):
        value = str(body.get(key) or "").strip()
        if value:
            merged[key] = value
    merged.setdefault("bucket", "animalshome-backup")
    if merged.get("account_id") and not _ACCOUNT.match(merged["account_id"]):
        raise HTTPException(400, "Account ID 应该是 32 位的数字和字母 a-f")
    if not _BUCKET.match(merged["bucket"]):
        raise HTTPException(400, "存储桶名字不对")
    if merged.get("access_key_id") and not _KEY_ID.match(merged["access_key_id"]):
        raise HTTPException(400, "Access Key ID 格式不对")
    if merged.get("passphrase") and len(merged["passphrase"]) < MIN_PASSPHRASE:
        raise HTTPException(400, f"备份密码至少 {MIN_PASSPHRASE} 位")
    return merged


def load_status() -> dict[str, Any]:
    try:
        data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _update_status(**fields: Any) -> None:
    _write_private(STATUS_PATH, {**load_status(), **fields})


# ── 打包 ──

def snapshot(source: Path, target: Path) -> int:
    """把 source 拷到 target，数据库用备份接口。返回文件数。"""
    count = 0
    secrets = {CONFIG_PATH.name, STATUS_PATH.name}  # 钥匙和密码不进备份包
    for root, dirs, files in os.walk(source, followlinks=False):
        rel = Path(root).relative_to(source)
        (target / rel).mkdir(parents=True, exist_ok=True)
        for name in files:
            src = Path(root) / name
            if src.is_symlink() or name in secrets or name.endswith(_SKIP_SUFFIX):
                continue
            dst = target / rel / name
            if name.endswith(".db"):
                try:
                    _copy_database(src, dst)
                except sqlite3.DatabaseError:  # 名字叫 .db 但不是 SQLite 的文件，照常拷
                    shutil.copy2(src, dst)
            else:
                shutil.copy2(src, dst)
            count += 1
    return count


def _copy_database(src: Path, dst: Path) -> None:
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def make_archive(folder: Path, archive: Path) -> None:
    with tarfile.open(archive, "w:gz", compresslevel=6) as tar:
        tar.add(folder, arcname="data", recursive=True)


# ── 加密：分块 AES-GCM，最后一块带标记，截断或改动都解不开 ──

def _derive(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def _nonce(prefix: bytes, counter: int, final: bool) -> bytes:
    return prefix + struct.pack(">I", counter) + (b"\x01" if final else b"\x00")


def encrypt_stream(source: BinaryIO, target: BinaryIO, passphrase: str) -> None:
    salt, prefix = os.urandom(16), os.urandom(7)
    header = MAGIC + salt + prefix
    target.write(header)
    cipher = AESGCM(_derive(passphrase, salt))
    counter = 0
    block = source.read(CHUNK_SIZE)
    while True:
        following = source.read(CHUNK_SIZE)
        sealed = cipher.encrypt(_nonce(prefix, counter, not following), block, header)
        target.write(struct.pack(">I", len(sealed)) + sealed)
        if not following:
            return
        block, counter = following, counter + 1


def decrypt_stream(source: BinaryIO, target: BinaryIO, passphrase: str) -> None:
    header = source.read(len(MAGIC) + 16 + 7)
    if len(header) != len(MAGIC) + 23 or not header.startswith(MAGIC):
        raise BackupError("这不是备份文件")
    salt, prefix = header[len(MAGIC):len(MAGIC) + 16], header[-7:]
    cipher = AESGCM(_derive(passphrase, salt))

    def next_block() -> bytes | None:
        raw = source.read(4)
        if not raw:
            return None
        if len(raw) != 4:
            raise BackupError("备份文件不完整")
        size = struct.unpack(">I", raw)[0]
        if size > CHUNK_SIZE + 16:
            raise BackupError("备份文件损坏")
        sealed = source.read(size)
        if len(sealed) != size:
            raise BackupError("备份文件不完整")
        return sealed

    counter = 0
    block = next_block()
    if block is None:
        raise BackupError("备份文件不完整")
    try:
        while True:
            following = next_block()
            target.write(cipher.decrypt(_nonce(prefix, counter, following is None), block, header))
            if following is None:
                return
            block, counter = following, counter + 1
    except InvalidTag:
        raise BackupError("备份密码不对，或者文件被改动过") from None


# ── R2（S3 接口，SigV4 签名）──

def _sign(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()


def auth_headers(method: str, host: str, path: str, query: dict[str, str], access_key: str, secret: str,
                 now: datetime | None = None) -> dict[str, str]:
    now = now or datetime.now(timezone.utc)
    stamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    payload = "UNSIGNED-PAYLOAD"
    canonical_query = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(query.items()))
    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload}\nx-amz-date:{stamp}\n"
    signed = "host;x-amz-content-sha256;x-amz-date"
    request = "\n".join([method, quote(path, safe="/-_.~"), canonical_query, canonical_headers, signed, payload])
    scope = f"{day}/auto/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(request.encode()).hexdigest()])
    key = _sign(_sign(_sign(_sign(("AWS4" + secret).encode(), day), "auto"), "s3"), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {"x-amz-date": stamp, "x-amz-content-sha256": payload,
            "Authorization": f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={signature}"}


class R2:
    def __init__(self, cfg: dict[str, str], client: httpx.Client | None = None):
        self.cfg = cfg
        self.host = f"{cfg['account_id']}.r2.cloudflarestorage.com"
        self.client = client or httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=False)

    def _request(self, method: str, key: str = "", query: dict[str, str] | None = None, **kwargs: Any) -> httpx.Response:
        path = f"/{self.cfg['bucket']}" + (f"/{key}" if key else "")
        query = query or {}
        headers = auth_headers(method, self.host, path, query, self.cfg["access_key_id"], self.cfg["secret_access_key"])
        headers.update(kwargs.pop("headers", {}))
        try:
            response = self.client.request(method, f"https://{self.host}{quote(path, safe='/-_.~')}", params=query, headers=headers, **kwargs)
        except httpx.HTTPError as error:
            raise BackupError(f"连不上存储（{type(error).__name__}）") from None
        if response.status_code in (401, 403):
            raise BackupError("R2 不接受这把钥匙")
        if response.status_code == 404:
            raise BackupError("找不到这个存储桶或文件")
        if response.status_code >= 300:
            raise BackupError(f"R2 返回了错误（{response.status_code}）")
        return response

    def put(self, key: str, path: Path) -> None:
        def chunks() -> Iterator[bytes]:
            with open(path, "rb") as handle:
                while block := handle.read(CHUNK_SIZE):
                    yield block
        self._request("PUT", key, headers={"Content-Length": str(path.stat().st_size)}, content=chunks())

    def list_backups(self) -> list[str]:
        names: list[str] = []
        token = ""
        for _ in range(50):
            query = {"list-type": "2", "prefix": PREFIX}
            if token:
                query["continuation-token"] = token
            root = ElementTree.fromstring(self._request("GET", query=query).content)
            ns = root.tag[:root.tag.index("}") + 1] if root.tag.startswith("{") else ""
            names += [node.text or "" for node in root.iter(f"{ns}Key")]
            following = root.find(f"{ns}NextContinuationToken")
            if following is None or not following.text:
                break
            token = following.text
        return sorted(n for n in names if _NAME.match(n))

    def get(self, key: str, path: Path) -> None:
        """边收边写到磁盘：这台机器内存小，备份文件不整个放进内存。"""
        remote = f"/{self.cfg['bucket']}/{key}"
        headers = auth_headers("GET", self.host, remote, {}, self.cfg["access_key_id"], self.cfg["secret_access_key"])
        try:
            with self.client.stream("GET", f"https://{self.host}{quote(remote, safe='/-_.~')}", headers=headers) as response:
                if response.status_code != 200:
                    raise BackupError(f"下载失败（{response.status_code}）")
                with open(path, "wb") as out:
                    for block in response.iter_bytes(CHUNK_SIZE):
                        out.write(block)
        except httpx.HTTPError as error:
            raise BackupError(f"连不上存储（{type(error).__name__}）") from None

    def delete(self, key: str) -> None:
        self._request("DELETE", key)


def prune(store: R2, keep: int = KEEP) -> int:
    names = store.list_backups()
    expired = names[:-keep] if keep > 0 else []
    for name in expired:
        store.delete(name)
    return len(expired)


# ── 备份和演练 ──

def run_backup(store: R2 | None = None, source: Path | None = None) -> dict[str, Any]:
    cfg = load_config()
    if not is_ready(cfg):
        raise BackupError("钥匙和密码还没填全")
    if not _lock.acquire(blocking=False):
        raise BackupError("正在备份，等一下")
    try:
        store = store or R2(cfg)
        with tempfile.TemporaryDirectory(prefix="ahbk-") as tmp:
            work = Path(tmp)
            copy = work / "data"
            files = snapshot(source or DATA_DIR, copy)
            archive, sealed = work / "data.tar.gz", work / "data.enc"
            make_archive(copy, archive)
            with open(archive, "rb") as plain, open(sealed, "wb") as out:
                encrypt_stream(plain, out, cfg["passphrase"])
            name = f"{PREFIX}{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.tar.gz.enc"
            size = sealed.stat().st_size
            store.put(name, sealed)
        pruned = prune(store)
        return {"name": name, "bytes": size, "files": files, "pruned": pruned}
    finally:
        _lock.release()


def check_restored(folder: Path) -> dict[str, int]:
    databases = bad = 0
    for path in folder.rglob("*.db"):
        databases += 1
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                bad += 1
        except sqlite3.DatabaseError:
            bad += 1
        finally:
            conn.close()
    return {"databases": databases, "bad": bad, "files": sum(1 for p in folder.rglob("*") if p.is_file())}


def extract_safely(archive: Path, target: Path) -> None:
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(target, filter="data")


def run_drill(store: R2 | None = None) -> dict[str, Any]:
    cfg = load_config()
    if not is_ready(cfg):
        raise BackupError("钥匙和密码还没填全")
    if not _lock.acquire(blocking=False):
        raise BackupError("正在备份，等一下")
    try:
        store = store or R2(cfg)
        names = store.list_backups()
        if not names:
            raise BackupError("R2 上还没有备份")
        with tempfile.TemporaryDirectory(prefix="ahbk-drill-") as tmp:
            work = Path(tmp)
            sealed, archive = work / "b.enc", work / "b.tar.gz"
            store.get(names[-1], sealed)
            with open(sealed, "rb") as source, open(archive, "wb") as out:
                decrypt_stream(source, out, cfg["passphrase"])
            extract_safely(archive, work / "out")
            result = check_restored(work / "out")
        if result["bad"] or not result["databases"]:
            raise BackupError(f"恢复出来的数据有问题（{result['bad']} 个库损坏）")
        return {"name": names[-1], **result}
    finally:
        _lock.release()


def _safe(job, stamp_key: str, result_key: str, error_key: str) -> dict[str, Any]:
    now = time.time()
    try:
        result = job()
        _update_status(**{stamp_key: now, result_key: result, error_key: ""})
        return {"ok": True, **result}
    except BackupError as error:
        message = str(error)
    except Exception as error:
        message = f"出错了（{type(error).__name__}）"
    log.warning("[offsite_backup] %s：%s", stamp_key, message)
    _update_status(**{stamp_key: now, error_key: message})
    return {"ok": False, "error": message}


def backup_now() -> dict[str, Any]:
    return _safe(run_backup, "last_run", "last_ok", "last_error")


def drill_now() -> dict[str, Any]:
    return _safe(run_drill, "last_drill", "drill_ok", "drill_error")


def due(now: datetime, last_run: float | None, ready: bool) -> bool:
    if not ready or (now.hour, now.minute) < (RUN_HOUR, RUN_MINUTE):
        return False
    return not last_run or datetime.fromtimestamp(last_run, BEIJING).date() < now.date()


async def _loop() -> None:
    while True:
        await asyncio.sleep(TICK_SECONDS)
        try:
            if due(datetime.now(BEIJING), load_status().get("last_run"), is_ready(load_config())):
                await asyncio.to_thread(backup_now)
        except Exception as error:
            log.warning("[offsite_backup] 循环出错：%s", type(error).__name__)


def start() -> None:
    task = asyncio.create_task(_loop())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ── 页面（小猫登录后才进得来）──

@router.get("/offsite-backup")
async def page():
    return FileResponse(BASE_DIR / "static" / "offsite-backup.html", headers={"Cache-Control": "no-cache"})


def public_state() -> dict[str, Any]:
    cfg = load_config()
    return {"filled": {k: bool(cfg.get(k)) for k in ("account_id", "access_key_id", "secret_access_key", "passphrase")},
            "bucket": cfg.get("bucket") or "animalshome-backup", "ready": is_ready(cfg), "status": load_status(), "keep": KEEP}


@router.get("/api/offsite-backup")
async def api_state():
    return await asyncio.to_thread(public_state)


@router.put("/api/offsite-backup")
async def api_save(request: Request):
    try:
        body = json.loads(await read_body_limited(request, MAX_BODY_BYTES, "内容太长"))
    except ValueError:
        raise HTTPException(400, "格式不正确") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "格式不正确")
    merged = merge_config(load_config(), body)
    await asyncio.to_thread(_write_private, CONFIG_PATH, merged)
    return await asyncio.to_thread(public_state)


@router.post("/api/offsite-backup/run")
async def api_run():
    return await asyncio.to_thread(backup_now)


@router.post("/api/offsite-backup/drill")
async def api_drill():
    return await asyncio.to_thread(drill_now)
