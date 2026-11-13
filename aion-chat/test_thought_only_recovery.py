import asyncio
import ai_providers as ap
from stream_safety import StreamActivity


def test_thought_only_reply_recovers_once_without_exposing_thought_or_mutating_history(monkeypatch):
    calls=[]; original=[{'role':'user','content':'draw a flower'}]; meta={}
    async def once(messages,cfg,meta,*args,**kwargs):
        calls.append(messages)
        if len(calls)==1:
            meta['reasoning_content']='PRIVATE_UNFINISHED_CODE'*4000
            yield StreamActivity()
            yield '  '
        else:
            assert 'PRIVATE_UNFINISHED_CODE' not in str(messages)
            yield '```html\n<html><body>flower</body></html>\n```'
    monkeypatch.setattr(ap,'_call_custom_openai_once',once)
    async def run(): return [x async for x in ap.call_custom_openai(original,{},meta)]
    out=asyncio.run(run())
    assert len(calls)==2 and len(original)==1
    assert any('flower</body>' in x for x in out)
    assert 'reasoning_content' not in meta and not meta.get('provider_error')


def test_repeated_thought_only_reply_is_bounded_and_reports_failure(monkeypatch):
    calls=[]; meta={}
    async def once(*args,**kwargs):
        calls.append(1);meta['reasoning_content']='unfinished';yield StreamActivity()
    monkeypatch.setattr(ap,'_call_custom_openai_once',once)
    async def run(): return [x async for x in ap.call_custom_openai([],{},meta)]
    out=asyncio.run(run())
    assert len(calls)==2 and meta['provider_error']=='empty_visible_response'
    assert '没有交付正文' in out[-1]


def test_visible_reply_is_never_replayed(monkeypatch):
    calls=[]
    async def once(*args,**kwargs): calls.append(1);yield 'hello'
    monkeypatch.setattr(ap,'_call_custom_openai_once',once)
    async def run(): return [x async for x in ap.call_custom_openai([],{}, {})]
    assert asyncio.run(run())==['hello'] and len(calls)==1
