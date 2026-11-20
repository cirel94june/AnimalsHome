"""Synthetic capability-state cache review; no user database or model requests."""
import asyncio
from unittest.mock import AsyncMock

import ai_providers
import capabilities
import context_builder
import pytest


@pytest.mark.parametrize('state_kind',['wallet','learning'])
def test_wallet_balance_does_not_change_stable_capability_cache_prefix(monkeypatch,state_kind):
    import toy_profiles
    import svakom_ai
    import ankni_ai
    import self_sign
    import routes.connor_wallet as wallet
    import english_corner
    monkeypatch.setattr(toy_profiles, 'capture_permission', lambda **kw: None)
    monkeypatch.setattr(toy_profiles, 'active', lambda: '')
    monkeypatch.setattr(svakom_ai, 'capture_permission', lambda **kw: False)
    monkeypatch.setattr(ankni_ai, 'capture_permission', lambda **kw: False)
    monkeypatch.setattr(self_sign, 'ability_text', lambda *a: '[SIGN] stable')
    monkeypatch.setattr(capabilities, 'is_capability_enabled', lambda key: key == ('transfer' if state_kind=='wallet' else 'english_corner_reminder'))
    monkeypatch.setattr(capabilities, 'taobao_chat_on', lambda: False)
    monkeypatch.setattr(capabilities, 'proactive_ability_text', lambda actor: '')
    monkeypatch.setattr(capabilities, 'autonomy_prompt_text', AsyncMock(return_value=''))
    monkeypatch.setattr(wallet, '_get_connor_balance', AsyncMock(side_effect=[40, 20]))
    monkeypatch.setattr(english_corner, 'get_unlearned_count', AsyncMock(side_effect=[2, 1]))
    monkeypatch.setattr(context_builder, 'is_capability_enabled', lambda key: False)
    monkeypatch.setattr(context_builder, 'get_device_context_for_prompt', lambda: '')
    monkeypatch.setattr(context_builder, 'build_cli_file_storage_text', lambda key: '')

    async def build():
        return await context_builder.build_ability_block('synthetic user', who='connor',
            excluded_capabilities={'post_sentinel'})

    def cached_prefix(block):
        messages = [
            {'role': 'user', 'content': 'stable persona'},
            {'role': 'assistant', 'content': 'ack'},
            {'role': 'user', 'content': block, '_context': 'abilities'},
            {'role': 'user', 'content': '[当前时间] synthetic', '_context': 'live'},
            {'role': 'user', 'content': '[历史记录说明] synthetic', '_context': 'history'},
            {'role': 'user', 'content': 'stable history'},
            {'role': 'user', 'content': 'latest question'},
        ]
        ordered = ai_providers._without_context_tags(ai_providers._cache_friendly_order(messages))
        marked, indices = ai_providers._cache_breakpoint(ordered)
        return '\n'.join(ai_providers._message_text(m) for m in marked[:max(indices)+1])

    first, second = asyncio.run(build()), asyncio.run(build())
    if state_kind=='wallet':assert '40.00' in first and '20.00' in second
    else:assert '2 张' in first and '1 张' in second
    assert cached_prefix(first) == cached_prefix(second), 'live wallet balance invalidates static ability/history prefix'
