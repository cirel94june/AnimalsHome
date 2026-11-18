import asyncio
import json
from unittest.mock import AsyncMock, Mock
import pytest
import netease_account as account
import music_personal as personal
import music_actions as actions
import music_station
import music_discovery as discovery


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    import actors
    monkeypatch.setattr(account,'ACCOUNT_PATH',tmp_path/'account.json')
    monkeypatch.setattr(account,'ASYNC_LOCK',asyncio.Lock())
    monkeypatch.setattr(actors,'list_actors',lambda:[{'id':'aion','name':'Companion A','enabled':True},{'id':'connor','name':'Companion B','enabled':True}])
    monkeypatch.setattr(actors,'display_name',lambda actor:actor)
    monkeypatch.setattr('capabilities.is_capability_enabled',lambda key:True)
    store=music_station.MusicStationStore(tmp_path/'music.db',tmp_path/'station')
    monkeypatch.setattr(music_station,'get_music_station_store',lambda:store)
    return store


def test_companions_collect_into_separate_local_playlists(isolated):
    async def probe():
        await isolated.init()
        song={'id':1,'name':'sample','artist':'artist'}
        await personal.add([song],'aion','one')
        await personal.add([song],'connor','two')
        await personal.add([song],'aion','one')
        playlists=await isolated.list_playlists()
        assert {p['name'] for p in playlists}=={'Companion A的歌单','Companion B的歌单'}
        for p in playlists:assert len(await isolated.list_tracks(p['id']))==1
    asyncio.run(probe())


def test_cloud_collection_uses_binding_and_deduplicates(isolated,monkeypatch):
    cfg=account.load();cfg.update(user_id='7',actor_playlists={'aion':'201'})
    cfg['permissions']['cloud_playlists']=True;account._save(cfg)
    mutate=Mock(return_value={'ok':True,'playlist_id':'201','message':'saved'})
    monkeypatch.setattr(account,'mutate',mutate)
    async def run(fn,*args,**kwargs):return fn(*args,**kwargs)
    monkeypatch.setattr(actions,'run_sync',run)
    async def probe():
        await isolated.init()
        for _ in range(2):assert (await personal.add([{'id':1,'name':'sample','artist':'artist'}],'aion','same'))['ok']
    asyncio.run(probe())
    assert mutate.call_count==1
    assert mutate.call_args.args[0]=='add' and mutate.call_args.args[2]=='201'


def test_actor_rename_keeps_its_existing_local_playlist(isolated,monkeypatch):
    import actors
    names={'name':'Before'}
    monkeypatch.setattr(actors,'list_actors',lambda:[{'id':'aion','name':names['name'],'enabled':True}])
    async def probe():
        await isolated.init()
        await personal.add([{'id':1,'name':'one'}],'aion','first',cloud=False)
        names['name']='After'
        await personal.add([{'id':2,'name':'two'}],'aion','second',cloud=False)
        playlists=await isolated.list_playlists()
        assert len(playlists)==1
        assert len(await isolated.list_tracks(playlists[0]['id']))==2
    asyncio.run(probe())


def test_personal_owner_binding_survives_concurrency_and_playlist_delete(isolated):
    async def probe():
        await isolated.init()
        await asyncio.gather(*(personal.add([{'id':i,'name':str(i)}],'aion','op-'+str(i),cloud=False) for i in [1,2]))
        playlists=await isolated.list_playlists();assert len(playlists)==1
        assert len(await isolated.list_tracks(playlists[0]['id']))==2
        await isolated.delete_playlist(playlists[0]['id'])
        await personal.add([{'id':3,'name':'three'}],'aion','third',cloud=False)
        playlists=await isolated.list_playlists();assert len(playlists)==1
        assert [s['external_id'] for s in await isolated.list_tracks(playlists[0]['id'])]==['3']
    asyncio.run(probe())


@pytest.mark.parametrize('command',['[MUSIC_SEARCH:sample]','[MUSIC_PICK:1]','[MUSIC_SAVE：1]'])
def test_discovery_commands_are_hidden_in_stream_and_saved_text(command):
    from safe_live_stream import KnownCommandStreamFilter
    from web_search import WebCommandStreamFilter
    from context_builder import strip_tool_commands
    for cls in [KnownCommandStreamFilter,WebCommandStreamFilter]:
        stream=cls();text=''.join(stream.feed(c) for c in 'before'+command+'after')
        text+=stream.finish()[0] if cls is KnownCommandStreamFilter else stream.flush()
        assert text=='beforeafter'
    assert strip_tool_commands('before'+command+'after')=='beforeafter'


def test_switch_off_and_account_change_block_collection(isolated,monkeypatch):
    async def probe():
        await isolated.init()
        assert not (await personal.add([{'id':1}],'aion','old',generation=999))['ok']
        monkeypatch.setattr('capabilities.is_capability_enabled',lambda key:False)
        assert not (await personal.add([{'id':1}],'aion','off'))['ok']
        assert await isolated.list_playlists()==[]
    asyncio.run(probe())


def test_binding_rejects_shared_and_foreign_playlists(isolated,monkeypatch):
    cfg=account.load();cfg['user_id']='7';account._save(cfg)
    monkeypatch.setattr(account,'read',lambda *args:{'playlists':[{'id':'201','owned':True,'liked':False},{'id':'202','owned':False,'liked':False}]})
    result=personal.bind('aion','201',0)
    assert result['account']['generation']==1
    with pytest.raises(account.NeteaseError):personal.bind('connor','201',1)
    with pytest.raises(account.NeteaseError):personal.bind('connor','202',1)
    with pytest.raises(account.NeteaseError):personal.bind('connor','',0)


def test_music_search_uses_netease_not_web(isolated,monkeypatch):
    import web_search
    monkeypatch.setattr('music.search_songs',lambda *args:[{'id':1,'name':'sample','artist':'artist','album':''}])
    async def run(fn,*args,**kwargs):return fn(*args,**kwargs)
    monkeypatch.setattr(actions,'run_sync',run)
    tavily=AsyncMock();monkeypatch.setattr(web_search,'tavily_search',tavily)
    result=asyncio.run(web_search.run_web_commands(['MUSIC_SEARCH:sample'],[]))
    assert 'MUSIC_CATALOG' in result[0];tavily.assert_not_awaited()
    monkeypatch.setattr('capabilities.is_capability_enabled',lambda key:key=='music')
    monkeypatch.setattr('capabilities.taobao_chat_on',lambda:False)
    assert web_search.allowed_web_commands(['MUSIC_SEARCH:sample','other'],[])==(['MUSIC_SEARCH:sample'],[])


@pytest.mark.parametrize('success,enabled,expected',[(True,True,0),(False,True,0),(True,False,0),(True,True,99)])
def test_discovery_can_only_pick_real_catalog_ids(isolated,monkeypatch,success,enabled,expected):
    import capabilities
    monkeypatch.setattr(capabilities,'is_capability_enabled',lambda key:enabled)
    record=AsyncMock();monkeypatch.setattr(music_station,'record_music_request',record)
    monkeypatch.setattr(actions,'run_sync',AsyncMock(return_value='synthetic://audio'))
    text='chosen [MUSIC_PICK:1][MUSIC_PICK:999][转账：200元]'
    context='results\nMUSIC_CATALOG\n'+json.dumps([{'id':1,'name':'sample','artist':'artist'}])
    clean,atts=asyncio.run(discovery.finish(text,[context],actor='aion',source_type='private',source_id='c',message_id='m',success=success,expected_generation=expected))
    assert '转账' not in clean and 'MUSIC_PICK' not in clean
    if success and enabled and expected==0:
        assert [a['id'] for a in atts]==[1];record.assert_awaited_once()
    else:assert atts==[];record.assert_not_awaited()
