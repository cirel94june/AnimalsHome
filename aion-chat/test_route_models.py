import asyncio
import gzip

import httpx
import pytest

import relay_models as rm

REAL_CLIENT = httpx.AsyncClient


class SlowStream(httpx.AsyncByteStream):
    """每隔一会儿给一点数据：每块之间都没超过空闲时限，但总时间很长。"""

    def __init__(self, pieces, gap):
        self.pieces, self.gap = pieces, gap

    async def __aiter__(self):
        for piece in self.pieces:
            await asyncio.sleep(self.gap)
            yield piece


def _json(data):
    # 流式 body：Response(json=...) 在构造时就被读完了，和真实网络不一样
    import json
    return httpx.Response(200, stream=SlowStream([json.dumps(data).encode()], 0))


def _relay(monkeypatch, handler, *, resolve=None):
    seen = []

    def handle(request):
        seen.append(request)
        return handler(request)
    monkeypatch.setattr(rm, "_client", lambda: REAL_CLIENT(transport=httpx.MockTransport(handle), follow_redirects=False))

    async def fake_resolve(host, port):
        return resolve or ["93.184.216.34"]
    monkeypatch.setattr(rm, "_resolve", fake_resolve)
    monkeypatch.delenv("AIONSHOME_RELAY_ALLOW", raising=False)
    return seen


def _fetch(base_url, api_key=""):
    return asyncio.run(rm.fetch(base_url, api_key))


def test_models_url_follows_the_same_rules_as_chat_requests():
    assert str(rm.models_url("https://relay.test")) == "https://relay.test/v1/models"
    assert str(rm.models_url("https://relay.test/v1/")) == "https://relay.test/v1/models"
    assert str(rm.models_url("https://relay.test/v1/chat/completions")) == "https://relay.test/v1/models"
    for bad in ("ftp://relay.test", "https://u:p@relay.test", "https://relay.test/v1?x=1", "relay.test"):
        with pytest.raises(rm.RelayModelsError):
            rm.models_url(bad)


def test_model_ids_accepts_openai_and_gemini_shapes():
    assert rm.model_ids({"data": [{"id": "gpt-5"}, {"id": "claude-x"}, {"id": "gpt-5"}]}) == ["claude-x", "gpt-5"]
    assert rm.model_ids({"models": [{"name": "models/gemini-3-pro"}]}) == ["gemini-3-pro"]
    assert rm.model_ids(["a", 3, None]) == ["a"]
    assert rm.model_ids({"error": "x"}) == []


def test_connects_to_the_checked_ip_but_keeps_host_and_tls_name(monkeypatch):
    seen = _relay(monkeypatch, lambda r: _json({"data": [{"id": "b"}, {"id": "A"}]}))
    assert _fetch("https://relay.test/v1", " sk-1 ") == ["A", "b"]
    request = seen[0]
    assert str(request.url) == "https://93.184.216.34/v1/models"
    assert request.headers["host"] == "relay.test"
    assert request.extensions["sni_hostname"] == "relay.test"
    assert request.headers["authorization"] == "Bearer sk-1"
    assert request.headers["accept-encoding"] == "identity"


@pytest.mark.parametrize("status, words", [(401, "Key 不对"), (404, "不支持"), (502, "502"), (302, "跳转")])
def test_relay_errors_are_explained(monkeypatch, status, words):
    _relay(monkeypatch, lambda r: httpx.Response(status, headers={"location": "http://10.0.0.1/"}))
    with pytest.raises(rm.RelayModelsError) as err:
        _fetch("https://relay.test", "k")
    assert words in str(err.value)


def test_compressed_answer_is_refused_before_any_decompression(monkeypatch):
    bomb = gzip.compress(b" " * (32 * 1024 * 1024))
    decoded = []
    real_decode = httpx._decoders.GZipDecoder.decode

    def spy(self, data):
        decoded.append(len(data))
        return real_decode(self, data)
    monkeypatch.setattr(httpx._decoders.GZipDecoder, "decode", spy)
    _relay(monkeypatch, lambda r: httpx.Response(200, headers={"content-encoding": "gzip"},
                                                 stream=SlowStream([bomb], 0)))
    with pytest.raises(rm.RelayModelsError) as err:
        _fetch("https://relay.test")
    assert "压缩" in str(err.value) and decoded == []


def test_size_limit_counts_raw_bytes_and_trusts_content_length_early(monkeypatch):
    monkeypatch.setattr(rm, "MAX_BYTES", 10)
    _relay(monkeypatch, lambda r: httpx.Response(200, headers={"content-length": "999"}, stream=SlowStream([b"x"], 0)))
    with pytest.raises(rm.RelayModelsError, match="太大"):
        _fetch("https://relay.test")
    _relay(monkeypatch, lambda r: httpx.Response(200, stream=SlowStream([b"x" * 6, b"x" * 6], 0)))
    with pytest.raises(rm.RelayModelsError, match="太大"):
        _fetch("https://relay.test")


def test_total_deadline_stops_a_slow_drip(monkeypatch):
    monkeypatch.setattr(rm, "TOTAL_SECONDS", 0.3)
    _relay(monkeypatch, lambda r: httpx.Response(200, stream=SlowStream([b" "] * 10 + [b'{"data":[{"id":"a"}]}'], 0.1)))
    with pytest.raises(rm.RelayModelsError, match="20 秒"):
        _fetch("https://relay.test")
    assert rm._active == 0


@pytest.mark.parametrize("base_url, resolved", [
    ("http://127.0.0.1:8000", None),
    ("http://[::1]:8080", None),
    ("http://169.254.169.254", None),
    ("http://100.89.1.2", None),                              # Tailscale / CGNAT 也不算公网
    ("https://rebind.test", ["93.184.216.34", "10.0.0.5"]),  # 解析结果里混了内网地址
])
def test_private_targets_are_refused_without_a_request(monkeypatch, base_url, resolved):
    seen = _relay(monkeypatch, lambda r: _json({"data": [{"id": "x"}]}), resolve=resolved)
    with pytest.raises(rm.RelayModelsError, match="内网"):
        _fetch(base_url)
    assert seen == []


def test_admin_allowlist_opens_one_exact_host_port(monkeypatch):
    seen = _relay(monkeypatch, lambda r: _json({"data": [{"id": "x"}]}))
    monkeypatch.setenv("AIONSHOME_RELAY_ALLOW", "127.0.0.1:8317")
    assert _fetch("http://127.0.0.1:8317/v1") == ["x"]
    with pytest.raises(rm.RelayModelsError):
        _fetch("http://127.0.0.1:8888/v1")  # 同一台机器的别的端口仍然拒绝
    assert len(seen) == 1


def test_concurrent_pulls_are_capped_without_queueing(monkeypatch):
    _relay(monkeypatch, lambda r: httpx.Response(200, stream=SlowStream([b'{"data":[{"id":"a"}]}'], 0.2)))

    async def run():
        results = await asyncio.gather(*(rm.fetch("https://relay.test") for _ in range(3)), return_exceptions=True)
        return results
    results = asyncio.run(run())
    assert sum(r == ["a"] for r in results) == 2
    assert sum(isinstance(r, rm.RelayModelsError) for r in results) == 1
    assert rm._active == 0
