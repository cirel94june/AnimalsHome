"""Independent synthetic counterexamples for pending usage/cache changes."""
import asyncio
import json
from pathlib import Path
import subprocess

import httpx
import pytest

import ai_providers as provider
import cache_request_trace as trace
import reply_usage
from test_review_cache_request_trace import cfg, MESSAGES, response, transport


def test_codex_third_snapshot_keeps_prior_cache_maximum():
    meta={}
    for cache in (60,0,0):
        provider._apply_codex_usage_meta(meta,{'input_tokens':100,'output_tokens':2,
                                             'cached_input_tokens':cache})
    assert meta['raw']['prompt_tokens_details']['cached_tokens']==60


def test_codex_sparse_snapshot_retains_reported_input():
    meta={}
    provider._apply_codex_usage_meta(meta,{'input_tokens':100,'output_tokens':2,'cached_input_tokens':60})
    provider._apply_codex_usage_meta(meta,{'output_tokens':3})
    assert meta.get('prompt_tokens')==100


def test_codex_sparse_snapshot_recomputes_derived_total():
    meta={}
    provider._apply_codex_usage_meta(meta,{'input_tokens':100,'output_tokens':2})
    provider._apply_codex_usage_meta(meta,{'input_tokens':100,'output_tokens':3})
    assert meta['total_tokens']==103


@pytest.mark.parametrize('cache',[None,-1,'invalid'])
def test_invalid_cache_is_not_normalized_to_reported_zero(cache):
    meta={}
    provider._apply_openai_compatible_usage_meta(meta,{'prompt_tokens':100,
        'prompt_tokens_details':{'cached_tokens':cache}})
    reply_usage.observe(meta,True)
    assert reply_usage.attachments(meta)[0]['cache_reported_calls']==0


def test_missing_completion_is_not_normalized_to_reported_zero():
    meta={}
    provider._apply_openai_compatible_usage_meta(meta,{'prompt_tokens':100,'cached_tokens':0})
    reply_usage.observe(meta,True)
    assert reply_usage.attachments(meta)[0]['output_reported_calls']==0


@pytest.mark.parametrize('write',[None,20])
def test_cache_write_does_not_establish_cache_read_zero(write):
    meta={'prompt_tokens':100,'raw':{'prompt_tokens':100,'cache_creation_input_tokens':write}}
    reply_usage.observe(meta,True)
    assert reply_usage.attachments(meta)[0]['cache_reported_calls']==0


@pytest.mark.parametrize('label',['写缓存 0','推理 0'])
def test_missing_new_usage_counters_are_not_presented_as_real_zero(label):
    meta={'prompt_tokens':100,'raw':{'cached_tokens':60}}
    reply_usage.observe(meta,True)
    script="""const fs=require('fs'),vm=require('vm');const c={window:{}};
vm.runInNewContext(fs.readFileSync('static/reply-usage.js','utf8'),c);
process.stdout.write(c.window.ReplyUsage.render(JSON.parse(fs.readFileSync(0,'utf8'))));"""
    result=subprocess.run(['node','-e',script],input=json.dumps({'attachments':reply_usage.attachments(meta)}),
        capture_output=True,text=True,encoding='utf-8',cwd=Path(__file__).parent,check=True)
    assert label not in result.stdout


def test_internal_requests_do_not_merge_cumulative_usage(cfg,monkeypatch):
    requests=[]
    def handle(request):
        requests.append(json.loads(request.content))
        first=len(requests)==1
        data={'choices':[{'delta':{'reasoning_content':'synthetic thought'} if first else {'content':'answer'}}],
              'usage':{'prompt_tokens':100 if first else 50,'completion_tokens':2,
                       'prompt_tokens_details':{'cached_tokens':60 if first else 20}}}
        return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
    transport(monkeypatch,handle)
    meta={}
    async def run():
        async for _ in provider.stream_ai(MESSAGES,'review-trace',meta):pass
    asyncio.run(run())
    assert len(requests)==2
    assert meta['prompt_tokens']==50, 'normalizer mixed usage from separate HTTP requests'


def test_trace_does_not_compare_different_upstream_accounts(cfg,monkeypatch):
    monkeypatch.setattr(trace,'_PREVIOUS',{})
    other={**cfg,'api_key':'synthetic-other-account'}
    monkeypatch.setitem(provider.MODELS,'review-trace-other',other)
    transport(monkeypatch,lambda request:response())
    async def run():
        for model in ('review-trace','review-trace-other'):
            meta={}
            trace.seed(meta,scope='private',identifier='synthetic-room',context_limit=30)
            async for _ in provider.stream_ai(MESSAGES,model,meta):pass
    asyncio.run(run())
    last=provider.relay_cache_diagnostic(other)['recent'][-1]
    assert last['same_fixed_prefix'] is False


def test_custom_reply_keeps_trace_for_persisted_debug_receipt(cfg,monkeypatch):
    transport(monkeypatch,lambda request:response())
    meta={}
    trace.seed(meta,scope='private',identifier='synthetic-room',context_limit=30)
    async def run():
        async for _ in provider.stream_ai(MESSAGES,'review-trace',meta):pass
    asyncio.run(run())
    assert 'cache_debug' in reply_usage.attachments(meta)[0]


def test_default_connor_cli_consumes_seed_and_reorders_context(monkeypatch):
    import routes.chatroom as routes
    import chatroom
    seen=[]
    monkeypatch.setattr(routes,'_resolve_connor_model',lambda *a:'Codex')
    monkeypatch.setattr(chatroom,'with_current_device_context',lambda m:m)
    monkeypatch.setitem(provider.MODELS,'Codex',{'provider':'codex_cli','model':'synthetic-codex'})
    async def fake_call(messages,model,meta):
        seen.extend(messages)
        yield 'answer'
    monkeypatch.setattr(chatroom,'call_codex_cli',fake_call)
    meta={}
    trace.seed(meta,scope='room',identifier='synthetic-room',context_limit=30)
    messages=[MESSAGES[0],MESSAGES[2],MESSAGES[1],MESSAGES[3]]
    async def run():
        async for _ in routes._stream_connor_model(messages,'Codex',meta):pass
    asyncio.run(run())
    assert '_cache_debug_seed' not in meta, 'default Codex bypasses the new bind_route entry'
    texts=[provider._message_text(m) for m in seen]
    assert texts.index('[历史记录说明] synthetic')<texts.index('[当前时间] synthetic')


def test_codex_debug_payload_remains_json_serializable():
    import routes.chatroom as routes
    meta={}
    provider._apply_codex_usage_meta(meta,{'input_tokens':100,'output_tokens':2,'cached_input_tokens':60})
    payload=routes._chatroom_debug_payload(room_id='synthetic-room',model_key='Codex',
        msg_id='synthetic-msg',history=[],digest_result={},usage_meta=meta)
    json.dumps(payload)  # Both SSE and ws.broadcast use the standard JSON encoder.


def test_direct_connor_followup_does_not_reuse_reported_field_presence(monkeypatch):
    import routes.chatroom as routes
    monkeypatch.setattr(routes,'_resolve_connor_model',lambda *a:'Codex')
    replies=iter([{'input_tokens':100,'output_tokens':2,'cached_input_tokens':60},{'output_tokens':3}])
    async def synthetic(*,messages,meta):
        provider._apply_codex_usage_meta(meta,next(replies))
        yield 'answer'
    monkeypatch.setattr(routes,'stream_connor_cli',synthetic)
    meta={}
    async def run():
        for _ in range(2):
            async for _ in routes._stream_connor_model(MESSAGES,'Codex',meta):pass
    asyncio.run(run())
    assert meta['_reply_usage']['reported_calls']==1


@pytest.mark.parametrize('file,function',[('chat.js','_buildTokenHtml'),('chatroom.js','crBuildTokenHtml')])
def test_raw_cache_counter_cannot_inject_html(file,function):
    script=r"""const fs=require('fs'),vm=require('vm');const c={};
const p=JSON.parse(fs.readFileSync(0,'utf8'));const s=fs.readFileSync('static/'+p.file,'utf8');
const a=s.indexOf('function '+p.function+'('),b=s.indexOf('\nfunction ',a+1);
vm.runInNewContext(s.slice(a,b),c);process.stdout.write(c[p.function]({raw:{prompt_tokens:100,cached_tokens:'<img src=x onerror=alert(1)>'}}));"""
    result=subprocess.run(['node','-e',script],input=json.dumps({'file':file,'function':function}),
        capture_output=True,text=True,encoding='utf-8',cwd=Path(__file__).parent,check=True)
    assert '<img ' not in result.stdout
