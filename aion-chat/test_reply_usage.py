import asyncio

import pytest

import ai_providers
import reply_usage
import usage_log


@pytest.mark.parametrize('raw,prompt,total,read', [
    ({'prompt_tokens_details': {'cached_tokens': 60}}, 100, 100, 60),
    ({'input_tokens_details': {'cached_tokens': 0}}, 100, 100, 0),
    ({'prompt_cache_hit_tokens': 70}, 100, 100, 70),
    ({'cachedContentTokenCount': 80}, 100, 100, 80),
    ({'input_tokens': 20, 'cache_read_input_tokens': 70, 'cache_creation_input_tokens': 10}, 20, 100, 70),
])
def test_provider_formats_use_total_input_including_cache(raw, prompt, total, read):
    meta = {'prompt_tokens': prompt, 'completion_tokens': 2, 'raw': raw}
    reply_usage.observe(meta, True)
    receipt = reply_usage.attachments(meta)[0]
    assert receipt['input_tokens'] == total
    assert receipt['cache_read_tokens'] == read
    assert receipt['cache_reported_calls'] == 1
    assert 'raw' not in receipt


@pytest.mark.parametrize('raw', [{}, {'cached_tokens': None}, {'cached_tokens': -1}, {'cached_tokens': 101}])
def test_missing_or_invalid_cache_is_not_a_reported_zero(raw):
    meta = {'prompt_tokens': 100, 'raw': raw}
    reply_usage.observe(meta, True)
    assert reply_usage.attachments(meta)[0]['cache_reported_calls'] == 0


def test_real_wrapper_aggregates_and_does_not_reuse_stale_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_log, 'USAGE_DB', tmp_path/'usage.db')
    monkeypatch.setitem(ai_providers.MODELS, 'test-receipt', {'provider': 'custom_openai', 'model': 'test'})
    responses = iter([
        {'prompt_tokens': 100, 'completion_tokens': 2, 'raw': {'cached_tokens': 60}},
        {'prompt_tokens': 50, 'completion_tokens': 3, 'raw': {'cached_tokens': 20}},
        {},
    ])

    async def provider(messages, key, meta, *a, **kw):
        meta.update(next(responses))
        yield 'ok'

    monkeypatch.setattr(ai_providers, '_stream_ai', provider)
    meta = {}

    async def run():
        for _ in range(3):
            assert ''.join([s async for s in ai_providers.stream_ai([], 'test-receipt', meta)]) == 'ok'
    asyncio.run(run())
    receipt = reply_usage.attachments(meta)[0]
    assert (receipt['calls'], receipt['reported_calls'], receipt['cache_reported_calls']) == (3, 2, 2)
    assert (receipt['input_tokens'], receipt['cache_read_tokens'], receipt['output_tokens']) == (150, 80, 5)
    assert reply_usage.attachments({}) == []  # Old replies are not backfilled with invented numbers.
