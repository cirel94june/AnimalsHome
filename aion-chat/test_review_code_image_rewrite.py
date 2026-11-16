"""Independent review of bounded code-image rewrites and automatic protocol routing."""
import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

import ai_providers
import config
import image_gen
from text_only import TEXT_ONLY

PLAN=json.dumps({'width':256,'height':256,'background':'#ffffff','shapes':[{'type':'rect','x':5,'y':5,'w':50,'h':50,'fill':'#334455'}]})


@pytest.fixture
def code_env(monkeypatch):
    monkeypatch.setitem(config.MODELS,'TEST_OWN_MODEL',{'provider':'custom_openai','model':'TEST_MODEL','base_url':'https://model.invalid/v1','api_key':'TEST_KEY'})
    monkeypatch.setattr(image_gen,'_code_model',lambda actor:'TEST_OWN_MODEL')
    saved=AsyncMock(return_value='album/test.png')
    monkeypatch.setattr(image_gen,'save_generated_image',saved)
    return saved


@pytest.mark.parametrize('first',['UNTRUSTED_BAD_JSON',json.dumps({'shapes':[{'type':'unknown'}]})])
def test_review_bad_plan_rewrites_same_model_and_saves_once(code_env,monkeypatch,first):
    calls,closed=[],[]
    async def stream(messages,model,meta,**kwargs):
        calls.append((messages,model,TEXT_ONLY.get()))
        try: yield first if len(calls)==1 else PLAN
        finally: closed.append(True)
    monkeypatch.setattr(ai_providers,'stream_ai',stream)
    assert asyncio.run(image_gen._generate_code_image('flower',False,'ai3'))=='album/test.png'
    assert len(calls)==2 and closed==[True,True]
    assert all(model=='TEST_OWN_MODEL' and text_only for _,model,text_only in calls)
    assert first not in calls[1][0][0]['content']
    assert code_env.await_count==1 and code_env.await_args.kwargs['actor']=='ai3'
    assert TEXT_ONLY.get() is False


@pytest.mark.parametrize('case',['twice_bad','provider_error','timeout','oversize'])
def test_review_failure_rewrite_limits(code_env,monkeypatch,case):
    calls,closed=[],[]
    async def stream(messages,model,meta,**kwargs):
        calls.append(1)
        try:
            assert TEXT_ONLY.get()
            if case=='provider_error': meta['provider_error']='TEST_PROVIDER_ERROR'; yield PLAN
            elif case=='timeout': raise httpx.ReadTimeout('TEST_TIMEOUT')
            elif case=='oversize': yield 'x'*180001
            else: yield 'not JSON'
        finally: closed.append(True)
    monkeypatch.setattr(ai_providers,'stream_ai',stream)
    async def run():
        result=await image_gen._generate_code_image('flower',False,'ai3')
        return result,image_gen.failure_reason()
    result,reason=asyncio.run(run())
    assert result is None and len(calls)==(2 if case=='twice_bad' else 1)
    assert len(closed)==len(calls) and code_env.await_count==0
    if case=='timeout': assert '超时' in reason
    assert TEXT_ONLY.get() is False


def test_review_cancellation_during_rewrite_closes_stream_and_never_saves(code_env,monkeypatch):
    calls,closed=[],[]
    async def stream(*args,**kwargs):
        calls.append(1)
        try:
            if len(calls)==1: yield 'invalid'
            else: await asyncio.sleep(10); yield PLAN
        finally: closed.append(True)
    monkeypatch.setattr(ai_providers,'stream_ai',stream)
    async def run():
        task=asyncio.create_task(image_gen._generate_code_image('flower',False,'ai3'))
        for _ in range(100):
            if len(calls)==2: break
            await asyncio.sleep(.005)
        assert len(calls)==2
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(run())
    assert closed==[True,True] and code_env.await_count==0 and TEXT_ONLY.get() is False


def test_review_timeout_is_shared_across_attempts(code_env,monkeypatch):
    monkeypatch.setattr(image_gen,'IMAGE_GEN_TIMEOUT',.08)
    calls=[]
    async def stream(*args,**kwargs):
        calls.append(1)
        await asyncio.sleep(.02 if len(calls)==1 else .1)
        yield 'invalid' if len(calls)==1 else PLAN
    monkeypatch.setattr(ai_providers,'stream_ai',stream)
    async def run():
        value=await image_gen._generate_code_image('flower',False,'ai3')
        assert value is None and '超时' in image_gen.failure_reason()
    asyncio.run(run())
    assert code_env.await_count==0 and len(calls)<=2


@pytest.mark.parametrize('openai,banana,prompt,expected',[
    (True,True,'flower','openai'), (False,True,'flower','banana'),
    (False,False,'flower','code'), (True,True,'code|flower','code'),
    (True,False,'banana|flower',None),
])
def test_review_auto_routing_prefers_configured_images_and_respects_explicit(monkeypatch,openai,banana,prompt,expected):
    monkeypatch.setitem(config.SETTINGS,'image_gen_provider','auto')
    monkeypatch.setitem(config.SETTINGS,'gemini_key','')
    monkeypatch.setitem(config.SETTINGS,'image_gen_profiles',{
        'openai':{'base_url':'https://openai.invalid/v1','api_key':'OPENAI_KEY','model':'gpt-image-test'} if openai else {},
        'gemini':{'api_key':'BANANA_KEY'} if banana else {},
    })
    for key in ['image_gen_base_url','image_gen_api_key','image_gen_model']: monkeypatch.setitem(config.SETTINGS,key,'')
    ai=AsyncMock(return_value='openai'); code=AsyncMock(return_value='code')
    monkeypatch.setattr(image_gen,'_generate_openai_image',ai)
    monkeypatch.setattr(image_gen,'_generate_code_image',code)
    save=AsyncMock(return_value='banana'); monkeypatch.setattr(image_gen,'save_generated_image',save)
    class Client:
        def __init__(self,*args,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,url,**kwargs):
            return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'inlineData':{'mimeType':'image/png','data':'dGVzdA=='}}]}}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(image_gen.httpx,'AsyncClient',Client)
    assert asyncio.run(image_gen.generate_image(prompt,source_identity='ai3'))==expected
    assert ai.await_count==int(expected=='openai') and code.await_count==int(expected=='code')
    assert save.await_count==int(expected=='banana')
    assert config.SETTINGS['image_gen_provider']=='auto'


def test_review_auto_openai_failure_does_not_switch_protocols(monkeypatch):
    monkeypatch.setitem(config.SETTINGS,'image_gen_provider','auto')
    monkeypatch.setitem(config.SETTINGS,'image_gen_profiles',{'openai':{'base_url':'https://openai.invalid/v1','api_key':'KEY','model':'gpt-image-test'},'gemini':{'api_key':'BANANA_KEY'}})
    ai=AsyncMock(return_value=None); code=AsyncMock(side_effect=AssertionError('unexpected fallback'))
    monkeypatch.setattr(image_gen,'_generate_openai_image',ai)
    monkeypatch.setattr(image_gen,'_generate_code_image',code)
    assert asyncio.run(image_gen.generate_image('flower',source_identity='ai3')) is None
    assert ai.await_count==1 and code.await_count==0 and config.SETTINGS['image_gen_provider']=='auto'


def test_review_rewrite_stops_if_the_underlying_model_route_changed(code_env,monkeypatch):
    route_key='REVIEW_PINNED_CODE_ROUTE'
    original={'provider':'custom_openai','model':'MODEL_A','base_url':'https://a.invalid/v1','api_key':'KEY_A','cache_marker':False}
    changed={**original,'model':'MODEL_B','base_url':'https://b.invalid/v1','api_key':'KEY_B'}
    monkeypatch.setitem(config.MODELS,route_key,original)
    monkeypatch.setattr(image_gen,'_code_model',lambda actor:route_key)
    monkeypatch.setattr(ai_providers,'with_current_device_context',lambda messages:messages)
    seen=[]
    async def once(messages,cfg,*args,**kwargs):
        seen.append(cfg['model'])
        yield 'invalid' if len(seen)==1 else PLAN
        if len(seen)==1: monkeypatch.setitem(config.MODELS,route_key,changed)
    monkeypatch.setattr(ai_providers,'_call_custom_openai_once',once)
    result=asyncio.run(image_gen._generate_code_image('flower',False,'ai3'))
    assert result is None and seen==['MODEL_A'], 'the retry must not use a changed underlying model/endpoint/key'
    assert code_env.await_count==0
