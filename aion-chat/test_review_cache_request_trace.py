"""Independent accepted-request trace probes; synthetic relay only."""
import asyncio
import json

import httpx
import pytest

import ai_providers as provider
import cache_request_trace as trace
import usage_log

REAL_CLIENT=httpx.AsyncClient
MESSAGES=[{'role':'system','content':'synthetic persona'},
          {'role':'user','content':'[历史记录说明] synthetic','_context':'history'},
          {'role':'user','content':'[当前时间] synthetic','_context':'live'},
          {'role':'user','content':'synthetic request'}]


@pytest.fixture
def cfg(tmp_path,monkeypatch):
    cfg={'provider':'custom_openai','base_url':'https://trace-review.invalid/v1',
         'model':'synthetic','api_key':'synthetic-secret','cache_marker':True}
    monkeypatch.setitem(provider.MODELS,'review-trace',cfg)
    monkeypatch.setattr(provider,'with_current_device_context',lambda m:m)
    for name in ('_NO_LONG_CACHE','_NO_CACHE_MARKER','_NO_STREAM_USAGE','_NO_GEMINI_THOUGHTS','_STREAM_DROPS_THOUGHTS'):
        monkeypatch.setattr(provider,name,set())
    monkeypatch.setattr(provider,'_CACHE_OBSERVED',{})
    monkeypatch.setattr(provider,'RELAY_RETRY_DELAY',0)
    monkeypatch.setattr(usage_log,'USAGE_DB',tmp_path/'usage.db')
    return cfg


def consume():
    async def run():
        return ''.join(str(c) for c in [p async for p in provider.stream_ai(MESSAGES,'review-trace',{})]
                       if isinstance(c,str))
    return asyncio.run(run())


def transport(monkeypatch,handler):
    monkeypatch.setattr(provider.httpx,'AsyncClient',
        lambda **kw:REAL_CLIENT(transport=httpx.MockTransport(handler),**kw))


def response(reasoning=False,usage=True):
    data={'choices':[{'delta':{'reasoning_content':'synthetic thought'} if reasoning else {'content':'synthetic answer'}}]}
    if usage:data['usage']={'prompt_tokens':100,'completion_tokens':2,'prompt_tokens_details':{'cached_tokens':60}}
    return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')


def test_retry_payload_must_not_borrow_previous_attempt_usage(cfg,monkeypatch):
    payloads=[]
    def handle(request):
        payloads.append(json.loads(request.content))
        return response(reasoning=len(payloads)==1,usage=len(payloads)==1)
    transport(monkeypatch,handle)
    assert 'synthetic answer' in consume()
    assert len(payloads)==2
    recent=provider.relay_cache_diagnostic(cfg)['recent']
    assert recent[-1]['message_ids']==trace.payload_shape(payloads[-1])['message_ids']
    assert recent[-1]['usage']=={}, 'last accepted request without usage borrowed previous reasoning-only request counters'


@pytest.mark.parametrize('failure_at',['payload_shape','numeric_usage'])
def test_diagnostic_failure_must_not_break_reply(cfg,monkeypatch,failure_at):
    def fail(*args,**kwargs):raise RuntimeError('synthetic diagnostic failure')
    monkeypatch.setattr(trace,failure_at,fail)
    transport(monkeypatch,lambda request:response())
    assert 'synthetic answer' in consume()


def test_nonstream_gemini_records_actual_accepted_body(cfg,monkeypatch):
    cfg['model']='gemini-synthetic'
    key=('https://trace-review.invalid/v1/chat/completions',cfg['model'])
    provider._STREAM_DROPS_THOUGHTS.add(key)
    accepted=[]
    def handle(request):
        accepted.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'synthetic answer'}}],
                                       'usage':{'prompt_tokens':100,'completion_tokens':2,
                                                'prompt_tokens_details':{'cached_tokens':0}}})
    transport(monkeypatch,handle)
    assert 'synthetic answer' in consume()
    assert accepted[0]['stream'] is False
    last=provider.relay_cache_diagnostic(cfg)['recent'][-1]
    assert last['options_id']==trace.payload_shape(accepted[0])['options_id']
    assert last['usage']['prompt_tokens_details.cached_tokens']==0
