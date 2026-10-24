"""第二道锁：经过 Cloudflare 来的请求必须带着验证通过的 Access 登录凭证；Tailscale / 本机照旧。"""

import asyncio
import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

import cf_access

TEAM = "claudexiaoke"
AUD = "aud-tag-123"
HOST = "home.animalshome.top"
ISSUER = f"https://{TEAM}.cloudflareaccess.com"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _int(value: int) -> str:
    return _b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))


KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks(key=KEY, kid="k1"):
    numbers = key.public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "kid": kid, "e": _int(numbers.e), "n": _int(numbers.n)}]}


def token(*, key=KEY, kid="k1", alg="RS256", **claims):
    payload = {"aud": [AUD], "iss": ISSUER, "exp": time.time() + 600, "nbf": time.time() - 10,
               "email": "cat@example.com"}
    payload.update(claims)
    head = _b64(json.dumps({"alg": alg, "kid": kid}).encode())
    body = _b64(json.dumps(payload).encode())
    signature = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{_b64(signature)}"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("AIONSHOME_CF_ACCESS_TEAM", TEAM)
    monkeypatch.setenv("AIONSHOME_CF_ACCESS_AUD", AUD)
    monkeypatch.setenv("AIONSHOME_PUBLIC_HOST", HOST)
    fetches = []

    async def fetch(issuer):
        fetches.append(issuer)
        return _jwks()
    monkeypatch.setattr(cf_access, "_fetch_certs", fetch)
    monkeypatch.setattr(cf_access, "_CERTS", cf_access._Certs())
    return fetches


def _run(scope):
    calls, sent = [], []

    async def app(scope, receive, send):
        calls.append(scope["path"])

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}
    asyncio.run(cf_access.CloudflareAccessMiddleware(app)(scope, receive, send))
    return calls, sent


def _scope(kind="http", **headers):
    return {"type": kind, "path": "/api/x",
            "headers": [(k.replace("_", "-").encode(), v.encode()) for k, v in headers.items()]}


def test_valid_token_passes(configured):
    calls, sent = _run(_scope(host=HOST, cf_access_jwt_assertion=token()))
    assert calls == ["/api/x"] and sent == []
    calls, _ = _run(_scope(host=HOST, cookie=f"a=1; CF_Authorization={token()}"))
    assert calls == ["/api/x"]
    assert configured == [ISSUER]  # 公钥缓存着，不每次都取


@pytest.mark.parametrize("bad", [
    lambda: "",
    lambda: "not.a.jwt",
    lambda: token(key=OTHER_KEY),             # 别人签的
    lambda: token(aud=["other-app"]),         # 别的应用的凭证
    lambda: token(iss="https://evil.cloudflareaccess.com"),
    lambda: token(exp=time.time() - 3600),    # 过期
    lambda: token(nbf=time.time() + 3600),    # 还没生效
    lambda: token(alg="none"),
    lambda: token(kid="unknown"),
    lambda: token() + "x",                    # 签名被改
])
def test_bad_or_missing_token_is_rejected(configured, bad):
    calls, sent = _run(_scope(host=HOST, cf_access_jwt_assertion=bad()))
    assert calls == [] and sent[0]["status"] == 403


def test_cloudflare_headers_count_even_with_another_host(configured):
    calls, sent = _run(_scope(host="127.0.0.1:8080", cf_ray="abc"))
    assert calls == [] and sent[0]["status"] == 403


def test_websocket_without_login_is_closed(configured):
    calls, sent = _run(_scope("websocket", host=HOST))
    assert calls == [] and sent == [{"type": "websocket.close", "code": 1008, "reason": "login required"}]
    calls, sent = _run(_scope("websocket", host=HOST, cookie=f"CF_Authorization={token()}"))
    assert calls == ["/api/x"]


def test_tailscale_and_local_requests_unaffected(configured):
    calls, sent = _run(_scope(host="aionshome.tail1234.ts.net"))
    assert calls == ["/api/x"] and sent == []
    assert configured == []


def test_not_configured_is_a_no_op(monkeypatch):
    for name in ("AIONSHOME_CF_ACCESS_TEAM", "AIONSHOME_CF_ACCESS_AUD", "AIONSHOME_PUBLIC_HOST"):
        monkeypatch.delenv(name, raising=False)
    calls, sent = _run(_scope(host=HOST, cf_ray="abc"))
    assert calls == ["/api/x"]


def test_unknown_key_triggers_one_refetch_after_rotation(configured, monkeypatch):
    assert asyncio.run(cf_access.verify(token(), cf_access.settings()))
    rotated = {"keys": _jwks()["keys"] + _jwks(OTHER_KEY, "k2")["keys"]}

    async def fetch(issuer):
        configured.append(issuer)
        return rotated
    monkeypatch.setattr(cf_access, "_fetch_certs", fetch)
    cf_access._CERTS.attempted_at -= cf_access.CERTS_MIN_REFETCH_SECONDS + 1  # 过了冷却时间
    assert asyncio.run(cf_access.verify(token(key=OTHER_KEY, kid="k2"), cf_access.settings()))
    assert len(configured) == 2
    # 刚取过：陌生的 kid 不会每次请求都去取（防止被刷）
    assert not asyncio.run(cf_access.verify(token(kid="k9"), cf_access.settings()))
    assert len(configured) == 2


def test_cert_fetch_failure_rejects(configured, monkeypatch):
    async def fail(issuer):
        raise OSError("down")
    monkeypatch.setattr(cf_access, "_fetch_certs", fail)
    calls, sent = _run(_scope(host=HOST, cf_access_jwt_assertion=token()))
    assert calls == [] and sent[0]["status"] == 403


@pytest.mark.parametrize("result", ["fail", "empty"])
def test_cold_cache_burst_fetches_once(configured, monkeypatch, result):
    """§72 修复审 M2：空缓存时一批并发请求、取公钥失败或取到空列表，也只去取一次。"""
    calls = []

    async def fetch(issuer):
        calls.append(issuer)
        await asyncio.sleep(0.01)
        if result == "fail":
            raise OSError("down")
        return {"keys": []}
    monkeypatch.setattr(cf_access, "_fetch_certs", fetch)

    async def burst():
        cfg = cf_access.settings()
        return await asyncio.gather(*(cf_access.verify(token(), cfg) for _ in range(6)))
    assert asyncio.run(burst()) == [False] * 6
    assert len(calls) == 1


@pytest.mark.parametrize("header", [[], None, 42, "x", {"alg": "RS256", "kid": 7}, {"alg": "RS256", "kid": ""}])
def test_malformed_header_is_a_clean_rejection(configured, header):
    """§72 修复审 L2：header 不是对象 / kid 不是字符串：稳定返回拒绝，不抛异常。"""
    bad = token().split(".")
    bad[0] = _b64(json.dumps(header).encode())
    assert asyncio.run(cf_access.verify(".".join(bad), cf_access.settings())) is False


@pytest.mark.parametrize("claims", [
    {"nbf": "tomorrow"}, {"nbf": None}, {"nbf": True},
    {"exp": float("nan")}, {"exp": float("inf")}, {"exp": True}, {"exp": "later"},
])
def test_time_claims_must_be_finite_numbers(configured, claims):
    assert asyncio.run(cf_access.verify(token(**claims), cf_access.settings())) is False
