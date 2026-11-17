"""Independent account/transaction probes; temporary files, no real API/account."""
import asyncio
import json
from types import SimpleNamespace

import aiosqlite
import pytest
import pyncm.apis

import netease_account as account
from music_station import MusicStationStore


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(account,'ACCOUNT_PATH',tmp_path/'account.json')
    def forbidden(*args,**kwargs):pytest.fail('no network allowed in review')
    monkeypatch.setattr(account._Session,'request',forbidden)
    account._save({'managed':True,'user_id':'1','nickname':'test','generation':1,
                   'cookies':[{'name':'MUSIC_U','value':'SYNTHETIC_PRIVATE_TOKEN'}],
                   'verified_at':1,'permissions':dict(account.PERMISSIONS)})
    return tmp_path


def test_private_playlist_serializes_real_protocol_value_ten(isolated,monkeypatch):
    captured=[]
    def encrypt(path,payload):
        captured.append((path,json.loads(payload)))
        return {'params':'synthetic'}
    def request(self,method,url,*args,**kwargs):
        assert '/playlist/create' in url
        return SimpleNamespace(content=b'{"code":200,"id":123}')
    monkeypatch.setattr(pyncm.apis,'EapiEncrypt',encrypt)
    monkeypatch.setattr(account._Session,'request',request)
    monkeypatch.setattr(account,'_identity',lambda session:{'user_id':'1','nickname':'test'})
    monkeypatch.setattr(account,'_playlists',lambda cfg:[])
    monkeypatch.setattr(account,'_tracks',lambda *args:{'owned_by':'1','name':'private'})
    assert account.mutate('create',name='private')['ok']
    assert len(captured)==1
    # NetEaseCloudMusicApi playlist_create protocol: 0 public, 10 private.
    assert str(captured[0][1]['privacy'])=='10', 'privacy=True is encoded as 1 by installed pyncm, which is not the private-playlist protocol value'


def test_public_logout_never_exposes_or_restores_previous_cookie(isolated,monkeypatch):
    public=json.dumps(account.public())
    assert 'SYNTHETIC_PRIVATE_TOKEN' not in public
    account.logout()
    cookies,_=account.credentials()
    assert cookies==[] and not account.public()['logged_in']


def test_cloud_permission_denial_precedes_any_api_call(isolated):
    with pytest.raises(account.NeteaseError,match='权限'):
        account.mutate('create',name='private',as_ai=True,expected_generation=1)


def test_local_song_metadata_transaction_rolls_back_on_invalid_later_song(tmp_path):
    async def probe():
        store=MusicStationStore(tmp_path/'music.db',tmp_path/'station')
        await store.init()
        with pytest.raises(ValueError):
            await store.save_song_playlist('must rollback',[{'id':1,'name':'valid'},{'id':0,'name':'invalid'}])
        assert await store.list_playlists()==[]
        async with aiosqlite.connect(store.db_path) as db:
            assert (await (await db.execute('SELECT COUNT(*) FROM music_station_tracks')).fetchone())[0]==0
    asyncio.run(probe())


def test_action_claim_is_atomic_and_fingerprint_bound(tmp_path):
    async def probe():
        store=MusicStationStore(tmp_path/'music.db',tmp_path/'station')
        await store.init()
        claims=await asyncio.gather(*(store.claim_music_action('same-operation','fingerprint') for _ in range(5)))
        assert sum(item is None for item in claims)==1
        assert all(item is None or item['state']=='pending' for item in claims)
        with pytest.raises(ValueError):await store.claim_music_action('same-operation','different-account')
    asyncio.run(probe())


def test_failed_replacement_login_invalidates_previous_global_session_cache(monkeypatch):
    import music
    session=SimpleNamespace(cookies={})
    monkeypatch.setattr(music._pyncm,'GetCurrentSession',lambda:session)
    monkeypatch.setattr(music,'_inited',False)
    monkeypatch.setattr(music,'_credential_version',None)
    monkeypatch.setattr(music,'_last_login_time',0)
    selected={'token':'account-A'}
    monkeypatch.setattr(account,'credentials',lambda:([{'name':'MUSIC_U','value':selected['token']}],selected['token']))
    def login(MUSIC_U,**kwargs):
        session.cookies['MUSIC_U']=MUSIC_U
        if MUSIC_U=='account-B':raise RuntimeError('synthetic network failure')
        return {'result':{'success':True}}
    monkeypatch.setattr(music,'LoginViaCookie',login)
    music._ensure_login()
    selected['token']='account-B'
    with pytest.raises(RuntimeError):music._ensure_login()
    selected['token']='account-A'
    music._ensure_login()
    assert session.cookies['MUSIC_U']=='account-A', 'old success cache skipped re-login although the default session now contains failed account B'


def test_queued_owner_write_never_rebinds_to_new_account(isolated,monkeypatch):
    import music_actions as actions
    import music_station
    store=MusicStationStore(isolated/'music.db',isolated/'station')
    monkeypatch.setattr(music_station,'get_music_station_store',lambda:store)
    monkeypatch.setattr(account,'ASYNC_LOCK',asyncio.Lock())
    mutations=[]
    async def resolve(specs):return []
    async def run_sync(fn,*args,**kwargs):
        assert fn is account.mutate
        mutations.append((account.load()['user_id'],kwargs['expected_generation']))
        return {'ok':True,'message':'saved to current account'}
    monkeypatch.setattr(actions,'resolve_songs',resolve)
    monkeypatch.setattr(actions,'run_sync',run_sync)
    async def probe():
        await store.init()
        await account.ASYNC_LOCK.acquire()
        task=asyncio.create_task(actions.execute({'target':'netease','name':'intended-for-A'},'queued-owner-action'))
        await asyncio.sleep(0) # The original owner's request is now waiting.
        cfg=account.load();cfg.update(user_id='2',generation=2)
        account._save(cfg) # A simultaneous login route commits account B.
        account.ASYNC_LOCK.release()
        await task
    asyncio.run(probe())
    assert mutations==[], 'queued owner write loaded identity only after waiting and silently executed against account B'
