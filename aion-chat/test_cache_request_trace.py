import asyncio
import json

import httpx

import ai_providers as provider
import cache_request_trace as trace
import usage_log

REAL_CLIENT = httpx.AsyncClient


def test_shape_preserves_payload_and_detects_fixed_vs_live_changes():
    payload = {'model': 'synthetic', 'messages': [
        {'role': 'system', 'content': 'private-persona'},
        {'role': 'user', 'content': '[历史记录说明] synthetic'},
        {'role': 'user', 'content': 'private-history'},
        {'role': 'user', 'content': 'private-live-A'},
    ]}
    before = json.dumps(payload)
    first = trace.payload_shape(payload)
    assert json.dumps(payload) == before
    payload['messages'][-1]['content'] = 'private-live-B'
    second = trace.payload_shape(payload)
    assert first['fixed_id'] == second['fixed_id']
    assert first['message_ids'][:-1] == second['message_ids'][:-1]
    assert first['message_ids'][-1] != second['message_ids'][-1]
    payload['messages'][0]['content'] = 'different-persona'
    assert trace.payload_shape(payload)['fixed_id'] != first['fixed_id']
    assert 'private-' not in json.dumps(first)
    assert trace.payload_shape({'messages': []})['fixed_id'] is None


def test_numeric_whitelist_does_not_copy_untrusted_usage():
    raw = {'prompt_tokens': 100, 'cached_tokens': 0, 'api_key': 'secret',
           'cache_creation_input_tokens': True, 'input_tokens': -1,
           'prompt_tokens_details': {'cached_tokens': 20, 'secret': 'private'},
           'output_tokens': '100', 'total_tokens': 10**13}
    assert trace.numeric_usage(raw) == {
        'prompt_tokens': 100, 'cached_tokens': 0, 'prompt_tokens_details.cached_tokens': 20}


def test_trace_reports_same_route_prefix_without_storing_conversation_id(monkeypatch):
    monkeypatch.setattr(trace, '_PREVIOUS', {})
    payload = {'model': 'synthetic', 'messages': [
        {'role': 'system', 'content': 'private-persona'},
        {'role': 'user', 'content': '[历史记录说明] stable'},
        {'role': 'user', 'content': 'latest'},
    ]}
    first, second = {}, {}
    for meta in (first, second):
        trace.seed(meta, scope='private', identifier='real-conversation-id', context_limit=10)
        trace.bind_route(meta, provider='codex_cli', model='synthetic')
        trace.start(meta, payload)
    debug = second['_cache_request_shape']
    assert debug['same_fixed_prefix'] is True
    assert debug['shared_prefix_messages'] == 3
    assert debug['context_limit'] == 10
    assert 'real-conversation-id' not in json.dumps(debug)
    assert debug['request_bytes'] > 0 and debug['approx_tokens'] > 0


def test_codex_relay_and_route_switch_keep_independent_prefix_history(monkeypatch):
    monkeypatch.setattr(trace, '_PREVIOUS', {})

    def one(provider_name, latest):
        meta = {}
        trace.seed(meta, scope='private', identifier='same-chat', context_limit=10)
        trace.bind_route(meta, provider=provider_name, model='same-model')
        trace.start(meta, {'model': 'same-model', 'messages': [
            {'role': 'system', 'content': 'stable-persona'},
            {'role': 'user', 'content': '[历史记录说明] stable-history'},
            {'role': 'user', 'content': latest},
        ]})
        return meta['_cache_request_shape']

    codex = [one('codex_cli', f'codex-{index}') for index in range(5)]
    relay = [one('custom_openai', f'relay-{index}') for index in range(5)]
    codex_again = one('codex_cli', 'codex-return')
    assert codex[0]['same_fixed_prefix'] is False
    assert all(item['same_fixed_prefix'] is True for item in codex[1:])
    assert relay[0]['same_fixed_prefix'] is False
    assert all(item['same_fixed_prefix'] is True for item in relay[1:])
    assert codex_again['same_fixed_prefix'] is True


def test_real_stream_records_accepted_payload_after_fallback(tmp_path, monkeypatch):
    cfg = {'provider': 'custom_openai', 'base_url': 'https://trace.invalid/v1',
           'model': 'synthetic-trace', 'api_key': 'synthetic-secret', 'cache_marker': True}
    monkeypatch.setitem(provider.MODELS, 'trace-test', cfg)
    monkeypatch.setattr(provider, '_CACHE_OBSERVED', {})
    monkeypatch.setattr(provider, '_NO_LONG_CACHE', set())
    monkeypatch.setattr(provider, '_NO_CACHE_MARKER', set())
    monkeypatch.setattr(provider, '_NO_STREAM_USAGE', set())
    monkeypatch.setattr(usage_log, 'USAGE_DB', tmp_path / 'usage.db')
    monkeypatch.setattr(provider, 'with_current_device_context', lambda m: m)
    payloads = []
    def handle(request):
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(400, text='cache_control is unsupported')
        return httpx.Response(200, text='data: ' + json.dumps({
            'choices': [{'delta': {'content': 'synthetic'}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 1,
                      'prompt_tokens_details': {'cached_tokens': 0}, 'secret': 'do-not-retain'},
        }) + '\n\ndata: [DONE]\n\n')
    monkeypatch.setattr(provider.httpx, 'AsyncClient',
                        lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handle), **kw))
    async def run():
        messages = [
            {'role': 'system', 'content': 'synthetic-persona'},
            {'role': 'user', 'content': '[历史记录说明] synthetic', '_context': 'history'},
            {'role': 'user', 'content': '[当前时间] synthetic', '_context': 'live'},
            {'role': 'user', 'content': 'synthetic-current'},
        ]
        for _ in range(10):
            async for _ in provider.stream_ai(messages, 'trace-test', {}):
                pass
    asyncio.run(run())
    recent = provider.relay_cache_diagnostic(cfg)['recent']
    assert len(recent) == 8
    assert recent[-1]['fixed_id'] == trace.payload_shape(payloads[-1])['fixed_id']
    assert recent[-1]['marked_messages'] == []
    assert recent[-1]['usage']['prompt_tokens_details.cached_tokens'] == 0
    output = json.dumps(recent)
    for secret in ('synthetic-persona', 'synthetic-secret', 'trace.invalid', 'do-not-retain'):
        assert secret not in output
