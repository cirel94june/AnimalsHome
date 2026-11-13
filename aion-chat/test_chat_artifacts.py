import asyncio
import sqlite3

import aiosqlite
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import actors
import chat_artifacts
import gift
import html_works
from routes import gift as gift_routes

DOC = '<!doctype html><html><head><title>一朵花</title></head><body><svg><circle cx="20" cy="20" r="10"/></svg></body></html>'


@pytest.fixture
def art(tmp_path, monkeypatch):
    path = tmp_path / 'chat.db'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE messages (id TEXT PRIMARY KEY,role TEXT,content TEXT); CREATE TABLE chatroom_messages (id TEXT PRIMARY KEY,sender TEXT,content TEXT); CREATE TABLE gifts (id TEXT PRIMARY KEY,image_path TEXT,message TEXT,created_at REAL,status TEXT,received_at REAL,sender TEXT);')
        db.execute('INSERT INTO messages VALUES (?,?,?)', ('main','assistant', '送给你\n```html\n'+DOC+'\n```'))
        db.execute('INSERT INTO messages VALUES (?,?,?)', ('human','user', '```html\n'+DOC+'\n```'))
        db.execute('INSERT INTO chatroom_messages VALUES (?,?,?)', ('seat','ai3', '```html\n'+DOC+'\n```'))
    connect = lambda: aiosqlite.connect(path)
    for module in [chat_artifacts,gift,gift_routes]: monkeypatch.setattr(module,'get_db',connect)
    monkeypatch.setattr(html_works,'DB_PATH',tmp_path/'works.db')
    monkeypatch.setattr(actors,'display_name',lambda actor: 'TEST_'+actor)
    import config
    images=tmp_path/'images'; images.mkdir()
    monkeypatch.setattr(config,'ALBUM_IMAGES_DIR',images)
    app=FastAPI(); app.include_router(chat_artifacts.router); app.include_router(gift_routes.router)
    return TestClient(app),path,images


def test_collects_actual_ai_message_and_is_idempotent(art):
    client,_,_=art
    first=client.post('/api/chat-artifacts/collect',headers={'X-AH-Request':'1'},json={'kind':'room','message_id':'seat','actor':'aion','html':'SPOOF'})
    assert first.status_code==200
    second=client.post('/api/chat-artifacts/collect',headers={'X-AH-Request':'1'},json={'kind':'room','message_id':'seat'})
    assert first.json()['work_id']==second.json()['work_id']
    works=html_works.list_works()
    assert len(works)==1 and works[0]['actor']=='ai3'
    assert html_works.get_work(works[0]['id'],False)['html']==DOC
    assert html_works.recent_count('ai3')==0
    gallery=client.get('/api/gift/list').json()['gifts']
    assert gallery[0]['kind']=='webpage' and gallery[0]['work_id']==works[0]['id']


@pytest.mark.parametrize('headers',[{}, {'X-AH-Request':'1','Origin':'https://evil.invalid'}, {'X-AH-Request':'1','Sec-Fetch-Site':'cross-site'}])
def test_cannot_be_collected_cross_site(art,headers):
    client,_,_=art
    assert client.post('/api/chat-artifacts/collect',headers=headers,json={'kind':'conv','message_id':'main'}).status_code==403


def test_human_message_and_unknown_sources_are_rejected(art):
    client,_,_=art
    assert client.post('/api/chat-artifacts/collect',headers={'X-AH-Request':'1'},json={'kind':'conv','message_id':'human'}).status_code==400
    assert client.post('/api/chat-artifacts/collect',headers={'X-AH-Request':'1'},json={'kind':'conv','message_id':'missing'}).status_code==404


def test_archive_chat_image_once_and_reject_path_escape(art):
    client,path,images=art
    (images/'x.png').write_bytes(b'test-png')
    async def run():
        first=await gift.archive_chat_image('album/x.png','ai3')
        assert first==await gift.archive_chat_image('album/x.png','ai3')
        with pytest.raises(ValueError): await gift.archive_chat_image('album/../chat.db','ai3')
        with pytest.raises(ValueError): await gift.archive_chat_image('album/x.png','unknown')
    asyncio.run(run())
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT sender,status,COUNT(*) FROM gifts').fetchone()==('ai3','received',1)
    assert client.get('/api/gift/list').json()['gifts'][0]['image_path']=='album/x.png'


def test_parser_supports_fragments_and_refuses_plain_code_and_oversize():
    assert chat_artifacts.extract_webpage('```svg\n<svg><text>hi</text></svg>\n```')
    assert chat_artifacts.extract_webpage('```html\n<div>hi</div>\n```')
    assert chat_artifacts.extract_webpage('```python\nprint(1)\n```') is None
    assert chat_artifacts.extract_webpage('```html\n<div>'+('x'*120001)+'</div>\n```') is None
