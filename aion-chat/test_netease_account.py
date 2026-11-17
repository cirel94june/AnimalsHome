import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock, AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import netease_account as account
import music_actions as actions
import music_station
from routes.netease_account import router
from safe_live_stream import KnownCommandStreamFilter
from web_search import WebCommandStreamFilter


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'account.json'
        self.p = patch.object(account, 'ACCOUNT_PATH', self.path); self.p.start()
        self.addCleanup(self.p.stop); self.addCleanup(self.tmp.cleanup)
        account._QR.clear()

    def saved(self, **values):
        cfg=account.load();cfg.update(values);account._save(cfg);return cfg

    def test_logout_never_resurrects_legacy_cookie(self):
        with patch.dict('config.SETTINGS', {'netease_music_u':'legacy-secret'}):
            self.assertTrue(account.credentials()[0])
            account.logout()
            self.assertEqual(account.credentials()[0], [])

    def test_public_status_never_contains_cookie(self):
        self.saved(managed=True,cookies=[{'name':'MUSIC_U','value':'secret-value'}],user_id='7',verified_at=1)
        self.assertNotIn('secret-value',json.dumps(account.public()))

    def test_bad_configuration_fails_closed(self):
        for values in [{'permissions':[]},{'permissions':{'cloud_playlists':'yes'}},{'cookies':[9]},{'generation':'1'}]:
            self.path.write_text(json.dumps(values),encoding='utf-8')
            with self.assertRaises(account.NeteaseError):account.load()

    def test_cloud_permissions_require_login_and_reset_on_login(self):
        with self.assertRaises(account.NeteaseError):account.update_permissions({'cloud_playlists':True})
        self.saved(user_id='7',permissions={'local_playlists':False,'cloud_playlists':True,'cloud_likes':True})
        session=account._session([{'name':'MUSIC_U','value':'token'}])
        result=account._commit(session,{'user_id':'7','nickname':'sample'})
        self.assertEqual(result['permissions'],{'local_playlists':False,'cloud_playlists':False,'cloud_likes':False})
        session.close()

    def test_qr_not_saved_until_owner_confirm_and_confirm_idempotent(self):
        session=account._session([{'name':'MUSIC_U','value':'token'}])
        with patch.object(account,'_session',return_value=session), patch.object(account.login,'LoginQrcodeUnikey',return_value={'code':200,'unikey':'abcdefgh123'}), patch.object(account.login,'LoginQrcodeCheck',return_value={'code':803}), patch.object(account,'_identity',return_value={'user_id':'7','nickname':'sample'}):
            qr=account.start_qr();self.assertFalse(self.path.exists())
            polled=account.poll_qr(qr['handle']);self.assertEqual(polled['state'],'confirm')
            self.assertFalse(self.path.exists())
            account.confirm_qr(qr['handle']);generation=account.load()['generation']
            account.confirm_qr(qr['handle']);self.assertEqual(account.load()['generation'],generation)
            self.assertFalse(account.public()['permissions']['cloud_playlists'])
        account.cancel_qr(qr['handle'])

    def test_login_change_stops_stale_qr(self):
        session=account._session([])
        account._QR['handle']={'expires':10**12,'session':session,'identity':{'user_id':'7'},'generation':0}
        self.saved(generation=1)
        with self.assertRaises(account.NeteaseError):account.confirm_qr('handle')
        account.cancel_qr('handle')

    def test_ai_cloud_denial_before_network(self):
        self.saved(user_id='7')
        with patch.object(account,'_identity') as identity, patch('capabilities.is_capability_enabled',return_value=True):
            with self.assertRaises(account.NeteaseError):account.mutate('create','name',as_ai=True)
            identity.assert_not_called()

    def test_mutation_verifies_current_owner(self):
        self.saved(user_id='7')
        with patch.object(account,'_identity',return_value={'user_id':'8'}), patch.object(account.playlist,'SetCreatePlaylist') as create:
            with self.assertRaises(account.NeteaseError):account.mutate('create','name')
            create.assert_not_called()

    def test_other_users_playlist_not_written(self):
        self.saved(user_id='7')
        with patch.object(account,'_identity',return_value={'user_id':'7'}),patch.object(account,'_tracks',return_value={'owned_by':'8'}),patch.object(account.playlist,'SetManipulatePlaylistTracks') as add:
            with self.assertRaises(account.NeteaseError):account.mutate('add',playlist_id='123',song_ids=[12])
            add.assert_not_called()

    def test_routes_reject_cross_site_and_never_echo_secret(self):
        app=FastAPI();app.include_router(router)
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/music-station/account').status_code,403)
            self.assertEqual(client.post('/api/music-station/account/logout',headers={'X-Music-Station':'1','Origin':'https://foreign.invalid'}).status_code,403)
            secret={'cookie':{'secret':'never-echo-this'}}
            response=client.post('/api/music-station/account/cookie',headers={'X-Music-Station':'1'},json=secret)
            self.assertEqual(response.status_code,400)
            self.assertNotIn('never-echo-this',response.text)


class PlaylistTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.store=music_station.MusicStationStore(root/'chat.db',root/'music')
        await self.store.init()
        self.patches=[patch.object(account,'ACCOUNT_PATH',root/'account.json'),patch.object(music_station,'get_music_station_store',return_value=self.store),patch('capabilities.is_capability_enabled',return_value=True)]
        for p in self.patches:p.start()
        self.song={'id':1,'name':'sample','artist':'artist','duration':123}

    async def asyncTearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    async def test_import_is_atomic_and_duplicate_safe_without_downloads(self):
        result=await self.store.save_song_playlist('sample',[self.song])
        again=await self.store.save_song_playlist('sample',[self.song])
        self.assertEqual(again['added'],0);self.assertEqual(result['playlist_id'],again['playlist_id'])
        with self.assertRaises(ValueError):
            await self.store.save_song_playlist('rollback',[self.song,{'id':-1}])
        self.assertEqual(len(await self.store.list_playlists()),1)
        self.assertEqual(len(await self.store.list_tracks()),1)

    async def test_same_action_runs_once_even_concurrently(self):
        data={'name':'sample','songs':[]}
        first,second=await asyncio.gather(actions.execute(data,'same'),actions.execute(data,'same'))
        self.assertTrue(first['ok']);self.assertTrue(second['ok'])
        self.assertEqual(len(await self.store.list_playlists()),1)
        with self.assertRaises(ValueError):await actions.execute({'name':'changed'},'same')

    async def test_delayed_request_cannot_write_to_a_new_login(self):
        with self.assertRaises(ValueError):
            await actions.execute({'name':'sample','account_generation':999},'stale-owner')
        self.assertEqual(await self.store.list_playlists(),[])

    async def test_cloud_timeout_is_never_replayed(self):
        cfg=account.load();cfg.update(user_id='7',verified_at=1);account._save(cfg)
        with patch.object(actions,'run_sync',new=AsyncMock(side_effect=TimeoutError())) as run:
            data={'target':'netease','name':'sample'}
            result=await actions.execute(data,'timed-out')
            self.assertFalse(result['ok']);self.assertEqual(result['state'],'unknown')
            await actions.execute(data,'timed-out');self.assertEqual(run.await_count,1)

    async def test_permission_revocation_during_song_resolution(self):
        async def resolve(_):
            account.update_permissions({'local_playlists':False});return [self.song]
        with patch.object(actions,'resolve_songs',side_effect=resolve):
            result=await actions.execute({'name':'sample','songs':[self.song]},'revoke',actor='aion',as_ai=True)
        self.assertFalse(result['ok']);self.assertEqual(await self.store.list_playlists(),[])

    async def test_malformed_or_example_commands_do_not_execute(self):
        block='[MUSIC_PLAYLIST]{"name":"sample"}[/MUSIC_PLAYLIST]'
        with patch.object(actions,'execute',new=AsyncMock()) as execute:
            text=await actions.process('```\n'+block+'\n```',actor='aion',source_type='private',source_id='c',message_id='m')
            execute.assert_not_awaited();self.assertNotIn('MUSIC_PLAYLIST',text)
            text=await actions.process('hello[MUSIC_PLAYLIST]{"songs":[',actor='aion',source_type='private',source_id='c',message_id='m')
            self.assertEqual(text,'hello')

    async def test_exact_song_resolution_refuses_ambiguous_versions(self):
        with patch.object(actions,'run_sync',new=AsyncMock(return_value=[self.song,{**self.song,'id':2}])):
            with self.assertRaises(ValueError):await actions.resolve_songs([{'name':'sample','artist':'artist'}])


class StreamTests(unittest.TestCase):
    def test_playlist_json_never_leaks_for_any_split(self):
        block='[MUSIC_PLAYLIST]{"songs":[{"name":"secret"}]}[/MUSIC_PLAYLIST]'
        for cls in [KnownCommandStreamFilter,WebCommandStreamFilter]:
            for cut in range(len(block)+1):
                stream=cls();result=stream.feed('before'+block[:cut])+stream.feed(block[cut:]+'after')
                result += stream.finish()[0] if cls is KnownCommandStreamFilter else stream.flush()
                self.assertEqual(result,'beforeafter',(cls,cut))
            stream=cls();result=''.join(stream.feed(char) for char in 'before'+block+'after')
            result += stream.finish()[0] if cls is KnownCommandStreamFilter else stream.flush()
            self.assertEqual(result,'beforeafter')


if __name__=='__main__':unittest.main()
