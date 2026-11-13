"""Independent review: rejected settings requests must not switch image credentials."""
import asyncio
import copy

import pytest
from fastapi import HTTPException

from routes import settings


def test_review_invalid_other_field_leaves_image_binding_unchanged(monkeypatch):
    initial = {
        'image_gen_provider': 'code',
        'image_gen_profiles': {'gemini': {'base_url': '', 'api_key': 'OLD_KEY', 'model': ''}},
        'custom_model_routes': [{'id': 'bound', 'base_url': 'https://bound.invalid/v1', 'api_key': 'BOUND_KEY'}],
    }
    for key, value in initial.items(): monkeypatch.setitem(settings.SETTINGS, key, copy.deepcopy(value))
    before = {key: copy.deepcopy(settings.SETTINGS[key]) for key in initial}
    body = settings.SettingsUpdate(
        image_gen_provider='auto',
        image_gen_profiles={'gemini': {'route_id': 'bound', 'model': 'banana-test'}},
        minimax_tts_model='INVALID_REVIEW_MODEL',
    )
    with pytest.raises(HTTPException) as caught:
        asyncio.run(settings.update_settings(body))
    assert caught.value.status_code == 400
    assert {key: settings.SETTINGS[key] for key in initial} == before
