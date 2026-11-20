import asyncio
from unittest.mock import AsyncMock

import pytest

import ai_providers
import capabilities
import context_builder


@pytest.mark.parametrize('kind', ['phone', 'patrol', 'reference'])
def test_device_changes_stay_after_cacheable_history(monkeypatch, kind):
    import app_supervision_ai as phone
    import patrol_control
    import toy_profiles
    import svakom_ai
    import ankni_ai
    import self_sign
    phase = ['A']
    enabled = {'phone': 'app_supervision', 'patrol': 'cam_check', 'reference': ''}[kind]
    monkeypatch.setattr(capabilities, 'is_capability_enabled', lambda k: k == enabled)
    monkeypatch.setattr(toy_profiles, 'capture_permission', lambda **kw: None)
    monkeypatch.setattr(toy_profiles, 'active', lambda: '')
    monkeypatch.setattr(svakom_ai, 'capture_permission', lambda **kw: kind == 'reference')
    monkeypatch.setattr(svakom_ai, 'reference_strength_prompt', lambda: 'reference-' + phase[0])
    monkeypatch.setattr(ankni_ai, 'capture_permission', lambda **kw: False)
    monkeypatch.setattr(self_sign, 'ability_text', lambda *a: 'synthetic-sign')
    monkeypatch.setattr(capabilities, 'taobao_chat_on', lambda: False)
    monkeypatch.setattr(capabilities, 'proactive_ability_text', lambda actor: '')
    monkeypatch.setattr(capabilities, 'autonomy_prompt_text', AsyncMock(return_value=''))
    monkeypatch.setattr(context_builder, 'is_capability_enabled', lambda k: False)
    monkeypatch.setattr(context_builder, 'get_device_context_for_prompt', lambda: '')
    monkeypatch.setattr(context_builder, 'build_cli_file_storage_text', lambda key: '')
    monkeypatch.setattr(patrol_control, 'status_text', lambda: 'patrol-' + phase[0])
    monkeypatch.setattr(phone, 'is_capability_enabled', lambda k: True)
    monkeypatch.setattr(phone, '_phone_online', lambda: True)
    cache = phone.AppSupervisionStateCache()
    monkeypatch.setattr(phone, 'state_cache', cache)

    async def build():
        return await context_builder.build_ability_block('synthetic', excluded_capabilities={'post_sentinel'})
    def ordered(block):
        return ai_providers._cache_friendly_order([
            {'role': 'user', 'content': 'persona'},
            {'role': 'user', 'content': block, '_context': 'abilities'},
            {'role': 'user', 'content': '[历史记录说明] synthetic', '_context': 'history'},
            {'role': 'user', 'content': 'old-history'},
            {'role': 'user', 'content': 'current'},
        ])
    cache.replace_snapshot({'groups': [{'groupId': 'synthetic', 'displayName': 'phone-A'}]})
    first = asyncio.run(build())
    phase[0] = 'B'
    cache.replace_snapshot({'groups': [{'groupId': 'synthetic', 'displayName': 'phone-B'}]})
    second = asyncio.run(build())
    assert first != second
    a, b = ordered(first), ordered(second)
    assert a[:-2] == b[:-2], 'device state rewrites the fixed prefix/history'
    assert a[-2] != b[-2]
    # Existing non-cache callers retain both instructions and current state.
    old_style = asyncio.run(capabilities.build_capability_prompt_items('synthetic'))
    target = {'phone': 'phone-B', 'patrol': 'patrol-B', 'reference': 'reference-B'}[kind]
    assert target in '\n'.join(old_style)
