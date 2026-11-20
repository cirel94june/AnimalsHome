"""Final HTTP-payload probes: synthetic memory changes never access the Hub."""
import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

import ai_providers as provider
import chatroom
import context_builder
import seat_chat
import usage_log

REAL_CLIENT = httpx.AsyncClient


class Stream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"synthetic answer"}}]}\n\ndata: [DONE]\n\n'


@pytest.mark.parametrize('path', ['aion_private','aion_group','connor_private','seat'])
def test_hub_change_and_clock_preserve_actual_sent_prefix(path,tmp_path,monkeypatch):
    import capabilities
    import memory_hub_bridge
    import routes.chat as private
    phase = ['A']
    clock = lambda *a, **kw: 'synthetic-time-' + phase[0]
    hub = AsyncMock(side_effect=lambda *a, **kw: '[Hub synthetic-memory-' + phase[0] + ']')
    monkeypatch.setattr(memory_hub_bridge,'context_block',hub)
    monkeypatch.setattr(context_builder,'now_text',clock)
    monkeypatch.setattr(context_builder,'build_health_summary',AsyncMock(return_value=''))
    monkeypatch.setattr(context_builder,'_timeline_display_names',lambda:('SyntheticUser','SyntheticAion','SyntheticConnor'))
    rows=[{'id':str(n),'source':'private','sender':'user' if n%2==0 else 'connor',
           'content':f'synthetic-history-{n}','created_at':n+1,'attachments':[]} for n in range(3)]
    wb={'user_name':'SyntheticUser','ai_name':'SyntheticAion','ai_persona':'stable persona'}
    monkeypatch.setattr(chatroom,'load_worldbook',lambda:wb)
    monkeypatch.setattr(chatroom,'get_chatroom_names',lambda:('SyntheticUser','SyntheticAion','SyntheticConnor'))
    monkeypatch.setattr(chatroom,'_read_connor_persona',lambda:'stable persona')
    monkeypatch.setattr(chatroom,'fetch_merged_timeline',AsyncMock(return_value=rows))
    monkeypatch.setattr(chatroom,'build_ability_block',AsyncMock(return_value='[STATIC-TOOL] stable instructions'))
    monkeypatch.setattr(chatroom,'group_privacy_rule',lambda *a:'stable privacy')
    monkeypatch.setattr(chatroom.group_mentions,'hint_for',lambda *a:'')
    monkeypatch.setattr(chatroom,'with_current_device_context',lambda m,**kw:m)

    async def memories(*a, **kw):
        result=await context_builder.build_memory_blocks('',skip_digest=True,
            memory_hub_actor='connor' if path=='connor_private' else 'aion')
        result['memory_block']='[相关记忆] synthetic-related-'+phase[0]
        return result
    monkeypatch.setattr(chatroom,'build_memory_blocks',memories)
    monkeypatch.setattr(private,'build_ability_block',AsyncMock(return_value='[STATIC-TOOL] stable instructions'))
    monkeypatch.setattr(private,'with_current_device_context',lambda m,**kw:m)
    monkeypatch.setattr(seat_chat,'load_worldbook',lambda:wb)
    monkeypatch.setattr(seat_chat.actors,'get_actor',lambda actor:{'name':'SyntheticSeat','model':'review-hub-cache'})
    monkeypatch.setattr(seat_chat,'_persona_text',lambda *a:'stable persona')
    monkeypatch.setattr(seat_chat,'_ability_lines',lambda *a:['[STATIC-TOOL] stable instructions'])
    monkeypatch.setattr(seat_chat,'proactive_line',lambda *a:'')
    monkeypatch.setattr(seat_chat,'location_line',lambda:'')
    monkeypatch.setattr(seat_chat,'now_text',clock)
    monkeypatch.setattr(capabilities,'is_capability_enabled',lambda *a:False)
    monkeypatch.setattr(provider,'with_current_device_context',lambda m,**kw:m)
    cfg={'provider':'custom_openai','model':'synthetic','base_url':'https://hub-prefix.invalid/v1',
         'api_key':'synthetic','cache_marker':True}
    monkeypatch.setitem(provider.MODELS,'review-hub-cache',cfg)
    monkeypatch.setattr(seat_chat,'MODELS',{'review-hub-cache':cfg})
    monkeypatch.setattr(usage_log,'USAGE_DB',tmp_path/'usage.db')
    monkeypatch.setattr(provider,'_CACHE_OBSERVED',{})
    payloads=[]
    def handle(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200,stream=Stream())
    monkeypatch.setattr(provider.httpx,'AsyncClient',lambda **kw:REAL_CLIENT(transport=httpx.MockTransport(handle),**kw))

    async def construct():
        if path=='aion_group':return (await chatroom.build_aion_group_context('synthetic-room',rows))[0]
        if path=='connor_private':return (await chatroom.build_connor_1v1_context('synthetic-room',rows))[0]
        if path=='seat':
            seat_rows=[{**m,'sender':'ai3' if m['sender']=='connor' else m['sender']} for m in rows]
            return await seat_chat.build_context('ai3',seat_rows,chat_id='synthetic-room')
        # Private handler uses these same production helpers plus tagged insertion.
        history=[{'role':'user','content':'stable persona'},{'role':'assistant','content':'ack'}]
        history+=context_builder.render_merged_timeline(rows,'aion')
        offset=await private._insert_private_ability_block(history,2,0,user_name='SyntheticUser',model_key='review-hub-cache')
        memory=await memories()
        for text in (memory['time_block'],memory['memory_block']):
            history[2+offset:2+offset]=[{'role':'user','content':text,'_context':'live'},
                                      {'role':'assistant','content':'ack','_context':'live_ack'}]
            offset+=2
        return history

    async def run():
        for value in ('A','B'):
            phase[0]=value
            messages=await construct()
            async for _ in provider.stream_ai(messages,'review-hub-cache',{}):pass
    asyncio.run(run())
    assert len(payloads)==2 and hub.await_count==2
    def fixed_prefix(payload):
        messages=payload['messages']
        live=next(i for i,m in enumerate(messages) if 'synthetic-time-' in provider._message_text(m))
        return messages[:live]
    assert fixed_prefix(payloads[0])==fixed_prefix(payloads[1])
    assert any('synthetic-history-0' in provider._message_text(m) for m in fixed_prefix(payloads[0]))
    assert 'synthetic-memory-A' in json.dumps(payloads[0])
    assert 'synthetic-memory-B' in json.dumps(payloads[1])
    assert 'synthetic-time-A' in json.dumps(payloads[0])
    assert 'synthetic-time-B' in json.dumps(payloads[1])


def test_restart_preserves_grown_window_head(monkeypatch,tmp_path):
    import cache_window_hints
    monkeypatch.setattr(cache_window_hints,'PATH',tmp_path/'hints.json')
    monkeypatch.setattr(cache_window_hints,'_HINTS',None)
    monkeypatch.setattr(context_builder,'_WINDOW_STARTS',{})
    rows=[{'source':'private','id':str(n),'created_at':n+1,'content':str(n)} for n in range(7)]
    key=('synthetic',None,None,5)
    context_builder._stable_window(rows[:5],5,key)
    growing=context_builder._stable_window(rows,5,key)
    assert [m['id'] for m in growing]==['0','1','2','3','4','5','6']
    context_builder._WINDOW_STARTS.clear()  # simulates only the process-local restart
    cache_window_hints._HINTS=None
    restarted=context_builder._stable_window(rows,5,key)
    assert [m['id'] for m in restarted]==['0','1','2','3','4','5','6']


def test_entering_mixed_timeline_does_not_rewrite_old_message_prefix(monkeypatch):
    monkeypatch.setattr(context_builder,'_timeline_display_names',lambda:('SyntheticUser','SyntheticAion','SyntheticConnor'))
    private=[{'id':str(n),'source':'private','sender':'user' if n%2==0 else 'connor',
              'content':f'synthetic-history-{n}','created_at':n+1,'attachments':[]} for n in range(3)]
    grouped=private+[{'id':'group','source':'group','sender':'user','content':'synthetic group',
                     'created_at':4,'attachments':[]}]
    before=context_builder.render_merged_timeline(private,'connor')
    after=context_builder.render_merged_timeline(grouped,'connor')
    # First user is historical in both requests; only append a new source.
    assert before[1]==after[1], 'new group row rewrites source labels on old private history'
