"""Independent review: Gemini listings keep the original SSRF/streaming boundaries."""
import asyncio

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import relay_models as rm
from routes import settings
from test_route_models import _relay, _json, SlowStream


def fetch(base='https://relay.test', key='KEY_CANARY'):
    return asyncio.run(rm.fetch(base, key, protocol='gemini'))


def test_review_gemini_pins_ip_preserves_tls_and_sends_key_only_in_header(monkeypatch):
    seen = _relay(monkeypatch, lambda request: _json({'models': [{'name':'models/banana-test'}]}))
    assert fetch() == ['banana-test']
    request = seen[0]
    assert str(request.url) == 'https://93.184.216.34/v1beta/models'
    assert request.headers['host'] == 'relay.test'
    assert request.extensions['sni_hostname'] == 'relay.test'
    assert request.headers['x-goog-api-key'] == 'KEY_CANARY'
    assert 'authorization' not in request.headers
    assert 'KEY_CANARY' not in str(request.url)


def test_review_gemini_rejects_mixed_public_private_dns_before_request(monkeypatch):
    seen = _relay(monkeypatch, lambda request: _json({'models': []}), resolve=['93.184.216.34', '127.0.0.1'])
    with pytest.raises(rm.RelayModelsError, match='内网'): fetch()
    assert seen == []


@pytest.mark.parametrize('case', ['compressed', 'oversize', 'drip', 'redirect'])
def test_review_gemini_keeps_body_and_time_limits(monkeypatch, case):
    monkeypatch.setattr(rm, 'MAX_BYTES', 32)
    monkeypatch.setattr(rm, 'TOTAL_SECONDS', .15)
    if case == 'compressed':
        response = httpx.Response(200, headers={'content-encoding':'gzip'}, stream=SlowStream([b'not-read'], 0))
    elif case == 'oversize':
        response = httpx.Response(200, stream=SlowStream([b'x'*16, b'x'*17], 0))
    elif case == 'drip':
        response = httpx.Response(200, stream=SlowStream([b' ']*10, .05))
    else:
        response = httpx.Response(302, headers={'location':'http://127.0.0.1/private'})
    seen = _relay(monkeypatch, lambda request: response)
    with pytest.raises(rm.RelayModelsError): fetch()
    assert len(seen) == 1 and rm._active == 0


def test_review_model_listing_binds_server_credentials_and_rejects_cross_site(monkeypatch):
    monkeypatch.setitem(settings.SETTINGS, 'custom_model_routes', [{'id':'bound','base_url':'https://bound.invalid/v1/chat/completions','api_key':'SERVER_ONLY_KEY'}])
    calls = []
    async def fake(base, key, *, protocol):
        calls.append((base, key, protocol))
        return ['banana-test']
    monkeypatch.setattr(rm, 'fetch', fake)
    app = FastAPI(); app.include_router(settings.router)
    with TestClient(app) as client:
        body={'provider':'gemini','route_id':'bound','base_url':'http://127.0.0.1/evil','api_key':'SPOOFED_KEY'}
        denied=client.post('/api/image-models',headers={'X-AH-Request':'1','Origin':'https://evil.invalid'},json=body)
        assert denied.status_code == 403 and calls == []
        accepted=client.post('/api/image-models',headers={'X-AH-Request':'1'},json=body)
        assert accepted.status_code == 200 and accepted.json() == {'models':['banana-test']}
        assert 'SERVER_ONLY_KEY' not in accepted.text
        assert calls == [('https://bound.invalid','SERVER_ONLY_KEY','gemini')]
        calls.clear()
        body['route_id']='deleted'
        assert client.post('/api/image-models',headers={'X-AH-Request':'1'},json=body).status_code == 404
        assert calls == []
