"""Personal playlist/discovery review probes: temporary SQLite/config, no APIs."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
import netease_account as account
import music_personal as personal
import music_station as station


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(account,'ACCOUNT_PATH',tmp_path/'account.json')
    cfg=account.load();cfg.update(generation=1)
    account._save(cfg)
    import capabilities
    monkeypatch.setattr(capabilities,'is_capability_enabled',lambda key:True)
    return tmp_path


def test_record_keeps_original_account_generation_across_database_wait(isolated,monkeypatch):
    class Store:
        async def record_request(self,*args,**kwargs):
            cfg=account.load();cfg.update(user_id='2',generation=2)
            account._save(cfg) # Owner switches accounts while record_request waits.
            return 'synthetic-track'
    monkeypatch.setattr(station,'get_music_station_store',lambda:Store())
    pending=[]
    monkeypatch.setattr(station,'spawn_generation_task',lambda coroutine:pending.append(coroutine))
    collect=AsyncMock(return_value={'ok':True})
    monkeypatch.setattr(personal,'collect',collect)
    async def probe():
        await station.record_music_request({'id':1},'aion','actor','private','chat','message')
        assert len(pending)==2
        pending[0].close() # Background media download is outside this local probe.
        await pending[1]
    asyncio.run(probe())
    assert collect.await_args.args[-1]==1, 'account generation was captured after the database wait, rebinding an old request to the new account'


def test_same_display_name_does_not_merge_two_actors_personal_playlists(isolated,monkeypatch):
    import actors
    monkeypatch.setattr(actors,'list_actors',lambda:[{'id':'aion','name':'Twin','enabled':True},{'id':'connor','name':'Twin','enabled':True}])
    store=station.MusicStationStore(isolated/'music.db',isolated/'station')
    monkeypatch.setattr(station,'get_music_station_store',lambda:store)
    async def probe():
        await store.init()
        await personal.add([{'id':1,'name':'one'}],'aion','operation-A',generation=1,cloud=False)
        await personal.add([{'id':2,'name':'two'}],'connor','operation-B',generation=1,cloud=False)
        playlists=await store.list_playlists()
        assert len(playlists)==2, 'personal playlists keyed solely by display name merged two distinct actors'
    asyncio.run(probe())


def test_quoted_discovery_save_is_an_example_not_a_write(isolated,monkeypatch):
    import music_discovery
    add=AsyncMock(return_value={'ok':True,'message':'saved'})
    monkeypatch.setattr(personal,'add',add)
    context='real search results\nMUSIC_CATALOG\n'+json.dumps([{'id':1,'name':'one','artist':'artist'}])
    asyncio.run(music_discovery.finish('仅举例：```\n[MUSIC_SAVE:1]\n```',[context],
        actor='aion',source_type='private',source_id='chat',message_id='message',expected_generation=1))
    add.assert_not_awaited()


def test_binding_does_not_overwrite_credentials_renewed_during_playlist_read(isolated,monkeypatch):
    cfg=account.load();cfg.update(user_id='1',managed=True,cookies=[{'name':'MUSIC_U','value':'old'}])
    account._save(cfg)
    monkeypatch.setattr(personal,'profiles',lambda:[{'actor':'aion'}])
    def read(*args):
        current=account.load();current['cookies']=[{'name':'MUSIC_U','value':'renewed'}]
        account._save(current) # _read_checked refreshes 301 before returning results.
        return {'playlists':[{'id':'123','owned':True,'liked':False}]}
    monkeypatch.setattr(account,'read',read)
    personal.bind('aion','123',1)
    assert account.load()['cookies'][0]['value']=='renewed', 'stale bind snapshot overwrote freshly renewed credentials'


def test_discovery_discards_attachments_if_account_changes_during_record(isolated,monkeypatch):
    import actors
    import music_actions
    import music_discovery
    monkeypatch.setattr(actors,'display_name',lambda actor:actor)
    monkeypatch.setattr(music_actions,'run_sync',AsyncMock(return_value='https://synthetic.invalid/audio'))
    async def record(*args,**kwargs):
        cfg=account.load();cfg.update(user_id='2',generation=2)
        account._save(cfg)
    monkeypatch.setattr(station,'record_music_request',record)
    context='results\nMUSIC_CATALOG\n'+json.dumps([{'id':1,'name':'one','artist':'artist'}])
    _,attachments=asyncio.run(music_discovery.finish('[MUSIC_PICK:1]',[context],
        actor='aion',source_type='private',source_id='chat',message_id='message',expected_generation=1))
    assert attachments==[], 'account changed during record_request but old-account discovery still returned music attachments for publication'


def test_private_background_search_never_changes_connor_into_aion(monkeypatch):
    import sys
    from types import SimpleNamespace
    import generation_control
    import music_discovery
    routed=[];pending=[]
    async def private(*args,actor='aion',**kwargs):routed.append(actor)
    monkeypatch.setitem(sys.modules,'routes.chat',SimpleNamespace(perform_web_search_check=private))
    monkeypatch.setattr(generation_control,'spawn_generation_task',lambda task:pending.append(task))
    music_discovery.launch_background_search(['MUSIC_SEARCH:synthetic'],{'type':'private'},'chat','connor','connor-model')
    async def finish():
        for task in pending:await task
    asyncio.run(finish())
    assert not routed or routed==['connor'], 'private fallback silently routed Connor through the Aion-only followup (persona/history/playlist identity)'


def test_renamed_actor_does_not_lose_old_playlist_to_another_actor(isolated,monkeypatch):
    import actors
    names={'aion':'Before','connor':'Other'}
    monkeypatch.setattr(actors,'list_actors',lambda:[{'id':key,'name':name,'enabled':True} for key,name in names.items()])
    store=station.MusicStationStore(isolated/'music.db',isolated/'station')
    monkeypatch.setattr(station,'get_music_station_store',lambda:store)
    async def probe():
        await store.init()
        await personal.add([{'id':1,'name':'one'}],'aion','operation-A',generation=1,cloud=False)
        original=account.load()['actor_local_playlists']['aion']
        names.update(aion='After',connor='Before') # Current names remain distinct.
        await personal.add([{'id':2,'name':'two'}],'connor','operation-B',generation=1,cloud=False)
        mapping=account.load()['actor_local_playlists']
        assert mapping['connor']!=original, 'second actor captured the renamed actor\'s still-bound historical playlist by matching its name'
        assert [t['external_id'] for t in await store.list_tracks(original)]==['1']
    asyncio.run(probe())


@pytest.mark.parametrize('operation',['create','add'])
def test_automatic_cloud_collection_never_claims_other_actor_binding(isolated,monkeypatch,operation):
    from unittest.mock import Mock
    cfg=account.load();cfg.update(user_id='1',actor_playlists={'aion':'123'})
    cfg['permissions']['cloud_playlists']=True;account._save(cfg)
    monkeypatch.setattr(account,'_identity',lambda session:{'user_id':'1'})
    monkeypatch.setattr(account,'_playlists',lambda cfg:[{'id':'123','name':'Shared','owned':True}])
    create=Mock();write=Mock()
    monkeypatch.setattr(account.playlist,'SetCreatePlaylist',create)
    monkeypatch.setattr(account.playlist,'SetManipulatePlaylistTracks',write)
    with pytest.raises(account.NeteaseError,match='另一位 AI'):
        account.mutate(operation,name='Shared',playlist_id='123' if operation=='add' else '',
                       song_ids=[1],as_ai=True,expected_generation=1,personal_actor='connor')
    create.assert_not_called();write.assert_not_called()


def test_owner_manual_cloud_write_is_not_blocked_by_actor_binding(isolated,monkeypatch):
    from unittest.mock import Mock
    cfg=account.load();cfg.update(user_id='1',actor_playlists={'aion':'123'})
    account._save(cfg)
    monkeypatch.setattr(account,'_identity',lambda session:{'user_id':'1'})
    monkeypatch.setattr(account,'_tracks',lambda *args:{'owned_by':'1','name':'Shared'})
    write=Mock(return_value={'code':200})
    monkeypatch.setattr(account.playlist,'SetManipulatePlaylistTracks',write)
    assert account.mutate('add',playlist_id='123',song_ids=[1],expected_generation=1)['ok']
    write.assert_called_once()
