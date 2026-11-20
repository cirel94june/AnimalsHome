"""Independent synthetic relay counterexamples; no real account or model calls."""
import asyncio
import json

import httpx
import pytest
import ai_providers as providers
from unittest.mock import AsyncMock

REAL_CLIENT=httpx.AsyncClient
MESSAGES=[{'role':'user','content':'stable persona'},
          {'role':'assistant','content':'ack'},
          {'role':'user','content':'系统当前的准确时间是 2026-11-20 10:00'},
          {'role':'user','content':'hello'}]
URL='https://review-isolation.invalid/v1'


class Stream(httpx.AsyncByteStream):
    def __init__(self,body):self.body=body
    async def __aiter__(self):yield self.body


def response(status=200,error=''):
    if status!=200:return httpx.Response(status,stream=Stream(json.dumps({'error':error}).encode()))
    data={'choices':[{'delta':{'content':'ok'}}],
          'usage':{'prompt_tokens':2000,'completion_tokens':1,'total_tokens':2001,
                   'prompt_tokens_details':{'cached_tokens':1800}}}
    return httpx.Response(200,stream=Stream(('data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n').encode()))


@pytest.fixture(autouse=True)
def clear_fallbacks():
    names=['_NO_CACHE_MARKER','_NO_LONG_CACHE','_NO_STREAM_USAGE','_NO_GEMINI_THOUGHTS']
    for name in names:getattr(providers,name).clear()
    yield
    for name in names:getattr(providers,name).clear()


def run(monkeypatch,handler,model,api_key='synthetic-shared'):
    monkeypatch.setattr(providers.httpx,'AsyncClient',
        lambda **kw:REAL_CLIENT(transport=httpx.MockTransport(handler),**kw))
    async def consume():
        meta={}
        text=''.join([c async for c in providers.call_custom_openai(MESSAGES,
            {'base_url':URL,'model':model,'api_key':api_key},meta) if isinstance(c,str)])
        assert text=='ok'
        return meta
    return asyncio.run(consume())


def controls(body):
    return [p['cache_control'] for m in body['messages'] for p in
            (m['content'] if isinstance(m['content'],list) else []) if 'cache_control' in p]


@pytest.mark.parametrize('isolation',['model','account'])
def test_marker_rejection_must_not_disable_another_model_or_account(monkeypatch,isolation):
    seen=[]
    def handler(request):
        body=json.loads(request.content);marked=bool(controls(body))
        bad=(body['model']=='reject-marker' if isolation=='model' else
             request.headers.get('Authorization')=='Bearer synthetic-reject')
        seen.append((bad,marked))
        return response(400,'unknown field cache_control') if bad and marked else response()
    run(monkeypatch,handler,'reject-marker' if isolation=='model' else 'same-model',
        'synthetic-shared' if isolation=='model' else 'synthetic-reject')
    run(monkeypatch,handler,'supports-marker' if isolation=='model' else 'same-model',
        'synthetic-shared' if isolation=='model' else 'synthetic-accept')
    assert seen[-1]==(False,True),'one incompatible relay model/account disabled caching for another'


def test_ttl_rejection_must_not_disable_another_models_hour_cache(monkeypatch):
    seen=[]
    def handler(request):
        body=json.loads(request.content);marks=controls(body)
        long=any(c.get('ttl')=='1h' for c in marks)
        seen.append((body['model'],long))
        return response(400,'ttl not supported') if body['model']=='five-minutes-only' and long else response()
    run(monkeypatch,handler,'five-minutes-only')
    run(monkeypatch,handler,'hour-cache-supported')
    assert seen[-1]==('hour-cache-supported',True)


def test_usage_rejection_must_not_hide_another_models_reported_cache(monkeypatch):
    seen=[]
    def handler(request):
        body=json.loads(request.content);asked=body.get('stream_options',{}).get('include_usage') is True
        seen.append((body['model'],asked))
        if body['model']=='no-stream-usage' and asked:return response(400,'stream_options not supported')
        return response()
    run(monkeypatch,handler,'no-stream-usage')
    run(monkeypatch,handler,'usage-supported')
    assert seen[-1]==('usage-supported',True),'one model suppressed usage requests for other models'


def synthetic_seat(monkeypatch,group,*,balance=None,history_messages=None,context_limit=30,
                   chat_id='review-synthetic-context',provider='custom_openai',cache_marker=True):
    import seat_chat
    import capabilities
    monkeypatch.setattr(seat_chat.actors,'get_actor',lambda actor:{'name':'synthetic-ai','model':'synthetic-cache'})
    monkeypatch.setattr(seat_chat,'MODELS',{'synthetic-cache':{'provider':provider,'cache_marker':cache_marker}})
    monkeypatch.setattr(seat_chat,'load_worldbook',lambda:{'user_name':'synthetic-user'})
    monkeypatch.setattr(seat_chat,'_persona_text',lambda actor:'synthetic-stable-persona')
    monkeypatch.setattr(seat_chat.memory_hub_bridge,'context_block',AsyncMock(return_value='synthetic-memory'))
    monkeypatch.setattr(seat_chat,'_ability_lines',lambda *args:['[TOOL] synthetic stable instruction'])
    monkeypatch.setattr(seat_chat,'proactive_line',lambda actor:'')
    monkeypatch.setattr(seat_chat,'location_line',lambda:'')
    monkeypatch.setattr(seat_chat,'group_privacy_rule',lambda name:'synthetic-privacy-rule')
    monkeypatch.setattr(seat_chat.group_mentions,'hint_for',lambda actor:'')
    monkeypatch.setattr(capabilities,'is_capability_enabled',lambda name:name=='transfer' and balance is not None)
    if balance is not None:
        from routes import seat_wallet
        monkeypatch.setattr(seat_wallet,'get_seat_balance',AsyncMock(return_value=balance))
    msgs=history_messages if history_messages is not None else [{'sender':'user','content':'synthetic-stable-history','created_at':1},
          {'sender':'ai3','content':'synthetic-stable-reply','created_at':2},
          {'sender':'user','content':'synthetic-latest-question','created_at':3}]
    return asyncio.run(seat_chat.build_context('ai3',msgs,group=group,context_limit=context_limit,chat_id=chat_id))


@pytest.mark.parametrize('group',[False,True])
def test_seat_context_allows_stable_abilities_and_history_before_live_background(monkeypatch,group):
    messages=synthetic_seat(monkeypatch,group)
    prepared=providers._cache_friendly_order(messages)
    text='\n'.join(providers._message_text(message) for message in prepared)
    assert text.index('synthetic-stable-history')<text.index('[当前时间]'), \
        'seat context lacks constructor tags, so live time/memory invalidate stable abilities and history'


def test_seat_cache_reorder_does_not_treat_wallet_balance_as_stable_instructions(monkeypatch):
    messages=synthetic_seat(monkeypatch,False,balance=12.34)
    prepared=providers._without_context_tags(providers._cache_friendly_order(messages))
    marked,_=providers._cache_breakpoint(prepared)
    prefix=[];last_marked=[]
    for message in marked:
        content=message.get('content')
        for part in content if isinstance(content,list) else [{'text':content}]:
            prefix.append(str(part.get('text') or ''))
            if part.get('cache_control'):last_marked=list(prefix)
    cache='\n'.join(last_marked)
    assert 'synthetic-stable-history' in cache
    assert '12.34' not in cache,'current wallet balance must stay in the live portion'


def test_seat_full_history_window_does_not_slide_the_prefix_on_every_reply(monkeypatch):
    msgs=[{'id':f'review-row-{n}','sender':'user' if n%2==0 else 'ai3',
           'content':f'synthetic-window-message-{n}','created_at':n+1,'source':'private'}
          for n in range(7)]
    first=synthetic_seat(monkeypatch,False,history_messages=msgs[:5],context_limit=5,chat_id='review-window')
    second=synthetic_seat(monkeypatch,False,history_messages=msgs,context_limit=5,chat_id='review-window')
    assert any('synthetic-window-message-0' in providers._message_text(m) for m in first)
    assert any('synthetic-window-message-0' in providers._message_text(m) for m in second), \
        'full seat history window drops its cached first message after each reply instead of batching eviction'


def test_reported_deepseek_cache_hit_field_is_not_lost_in_local_usage_accounting():
    import usage_log
    assert usage_log.cache_tokens({'prompt_tokens':2000,'prompt_cache_hit_tokens':1800,
                                   'prompt_cache_miss_tokens':200})==(1800,0)


@pytest.mark.parametrize('other_chat,other_group',[('review-other-chat',False),('review-isolated-window',True)])
def test_stable_window_bookmark_isolated_by_chat_and_group(monkeypatch,other_chat,other_group):
    import context_builder
    context_builder._WINDOW_STARTS.clear()
    msgs=[{'id':f'review-isolated-{n}','sender':'user' if n%2==0 else 'ai3',
           'content':f'synthetic-isolated-message-{n}','created_at':n+1,'source':'private'}
          for n in range(7)]
    synthetic_seat(monkeypatch,False,history_messages=msgs[:5],context_limit=5,chat_id='review-isolated-window')
    same=synthetic_seat(monkeypatch,False,history_messages=msgs,context_limit=5,chat_id='review-isolated-window')
    other=synthetic_seat(monkeypatch,other_group,history_messages=msgs,context_limit=5,chat_id=other_chat)
    assert any('synthetic-isolated-message-0' in providers._message_text(m) for m in same)
    assert not any('synthetic-isolated-message-0' in providers._message_text(m) for m in other)


@pytest.mark.parametrize('provider,cache_marker',[('custom_openai',False),('gemini',True)])
def test_non_cache_routes_keep_original_fixed_history_limit(monkeypatch,provider,cache_marker):
    import context_builder
    context_builder._WINDOW_STARTS.clear()
    msgs=[{'id':f'review-uncached-{n}','sender':'user' if n%2==0 else 'ai3',
           'content':f'synthetic-uncached-message-{n}','created_at':n+1,'source':'private'}
          for n in range(7)]
    options={'provider':provider,'cache_marker':cache_marker,'context_limit':5,'chat_id':'review-uncached'}
    synthetic_seat(monkeypatch,False,history_messages=msgs[:5],**options)
    second=synthetic_seat(monkeypatch,False,history_messages=msgs,**options)
    text='\n'.join(providers._message_text(m) for m in second)
    assert 'synthetic-uncached-message-0' not in text
    assert 'synthetic-uncached-message-1' not in text
    assert all(f'synthetic-uncached-message-{n}' in text for n in range(2,7))
