"""Independent diagnostics probes using synthetic cfg/meta only."""
import asyncio
import hashlib
import json

import ai_providers as provider
import usage_log


def config(model='synthetic-a', key='synthetic-secret-a'):
    return {'provider':'custom_openai','base_url':'https://diagnostic-review.invalid/v1',
            'model':model,'api_key':key,'cache_marker':True}


def test_model_account_isolation_and_no_identifier_leak(monkeypatch):
    monkeypatch.setattr(provider, '_CACHE_OBSERVED', {})
    monkeypatch.setattr(provider, '_NO_CACHE_MARKER', set())
    cfg = config()
    provider._observe_relay_cache(cfg, {'prompt_tokens':100,'raw':{'cached_tokens':60}}, True)
    key = provider._relay_feature_key('https://diagnostic-review.invalid/v1/chat/completions',
                                      cfg['model'],cfg['api_key'])
    provider._NO_CACHE_MARKER.add(key)
    same = provider.relay_cache_diagnostic(cfg)
    assert same['calls'] == same['reported'] == same['hits'] == 1
    for other in (config('synthetic-b'),config(key='synthetic-secret-b')):
        diagnostic = provider.relay_cache_diagnostic(other)
        assert diagnostic['calls'] == 0 and not diagnostic['marker_rejected']
    output = json.dumps(same)
    for secret in ('diagnostic-review.invalid','synthetic-a','synthetic-secret-a',
                   hashlib.sha256(cfg['api_key'].encode()).hexdigest(),'cached_tokens'):
        assert secret not in output


def test_real_stream_pins_route_and_does_not_reobserve_old_meta(tmp_path, monkeypatch):
    monkeypatch.setattr(provider, '_CACHE_OBSERVED', {})
    monkeypatch.setattr(usage_log, 'USAGE_DB', tmp_path/'usage.db')
    original = config()
    replacement = config('synthetic-b','synthetic-secret-b')
    monkeypatch.setitem(provider.MODELS, 'review-diagnostic', original)
    async def synthetic(messages, model, meta, **kwargs):
        meta.update(prompt_tokens=100,completion_tokens=1,raw={'cached_tokens':60})
        provider.MODELS['review-diagnostic'] = replacement
        yield 'synthetic reply'
    monkeypatch.setattr(provider, '_stream_ai', synthetic)
    meta = {}
    async def run():
        async for _ in provider.stream_ai([], 'review-diagnostic', meta):pass
    asyncio.run(run())
    assert provider.relay_cache_diagnostic(original)['hits'] == 1
    assert provider.relay_cache_diagnostic(replacement)['calls'] == 0

    async def no_usage(*args, **kwargs):
        yield 'no usage reply'
    monkeypatch.setattr(provider, '_stream_ai', no_usage)
    asyncio.run(run())
    status = provider.relay_cache_diagnostic(replacement)
    assert (status['calls'],status['reported'],status['hits']) == (1,0,0)


def test_observation_bound_and_incomplete_anthropic_denominator(monkeypatch):
    monkeypatch.setattr(provider, '_CACHE_OBSERVED', {})
    for n in range(205):
        provider._observe_relay_cache(config(str(n)),{},False)
    assert len(provider._CACHE_OBSERVED) == 200
    cfg=config('partial')
    provider._observe_relay_cache(cfg,{'prompt_tokens':100,
        'raw':{'input_tokens':100,'cache_read_input_tokens':60}},True)
    status=provider.relay_cache_diagnostic(cfg)
    assert (status['calls'],status['reported'],status['hits']) == (1,0,0)
