"""Independent review: a bound route must never borrow an unrelated global key."""
import asyncio
import base64

import pytest

import config
import image_gen
from image_profiles import available_modes


def test_review_bound_banana_route_with_deleted_key_does_not_borrow_global_key(monkeypatch):
    cfg = {
        'image_gen_provider': 'auto',
        'image_gen_profiles': {'gemini': {'route_id': 'bound', 'model': 'banana-test'}},
        'custom_model_routes': [{'id': 'bound', 'base_url': 'https://bound.invalid/v1', 'api_key': ''}],
        'gemini_key': 'UNRELATED_GLOBAL_KEY',
    }
    for key, value in cfg.items(): monkeypatch.setitem(config.SETTINGS, key, value)
    calls = []

    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {'candidates':[{'content':{'parts':[{'inlineData':{'mimeType':'image/png', 'data':base64.b64encode(b'fake').decode()}}]}}]}

    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    async def save(*args, **kwargs): return 'album/fake.png'
    monkeypatch.setattr(image_gen.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(image_gen, 'save_generated_image', save)
    assert asyncio.run(image_gen.generate_image('banana|a flower', source_identity='ai3')) is None
    assert calls == [], 'a bound relay must not receive UNRELATED_GLOBAL_KEY'
    assert 'banana' not in available_modes(config.SETTINGS)
