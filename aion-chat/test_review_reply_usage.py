"""Independent real stream-entry probes; temporary usage DB and synthetic SSE."""
import asyncio
import json
from pathlib import Path
import subprocess

import httpx
import pytest

import ai_providers as provider
import reply_usage
import usage_log

REAL_CLIENT=httpx.AsyncClient


class Stream(httpx.AsyncByteStream):
    def __init__(self,data,fail_after=False):self.data=data;self.fail_after=fail_after
    async def __aiter__(self):
        yield self.data
        if self.fail_after:raise httpx.ReadError('synthetic transport failure')


def response(*,reasoning=False,usage=True,fail_after=False):
    data={'choices':[{'delta':{'reasoning_content':'synthetic thought'} if reasoning else {'content':'answer'}}]}
    if usage:
        data['usage']={'prompt_tokens':100 if reasoning else 50,'completion_tokens':2,
                       'prompt_tokens_details':{'cached_tokens':60 if reasoning else 20}}
    body=('data: '+json.dumps(data)+'\n\n'+('' if fail_after else 'data: [DONE]\n\n')).encode()
    return httpx.Response(200,stream=Stream(body,fail_after))


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(usage_log,'USAGE_DB',tmp_path/'usage.db')
    monkeypatch.setitem(provider.MODELS,'review-receipt',{'provider':'custom_openai','model':'synthetic',
        'base_url':'https://reply-review.invalid/v1','api_key':'synthetic','cache_marker':False})
    monkeypatch.setattr(provider,'RELAY_RETRY_DELAY',0)
    return monkeypatch


@pytest.mark.parametrize('first_has_usage,first_fails',[(True,False),(False,False),(True,True)])
def test_internal_provider_retry_cannot_be_shown_as_one_complete_call(setup,first_has_usage,first_fails):
    attempts=[]
    def handler(request):
        attempts.append(json.loads(request.content))
        return response(reasoning=len(attempts)==1,usage=first_has_usage if len(attempts)==1 else True,
                        fail_after=first_fails and len(attempts)==1)
    setup.setattr(provider.httpx,'AsyncClient',lambda **kw:REAL_CLIENT(transport=httpx.MockTransport(handler),**kw))
    meta={}
    async def run():
        return [p async for p in provider.stream_ai([{'role':'user','content':'synthetic request'}],
            'review-receipt',meta,include_device_context=False)]
    asyncio.run(run())
    assert len(attempts)==2
    receipt=reply_usage.attachments(meta)[0]
    assert receipt['calls']==2, 'internal second provider request is hidden behind one stream wrapper'
    if receipt['reported_calls']==receipt['calls'] and receipt['cache_reported_calls']==receipt['calls']:
        assert first_has_usage
        assert receipt['input_tokens']==150 and receipt['cache_read_tokens']==80


def test_concurrent_replies_keep_receipts_separate(setup):
    async def synthetic(messages,key,meta,**kw):
        value=int(messages[0]['content'])
        await asyncio.sleep(0)
        meta.update(prompt_tokens=value,completion_tokens=1,raw={'cached_tokens':value//2})
        yield 'answer'
    setup.setattr(provider,'_stream_ai',synthetic)
    async def run():
        async def single(value):
            meta={}
            async for _ in provider.stream_ai([{'role':'user','content':str(value)}],'review-receipt',meta):pass
            return reply_usage.attachments(meta)[0]
        return await asyncio.gather(single(100),single(200))
    receipts=asyncio.run(run())
    assert [r['input_tokens'] for r in receipts]==[100,200]
    assert [r['cache_read_tokens'] for r in receipts]==[50,100]


@pytest.mark.parametrize('cache_write',[None,'invalid',-1])
def test_partial_cache_metadata_cannot_be_marked_complete(cache_write):
    meta={'prompt_tokens':100,'completion_tokens':5,'raw':{'input_tokens':100,
        'cache_read_input_tokens':20}}
    if cache_write is not None:meta['raw']['cache_creation_input_tokens']=cache_write
    reply_usage.observe(meta,True)
    receipt=reply_usage.attachments(meta)[0]
    # Anthropic's uncached input excludes cache-write tokens; missing write usage
    # means total input (the denominator) is not known even though cache read is.
    assert receipt['cache_reported_calls']<receipt['calls']


def test_invalid_large_provider_counter_does_not_break_chat_finalization():
    meta={'prompt_tokens':10**400,'raw':{'cached_tokens':0}}
    reply_usage.observe(meta,True)
    assert reply_usage.attachments(meta)[0]['reported_calls']==0


def test_codex_normalization_does_not_invent_missing_cache_usage():
    meta = {}
    usage = provider._normalize_codex_app_server_usage({'inputTokens': 100, 'outputTokens': 2})
    provider._apply_codex_usage_meta(meta, usage)
    reply_usage.observe(meta, True)
    receipt = reply_usage.attachments(meta)[0]
    assert receipt['cache_reported_calls'] < receipt['calls']


def test_missing_output_count_is_not_presented_as_reported_zero():
    meta={'prompt_tokens':100,'raw':{'cached_tokens':60}}
    reply_usage.observe(meta,True)
    message={'attachments':reply_usage.attachments(meta)}
    script="""const fs=require('fs'),vm=require('vm');const c={window:{}};
vm.runInNewContext(fs.readFileSync('static/reply-usage.js','utf8'),c);
process.stdout.write(c.window.ReplyUsage.render(JSON.parse(fs.readFileSync(0,'utf8'))));"""
    result=subprocess.run(['node','-e',script],input=json.dumps(message),capture_output=True,
                          text=True,encoding='utf-8',cwd=Path(__file__).parent,check=True)
    assert '输出 0' not in result.stdout,'missing completion usage is currently invented as zero'


def test_receipt_roundtrips_with_existing_attachment(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    import aiosqlite
    import routes.chatroom as route

    database = tmp_path / 'isolated-chat.db'

    @asynccontextmanager
    async def db():
        async with aiosqlite.connect(database) as connection:
            yield connection

    async def unchanged(*args):
        return args[-1]

    monkeypatch.setattr(route, 'get_db', db)
    monkeypatch.setattr(route, '_with_link_previews', unchanged)
    monkeypatch.setattr(route, 'with_band_vibration_attachment', unchanged)
    monkeypatch.setattr(route, 'broadcast_synced', AsyncMock())
    monkeypatch.setattr(route, 'current_generation', lambda: None)
    meta = {'prompt_tokens': 100, 'completion_tokens': 2,
            'raw': {'cached_tokens': 60, 'synthetic_private_key': 'must-not-persist'}}
    reply_usage.observe(meta, True)
    music = {'type': 'music', 'title': 'synthetic tune', 'id': '123'}

    async def run():
        async with db() as connection:
            await connection.executescript('''
                CREATE TABLE chatroom_rooms (id TEXT PRIMARY KEY, type TEXT, updated_at REAL);
                CREATE TABLE chatroom_messages (id TEXT PRIMARY KEY, room_id TEXT, sender TEXT,
                    content TEXT, attachments TEXT, reasoning_content TEXT, created_at REAL);
                INSERT INTO chatroom_rooms VALUES ('isolated', 'seat_1v1', 0);
            ''')
            await connection.commit()
        saved = await route._save_msg('isolated', 'lucien', 'synthetic reply',
            msg_id='isolated-msg', attachments=[music], usage_meta=meta, auto_tts=False)
        _, loaded = await route._load_room_and_messages('isolated')
        assert loaded[0]['attachments'] == saved['attachments'] == [music] + reply_usage.attachments(meta)
        assert 'must-not-persist' not in json.dumps(loaded)

    asyncio.run(run())
