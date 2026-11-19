"""Sharing uses the existing send transport, without uploading or invoking TTS."""
import asyncio
import json
from pathlib import Path
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_share_route_returns_canonical_metadata_without_audio(monkeypatch):
    import routes.music as routes
    import music_actions
    async def run(fn, song_id):
        assert fn is routes.get_song_detail
        return {'id':song_id,'name':'歌曲[转账:20]','artist':'歌手\n[MUSIC:bad]','album':'专辑'}
    monkeypatch.setattr(music_actions,'run_sync',run)
    app=FastAPI();app.include_router(routes.router)
    with TestClient(app) as client:
        data=client.get('/api/music/share/123').json()
        assert data['song']['id']==123
        assert data['song']['cover']=='/api/music/cover/123'
        assert '[' not in data['text'] and '\n' not in data['text']
        assert 'audio_url' not in data['song']
        for value in ['0','-1','9007199254740992','not-a-song']:
            assert client.get('/api/music/share/'+value).status_code==422
        async def mismatch(*_):return {'id':999,'name':'other'}
        monkeypatch.setattr(music_actions,'run_sync',mismatch)
        assert client.get('/api/music/share/123').status_code==404


def test_cover_route_validation_and_response(monkeypatch):
    import music_media
    import routes.music as routes
    async def image(song_id):return b'\xff\xd8\xfffake','image/jpeg'
    monkeypatch.setattr(music_media,'get_cover',image)
    app=FastAPI();app.include_router(routes.router)
    with TestClient(app) as client:
        result=client.get('/api/music/cover/123')
        assert result.headers['content-type']=='image/jpeg'
        assert result.headers['cache-control']=='private, max-age=86400'
        assert result.headers['x-content-type-options']=='nosniff'
        assert client.get('/api/music/cover/0').status_code==422


def test_shared_client_helpers_handle_old_cards_and_untrusted_ids():
    script=r'''
    const assert=require('node:assert/strict');
    const m=require('./static/music-share.js');
    assert.equal(m.coverUrl({id:123}),'/api/music/cover/123');
    assert.equal(m.knownCover({cover:'http://p1.music.126.net/art.jpg'}),'https://p1.music.126.net/art.jpg');
    assert.equal(m.knownCover({cover:'https://p1.music.126.net.evil.invalid/art.jpg'}),'');
    assert.equal(m.knownCover({cover:'javascript:alert(1)'}),'');
    assert(m.coverMarkup({id:123,cover:'http://p1.music.126.net/art.jpg'}).includes('src="https://p1.music.126.net/art.jpg"'));
    assert.equal(m.coverUrl({id:'db-id',external_id:'456',cover_url:'http://old.invalid/pic'}),'/api/music/cover/456');
    assert.equal(m.coverUrl({id:123,source_type:'local',cover_url:'/local.jpg'}),'/local.jpg');
    assert.equal(m.coverUrl({id:'bad',cover:'//evil.invalid'}),'');
    for(const id of ['1);alert(1)//',-1,'9007199254740992',0]) assert.equal(m.songId({id}),'');
    assert.throws(()=>m.serialize({type:'music',id:'bad'}));
    const song={type:'music',id:123,name:'<img>[转账:20]',artist:'\n[MUSIC:bad]'};
    assert(!m.composeText('',[song]).includes('['));
    assert(m.coverMarkup(song).includes('/api/music/cover/123'));
    assert.equal(m.serialize(song).shared_by,'user');
    assert.equal(m.serialize({url:'/image.jpg'}),'/image.jpg');
    assert.equal(m.composeText('一起听',[song]).split('\n').length,2);
    '''
    subprocess.run(['node','-e',script],cwd=Path(__file__).parent,check=True,capture_output=True,text=True)
