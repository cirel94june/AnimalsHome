"""Revocation probes with synthetic model text and entirely mocked music APIs."""
import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock,Mock

import pytest


@pytest.mark.parametrize('function_name',['_process_chatroom_commands','_process_seat_commands'])
def test_group_revocation_during_notice_stops_autoplay_and_station_write(function_name):
    tree=ast.parse((Path(__file__).parent/'routes/chatroom.py').read_text(encoding='utf-8'))
    source=next(node for node in tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name==function_name)
    start=next(i for i,node in enumerate(source.body) if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='music_matches' for t in node.targets))
    end=next(i for i in range(start,len(source.body)) if isinstance(source.body[i],ast.If) and any(isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='record_music_request' for node in ast.walk(source.body[i])))+1
    fragment=source.body[start:end]
    active={'value':True}
    async def notice(*args,**kwargs):
        active['value']=False # Owner closes permission while message persistence waits.
        await asyncio.sleep(0)
    async def to_thread(fn,*args,**kwargs):return fn(*args,**kwargs)
    import re
    from types import SimpleNamespace
    broadcast=AsyncMock();record=AsyncMock();queue=SimpleNamespace(put=AsyncMock())
    namespace={'full_text':'[MUSIC:synthetic song]','MUSIC_CMD_PATTERN':re.compile(r'\[MUSIC:([^\]]+)\]'),
               'is_capability_enabled':lambda key:active['value'],'asyncio':SimpleNamespace(to_thread=to_thread),
               'search_songs':lambda *a,**k:[{'id':1,'name':'song','artist':'artist'}],
               'get_audio_url':lambda value:'https://synthetic.invalid/audio','_chatroom_sys_msg':notice,
               'who_label':'actor','who_identity':'aion','label':'actor','actor_id':'ai3',
               'room_id':'room','msg_id':'message','_q':queue,'triggered':{},
               'ws_manager':SimpleNamespace(broadcast=broadcast),'record_music_request':record}
    function=ast.AsyncFunctionDef(name='probe',args=ast.arguments(posonlyargs=[],args=[ast.arg(arg='full_text')],kwonlyargs=[],kw_defaults=[],defaults=[]),body=fragment,decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),'music-fragment','exec'),namespace)
    asyncio.run(namespace['probe'](namespace['full_text']))
    broadcast.assert_not_awaited()
    record.assert_not_awaited()
    queue.put.assert_not_awaited()


def test_ai_cloud_write_checks_music_switch_after_worker_queue(tmp_path,monkeypatch):
    import capabilities
    import music_actions as actions
    import music_station
    import netease_account as account
    from music_station import MusicStationStore
    monkeypatch.setattr(account,'ACCOUNT_PATH',tmp_path/'account.json')
    cfg=account.load();cfg.update(managed=True,user_id='1',generation=1)
    cfg['permissions']['cloud_playlists']=True
    account._save(cfg)
    store=MusicStationStore(tmp_path/'music.db',tmp_path/'station')
    monkeypatch.setattr(music_station,'get_music_station_store',lambda:store)
    monkeypatch.setattr(account,'ASYNC_LOCK',asyncio.Lock())
    active={'value':True}
    monkeypatch.setattr(capabilities,'is_capability_enabled',lambda key:active['value'])
    monkeypatch.setattr(actions,'resolve_songs',AsyncMock(return_value=[]))
    monkeypatch.setattr(account,'_identity',lambda session:{'user_id':'1'})
    monkeypatch.setattr(account,'_playlists',lambda cfg:[])
    monkeypatch.setattr(account,'_tracks',lambda *args:{'owned_by':'1','name':'private'})
    create=Mock(return_value={'code':200,'id':123})
    monkeypatch.setattr(account.playlist,'SetCreatePlaylist',create)
    async def queued(fn,*args,**kwargs):
        active['value']=False # Closing switch after execute's final pre-queue check.
        return fn(*args,**kwargs)
    monkeypatch.setattr(actions,'run_sync',queued)
    async def probe():
        await store.init()
        return await actions.execute({'target':'netease','name':'must-not-create'},'queued-ai',actor='aion',as_ai=True)
    result=asyncio.run(probe())
    create.assert_not_called()
    assert not result['ok']


def test_private_notice_revocation_stops_later_station_write_and_broadcast():
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    tree=ast.parse((Path(__file__).parent/'routes/chat.py').read_text(encoding='utf-8'))
    function=next(node for node in tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='_music_sys_msg')
    active={'value':True}
    async def commit():
        active['value']=False
        await asyncio.sleep(0)
    @asynccontextmanager
    async def database():yield SimpleNamespace(execute=AsyncMock(),commit=commit)
    record=AsyncMock();broadcast=AsyncMock()
    namespace={'is_capability_enabled':lambda key:active['value'],'load_worldbook':lambda:{'ai_name':'actor'},
               'time':SimpleNamespace(time=lambda:123),'get_db':database,
               'record_music_request':record,'manager':SimpleNamespace(broadcast=broadcast)}
    exec(compile(ast.Module(body=[function],type_ignores=[]),'private-music','exec'),namespace)
    asyncio.run(namespace['_music_sys_msg']('synthetic',[{'id':1,'name':'song','artist':'artist'}]))
    record.assert_not_awaited()
    broadcast.assert_not_awaited()


def test_wish_revocation_during_status_write_stops_late_music_attachment(monkeypatch):
    import music
    tree=ast.parse((Path(__file__).parent/'autonomy.py').read_text(encoding='utf-8'))
    source=next(node for node in tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='_run_wish_pool')
    branch=next(node for node in source.body if isinstance(node,ast.If) and isinstance(node.test,ast.Name) and node.test.id=='music_keyword')
    active={'value':True}
    monkeypatch.setattr(music,'search_songs',lambda *args,**kwargs:[{'id':1,'name':'song','artist':'artist'}])
    monkeypatch.setattr(music,'get_audio_url',lambda value:'https://synthetic.invalid/audio')
    async def update(*args,**kwargs):
        active['value']=False
        await asyncio.sleep(0)
        return {'id':'wish','status':'fulfilled'}
    save=AsyncMock(return_value={'id':'message'})
    namespace={'music_keyword':'song','is_capability_enabled':lambda key:active['value'],
               'card':{'type':'wish_card','status':'active'},'wish':{'id':'wish'},'result':{},
               'actor':'aion','actor_name':'actor','user_name':'owner','visible_response':'test',
               'force_private':True,'update_wish':update,'_save_private_message':save,
               '_wish_music_attachment':lambda song:{'type':'music','id':song['id']},
               'append_idle_event':AsyncMock(return_value={}), 'viewed_event':{}}
    function=ast.AsyncFunctionDef(name='probe',args=ast.arguments(posonlyargs=[],args=[ast.arg(arg='visible_response')],kwonlyargs=[],kw_defaults=[],defaults=[]),body=[branch],decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),'wish-music','exec'),namespace)
    asyncio.run(namespace['probe']('test'))
    for call in save.await_args_list:
        assert all(item.get('type')!='music' for item in call.args[2]), 'wish still published the music attachment after permission was revoked during update_wish'
