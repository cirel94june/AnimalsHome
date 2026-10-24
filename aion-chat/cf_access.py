"""第二道锁：从 Cloudflare 隧道进来的请求，必须带着 Cloudflare Access 签发、且验证通过的登录凭证。

门锁本身是 Cloudflare Access（只允许小猫的邮箱登录）。这里在 AionsHome 里再核对一次：
- 凭证在请求头 `Cf-Access-Jwt-Assertion`（Cloudflare 自动加）或 Cookie `CF_Authorization` 里；
- 用团队域名公开的公钥（/cdn-cgi/access/certs）验 RS256 签名，并核对 aud（这个应用的 AUD 标签）、
  iss（团队域名）、有效期；
- 不合格：普通请求回 403，实时连接（WebSocket）直接关闭。

只管「经过 Cloudflare 来的」请求：Host 是公开地址，或者带着 Cloudflare 的请求头。从 Tailscale /
本机来的请求照旧（它们本来就只有小猫的设备能到）。没配置（环境变量为空）时什么都不做（本地开发）。

配置（/etc/aionshome.env）：
  AIONSHOME_CF_ACCESS_TEAM=claudexiaoke          # 团队名，登录页是 <团队>.cloudflareaccess.com
  AIONSHOME_CF_ACCESS_AUD=<应用的 AUD 标签>
  AIONSHOME_PUBLIC_HOST=home.animalshome.top
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

log = logging.getLogger("cf_access")

CERTS_TTL_SECONDS = 3600
CERTS_MIN_REFETCH_SECONDS = 60
CLOCK_SKEW_SECONDS = 60
_CF_HEADERS = ("cf-ray", "cf-connecting-ip", "cf-access-jwt-assertion", "cdn-loop")


def settings() -> dict | None:
    team = os.environ.get("AIONSHOME_CF_ACCESS_TEAM", "").strip()
    aud = os.environ.get("AIONSHOME_CF_ACCESS_AUD", "").strip()
    host = os.environ.get("AIONSHOME_PUBLIC_HOST", "").strip().lower()
    if not (team and aud and host):
        return None
    return {"team": team, "aud": aud, "host": host, "issuer": f"https://{team}.cloudflareaccess.com"}


def _b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _b64int(value: str) -> int:
    return int.from_bytes(_b64url(value), "big")


class _Certs:
    def __init__(self):
        self.keys: dict[str, rsa.RSAPublicKey] = {}
        self.fetched_at = 0.0
        self.lock = asyncio.Lock()

    async def get(self, issuer: str, kid: str, *, fetch=None) -> rsa.RSAPublicKey | None:
        now = time.monotonic()
        stale = now - self.fetched_at > CERTS_TTL_SECONDS
        missing = kid not in self.keys and now - self.fetched_at > CERTS_MIN_REFETCH_SECONDS
        if stale or missing:
            async with self.lock:
                if time.monotonic() - self.fetched_at > CERTS_MIN_REFETCH_SECONDS or not self.keys:
                    try:
                        payload = await (fetch or _fetch_certs)(issuer)
                        keys = {}
                        for item in payload.get("keys", []):
                            if item.get("kty") == "RSA" and item.get("kid"):
                                keys[item["kid"]] = rsa.RSAPublicNumbers(
                                    _b64int(item["e"]), _b64int(item["n"])).public_key()
                        if keys:
                            self.keys = keys
                        self.fetched_at = time.monotonic()
                    except Exception as error:  # 取公钥失败：保留旧的，验证不过就拒绝
                        log.warning("[cf_access] 取公钥失败：%s", type(error).__name__)
                        self.fetched_at = time.monotonic()
        return self.keys.get(kid)


async def _fetch_certs(issuer: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{issuer}/cdn-cgi/access/certs")
        response.raise_for_status()
        return response.json()


_CERTS = _Certs()


async def verify(token: str, cfg: dict, *, now: float | None = None, certs: _Certs | None = None) -> bool:
    """验 Cloudflare Access 的登录凭证（RS256）。任何一项不对都返回 False。"""
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        header = json.loads(_b64url(header_b64))
        payload = json.loads(_b64url(payload_b64))
        if header.get("alg") != "RS256" or not header.get("kid"):
            return False
        key = await (certs or _CERTS).get(cfg["issuer"], header["kid"])
        if key is None:
            return False
        key.verify(_b64url(signature_b64), f"{header_b64}.{payload_b64}".encode("ascii"),
                   padding.PKCS1v15(), hashes.SHA256())
    except (ValueError, InvalidSignature, TypeError, UnicodeError, json.JSONDecodeError):
        return False
    now = time.time() if now is None else now
    aud = payload.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if cfg["aud"] not in auds or payload.get("iss") != cfg["issuer"]:
        return False
    exp, nbf = payload.get("exp"), payload.get("nbf", 0)
    if not isinstance(exp, (int, float)) or now > exp + CLOCK_SKEW_SECONDS:
        return False
    if isinstance(nbf, (int, float)) and now + CLOCK_SKEW_SECONDS < nbf:
        return False
    return True


def _token_from(headers: dict[str, str]) -> str:
    token = headers.get("cf-access-jwt-assertion", "").strip()
    if token:
        return token
    for part in headers.get("cookie", "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == "CF_Authorization" and value:
            return value.strip()
    return ""


def via_cloudflare(headers: dict[str, str], cfg: dict) -> bool:
    host = headers.get("host", "").split(":")[0].strip().lower()
    return host == cfg["host"] or any(name in headers for name in _CF_HEADERS)


class CloudflareAccessMiddleware:
    """纯 ASGI 外层：经过 Cloudflare 来的 http / websocket 请求都要先验凭证。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        cfg = settings()
        if cfg is None or scope.get("type") not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = {name.decode("latin-1").lower(): value.decode("latin-1")
                   for name, value in scope.get("headers", [])}
        if via_cloudflare(headers, cfg):
            token = _token_from(headers)
            if not token or not await verify(token, cfg):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008, "reason": "login required"})
                else:
                    body = b'{"detail":"login required"}'
                    await send({"type": "http.response.start", "status": 403,
                                "headers": [(b"content-type", b"application/json"),
                                            (b"content-length", str(len(body)).encode())]})
                    await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
