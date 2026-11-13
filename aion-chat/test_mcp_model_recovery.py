import asyncio
import sys
import types

import httpx
import pytest

import mcp_outings


@pytest.fixture
def relay(monkeypatch):
    usage = types.SimpleNamespace(route_for_model=lambda *a: {}, record_json=lambda *a, **k: None, messages_text=lambda *a: '')
    monkeypatch.setitem(sys.modules, 'usage_log', usage)
    monkeypatch.setattr(mcp_outings, '_endpoint', lambda key: ('http://relay.test/chat/completions', {}, 'gemini-test'))
    monkeypatch.setattr(mcp_outings, 'RELAY_RETRY_SECONDS', 0)
    replies, requests = [], []
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            requests.append(kwargs['json'])
            return replies.pop(0)
    monkeypatch.setattr(mcp_outings.httpx, 'AsyncClient', Client)
    return replies, requests


@pytest.mark.parametrize('first', [
    httpx.Response(466, json={'error': {'type': 'empty_response', 'code': 'empty_response'}}),
    httpx.Response(200, json={'choices': [{'message': {'content': ''}}]}),
    httpx.Response(503, json={'error': {'type': 'unavailable'}}),
])
def test_empty_or_temporary_model_failure_retries_the_model_only(relay, first):
    replies, requests = relay
    replies.extend([first, httpx.Response(200, json={'choices': [{'message': {'content': 'ready'}}]})])
    assert asyncio.run(mcp_outings.call_model('test', [{'role': 'user', 'content': 'hello'}]))['content'] == 'ready'
    assert len(requests) == 2 and requests[0] == requests[1]


def test_invalid_request_is_not_retried(relay):
    replies, requests = relay
    replies.append(httpx.Response(400, json={'error': {'type': 'invalid_request'}}))
    with pytest.raises(RuntimeError, match='模型线路拒绝了工具请求'):
        asyncio.run(mcp_outings.call_model('test', []))
    assert len(requests) == 1


def test_provider_signature_and_tool_call_metadata_are_preserved(relay):
    replies, _ = relay
    msg = {'content': '', 'extra_content': {'google': {'thought_signature': 'TEST_SIGNATURE'}},
           'reasoning_content': 'TEST_REASONING',
           'tool_calls': [{'id': 'test', 'type': 'function', 'function': {'name': 'list_games', 'arguments': '{}'},
                           'extra_content': {'google': {'thought_signature': 'TEST_CALL_SIGNATURE'}}}]}
    replies.append(httpx.Response(200, json={'choices': [{'message': msg}]}))
    result = asyncio.run(mcp_outings.call_model('test', []))
    assert result == msg
