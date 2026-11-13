"""Independent local review of thought-only recovery boundaries."""
import asyncio
import json

import httpx
import pytest

import ai_providers as ap
from stream_safety import StreamActivity
from test_relay_provider_errors import FakeStreamResponse, FakeAsyncClient


def test_review_actual_sse_recovery_does_not_replay_private_thoughts(monkeypatch):
    secret = 'PRIVATE_UNFINISHED_CODE [转账：20元] <script>untrusted()</script>'
    responses = [
        FakeStreamResponse(lines=['data: '+json.dumps({'choices':[{'delta':{'reasoning_content':secret}}]}), 'data: [DONE]']),
        FakeStreamResponse(lines=['data: '+json.dumps({'choices':[{'delta':{'content':'FINAL_DELIVERY'}}]}), 'data: [DONE]']),
    ]
    clients = []
    def factory(*args, **kwargs):
        client = FakeAsyncClient(responses.pop(0))
        clients.append(client)
        return client
    monkeypatch.setattr(ap.httpx, 'AsyncClient', factory)
    history = [{'role':'user', 'content':'deliver a webpage'}]
    meta = {}
    async def run():
        return [piece async for piece in ap.call_custom_openai(history, {'base_url':'https://relay.invalid/v1','model':'unit-model','cache_marker':False}, meta)]
    output = asyncio.run(run())
    assert len(clients) == 2
    assert secret not in repr(clients[1].calls)
    assert secret not in ''.join(output)
    assert output.count('FINAL_DELIVERY') == 1
    assert history == [{'role':'user', 'content':'deliver a webpage'}]
    assert 'reasoning_content' not in meta


def test_review_provider_error_is_not_recovered_as_an_empty_reply(monkeypatch):
    calls=[]
    async def once(messages,cfg,meta,*args,**kwargs):
        calls.append(1)
        meta.update(reasoning_content='PRIVATE', provider_error='HTTP 403')
        yield StreamActivity()
    monkeypatch.setattr(ap,'_call_custom_openai_once',once)
    async def run(): return [piece async for piece in ap.call_custom_openai([],{}, {})]
    asyncio.run(run())
    assert calls == [1]


def test_review_cancel_during_thoughts_never_starts_a_recovery(monkeypatch):
    calls, closed = [], []
    async def once(messages,cfg,meta,*args,**kwargs):
        calls.append(1)
        try:
            meta['reasoning_content']='PRIVATE'
            yield StreamActivity()
            await asyncio.sleep(10)
        finally: closed.append(True)
    monkeypatch.setattr(ap,'_call_custom_openai_once',once)
    async def run():
        async def collect(): return [piece async for piece in ap.call_custom_openai([],{}, {})]
        task=asyncio.create_task(collect())
        for _ in range(20):
            if calls: break
            await asyncio.sleep(.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(run())
    assert calls == [1] and closed == [True]


def test_review_network_retry_after_recovery_stays_within_three_attempts(monkeypatch):
    calls=[]
    async def once(messages,cfg,meta,*args,**kwargs):
        calls.append(messages)
        if len(calls)==1:
            meta['reasoning_content']='PRIVATE'
            yield StreamActivity()
        elif len(calls)==2:
            raise httpx.ReadError('transport failed before visible body')
        else:
            yield 'FINAL_ONCE'
    monkeypatch.setattr(ap,'_call_custom_openai_once',once)
    monkeypatch.setattr(ap,'RELAY_RETRY_DELAY',0)
    meta={}
    async def run(): return [piece async for piece in ap.call_custom_openai([],{},meta)]
    output=asyncio.run(run())
    assert len(calls)==3 and output.count('FINAL_ONCE')==1
    assert all('PRIVATE' not in repr(history) for history in calls)


def test_review_recovery_wholly_empty_second_attempt_reports_failure(monkeypatch):
    calls=[]
    async def once(messages,cfg,meta,*args,**kwargs):
        calls.append(1)
        if len(calls)==1: meta['reasoning_content']='PRIVATE'
        yield StreamActivity()
    monkeypatch.setattr(ap,'_call_custom_openai_once',once)
    meta={}
    async def run(): return [piece async for piece in ap.call_custom_openai([],{},meta)]
    output=asyncio.run(run())
    assert calls==[1,1] and meta['provider_error']=='empty_visible_response'
    assert '没有交付正文' in output[-1]
