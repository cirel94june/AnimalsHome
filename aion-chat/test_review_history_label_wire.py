"""Independent new-turn wire prefix and baseline attachment comparison."""
import ast
import asyncio
import json
import subprocess

import ai_providers as provider
import context_builder
from test_review_cache_request_trace import cfg, response, transport


def row(n,sender='user',source='private',attachments=None):
    return {'id':str(n),'source':source,'sender':sender,'content':f'synthetic-turn-{n}',
            'created_at':n+1,'attachments':attachments or []}


def test_new_group_turn_keeps_previous_actual_wire_text_prefix(cfg,monkeypatch):
    monkeypatch.setattr(context_builder,'_timeline_display_names',lambda:('SyntheticUser','SyntheticAion','SyntheticConnor'))
    old=[row(1),row(2,'connor')]
    payloads=[]
    def handle(request):
        payloads.append(json.loads(request.content))
        return response()
    transport(monkeypatch,handle)
    async def run():
        for index,rows in enumerate((old,old+[row(3,source='group')])):
            messages=[{'role':'system','content':'synthetic stable persona'},
                      {'role':'user','content':f'[当前时间] synthetic-{index}','_context':'live'},
                      {'role':'assistant','content':'ack','_context':'live_ack'}]
            messages+=context_builder.render_merged_timeline(rows,'connor')
            async for _ in provider.stream_ai(messages,'review-trace',{}):pass
    asyncio.run(run())
    first=payloads[0]['messages']
    boundary=next(n for n,m in enumerate(first) if '[当前时间]' in provider._message_text(m))
    # Existing moving cache_control breakpoints change string/text-block shape.
    # Compare actual sent role/text; this change only claims stable utterance text.
    sent_text=lambda messages:[(m['role'],provider._message_text(m)) for m in messages]
    assert sent_text(first[:boundary])==sent_text(payloads[1]['messages'][:boundary])
    assert any('synthetic-turn-1' in provider._message_text(m) for m in first[:boundary])
    texts=[provider._message_text(m) for m in payloads[1]['messages']]
    assert texts.index(next(t for t in texts if 'synthetic-turn-1' in t)) < texts.index(next(t for t in texts if 'synthetic-turn-2' in t))
    assert 'synthetic-turn-3' in texts[-1] and '[群聊]' in texts[-1]


def test_media_roles_and_order_match_production_baseline(monkeypatch):
    monkeypatch.setattr(context_builder,'_timeline_display_names',lambda:('SyntheticUser','SyntheticAion','SyntheticConnor'))
    baseline=subprocess.run(['git','show','82d3eb7:aion-chat/context_builder.py'],
                            capture_output=True,text=True,encoding='utf-8',check=True).stdout
    original=next(node for node in ast.parse(baseline).body
                  if isinstance(node,ast.FunctionDef) and node.name=='render_merged_timeline')
    namespace=vars(context_builder).copy()
    exec(compile(ast.Module(body=[original],type_ignores=[]),'synthetic-baseline','exec'),namespace)
    photo=lambda n:{'type':'image','url':f'/uploads/synthetic-{n}.png'}
    rows=[row(1,attachments=[photo(1)]),row(2,'connor'),
          row(3,source='group',attachments=[photo(3)]),row(4,'aion','group'),
          row(5,attachments=[photo(5)])]
    before=namespace['render_merged_timeline'](rows,'connor')
    after=context_builder.render_merged_timeline(rows,'connor')
    assert len(before)==len(after)
    for old,new in zip(before[1:],after[1:]):
        expected={**old,'content':old['content'].replace('当前用户消息 -','历史消息 -')}
        assert expected==new
    assert '最新一条用户发言' in after[0]['content']
    assert '结合其后的其他人发言' in after[0]['content']
