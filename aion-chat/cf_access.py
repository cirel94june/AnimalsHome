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
  AIONSHOME_CF_ACCESS_OUTING_AUD=<「回家借工具」那个 Access 应用的 AUD 标签>   # 可选，见下

回家借工具（TG 上的 bot 来借，docs/施工方案-bot出门.md）：
- Cloudflare 上单独建一个 Access 应用，只管 home.animalshome.top/api/outing/*，策略只认 bot 的服务钥匙；
  整站那个应用仍只认小猫的邮箱。
- 这里再拦一次，两个方向都拦：
  * /api/outing/* 只认「借工具应用」签发的、服务钥匙的凭证（有 common_name、没有 email），小猫的登录也进不来；
  * 其他路径只认整站应用的凭证，并且服务钥匙的凭证一律不认，免得服务钥匙进到别的地方。
- 没配 AIONSHOME_CF_ACCESS_OUTING_AUD 时，经 Cloudflare 来的 /api/outing/* 一律拒绝。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
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


OUTING_PREFIX = "/api/outing/"


def settings() -> dict | None:
    team = os.environ.get("AIONSHOME_CF_ACCESS_TEAM", "").strip()
    aud = os.environ.get("AIONSHOME_CF_ACCESS_AUD", "").strip()
    host = os.environ.get("AIONSHOME_PUBLIC_HOST", "").strip().lower()
    if not (team and aud and host):
        return None
    return {"team": team, "aud": aud, "host": host, "issuer": f"https://{team}.cloudflareaccess.com",
            "outing_aud": os.environ.get("AIONSHOME_CF_ACCESS_OUTING_AUD", "").strip()}


def is_outing_path(path: str) -> bool:
    """借工具那扇门。路径里带 .. 的一律不算（也不放进别处），由调用方拒绝。"""
    return path == OUTING_PREFIX.rstrip("/") or path.startswith(OUTING_PREFIX)


def is_service_token(claims: dict) -> bool:
    """Cloudflare 服务钥匙签发的凭证：有 common_name（服务钥匙的 Client ID），没有登录人的 email。"""
    return bool(claims.get("common_name")) and not claims.get("email")


def _b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _b64int(value: str) -> int:
    return int.from_bytes(_b64url(value), "big")


class _Certs:
    def __init__(self):
        self.keys: dict[str, rsa.RSAPublicKey] = {}
        self.fetched_at = 0.0           # 上次成功取到公钥的时间
        self.attempted_at: float | None = None  # 上次去取（不论成败）的时间；None = 还没取过
        self.lock = asyncio.Lock()

    def _fresh(self, kid: str, now: float) -> bool:
        return kid in self.keys and now - self.fetched_at <= CERTS_TTL_SECONDS

    async def get(self, issuer: str, kid: str, *, fetch=None) -> rsa.RSAPublicKey | None:
        if self._fresh(kid, time.monotonic()):
            return self.keys[kid]
        async with self.lock:
            now = time.monotonic()
            if self._fresh(kid, now):  # 排队期间别人已经取到了
                return self.keys[kid]
            # 成功、失败、取到空列表都算一次尝试：60 秒内不再去取（排队的这一批也不例外）
            if self.attempted_at is not None and now - self.attempted_at < CERTS_MIN_REFETCH_SECONDS:
                return self.keys.get(kid)
            self.attempted_at = now
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
        return self.keys.get(kid)


async def _fetch_certs(issuer: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{issuer}/cdn-cgi/access/certs")
        response.raise_for_status()
        return response.json()


_CERTS = _Certs()


async def verify(token: str, cfg: dict, *, now: float | None = None, certs: _Certs | None = None) -> bool:
    """验 Cloudflare Access 的登录凭证（RS256）。任何一项不对都返回 False。"""
    return await verify_claims(token, cfg, now=now, certs=certs) is not None


async def verify_claims(token: str, cfg: dict, *, aud: str | None = None, now: float | None = None,
                        certs: _Certs | None = None) -> dict | None:
    """验凭证并返回里面的内容；任何一项不对返回 None。aud 默认是整站应用的。"""
    expected_aud = cfg["aud"] if aud is None else aud
    if not expected_aud:
        return None
    try:
        header_b64, payload_b64, signature_b64 = str(token or "").split(".")
        header = json.loads(_b64url(header_b64))
        payload = json.loads(_b64url(payload_b64))
        if not isinstance(header, dict) or not isinstance(payload, dict):
            return None
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not header["kid"]:
            return None
        key = await (certs or _CERTS).get(cfg["issuer"], header["kid"])
        if key is None:
            return None
        key.verify(_b64url(signature_b64), f"{header_b64}.{payload_b64}".encode("ascii"),
                   padding.PKCS1v15(), hashes.SHA256())
    except (ValueError, InvalidSignature, TypeError, UnicodeError, json.JSONDecodeError):
        return None
    now = time.time() if now is None else now
    token_aud = payload.get("aud")
    auds = token_aud if isinstance(token_aud, list) else [token_aud]
    if expected_aud not in auds or payload.get("iss") != cfg["issuer"]:
        return None
    exp = payload.get("exp")
    if not _finite_number(exp) or now > exp + CLOCK_SKEW_SECONDS:
        return None
    if "nbf" in payload:  # 有 nbf 就必须是正常的数字
        nbf = payload["nbf"]
        if not _finite_number(nbf) or now + CLOCK_SKEW_SECONDS < nbf:
            return None
    return payload


def _finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


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
            path = str(scope.get("path") or "")
            if ".." in path.split("/"):
                allowed = False
            elif is_outing_path(path):
                # 借工具那扇门：只认借工具应用签发的服务钥匙凭证
                claims = await verify_claims(token, cfg, aud=cfg.get("outing_aud") or "") if token else None
                allowed = claims is not None and is_service_token(claims)
            else:
                # 别的地方：只认整站应用的凭证，服务钥匙一律不认
                claims = await verify_claims(token, cfg) if token else None
                allowed = claims is not None and not is_service_token(claims)
            if not allowed:
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
