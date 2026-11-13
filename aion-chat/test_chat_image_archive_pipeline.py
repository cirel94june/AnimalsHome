import asyncio
import sqlite3
from unittest.mock import AsyncMock

import aiosqlite
import pytest

import config
import gift
import image_gen
from routes import chat,chatroom


def test_private_and_room_generation_archive_with_the_actual_sender(tmp_path,monkeypatch):
    path=tmp_path/'chat.db'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE conversations (id TEXT PRIMARY KEY, updated_at REAL); CREATE TABLE messages (id TEXT PRIMARY KEY,conv_id TEXT,role TEXT,content TEXT,created_at REAL,attachments TEXT); CREATE TABLE gifts (id TEXT PRIMARY KEY,image_path TEXT,message TEXT,created_at REAL,status TEXT,received_at REAL,sender TEXT); INSERT INTO conversations VALUES ("c",0);')
    root=tmp_path/'images';root.mkdir();(root/'image.png').write_bytes(b'stored-generated-image')
    monkeypatch.setattr(config,'ALBUM_IMAGES_DIR',root)
    for module in [chat,gift]:monkeypatch.setattr(module,'get_db',lambda:aiosqlite.connect(path))
    monkeypatch.setattr(chat,'export_conversation',AsyncMock())
    monkeypatch.setattr(chat.manager,'broadcast',AsyncMock())
    room_save=AsyncMock(return_value={'id':'room_img'})
    monkeypatch.setattr(chatroom,'_save_msg',room_save)
    generate=AsyncMock(return_value='album/image.png')
    monkeypatch.setattr(image_gen,'generate_image',generate)
    async def run():
        await chat._do_image_gen('c','trigger','code|a flower',False)
        await chatroom._chatroom_image_gen('room','ai3','banana|a flower',False)
    asyncio.run(run())
    assert [call.kwargs['source_identity'] for call in generate.await_args_list]==['aion','ai3']
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT sender,status FROM gifts ORDER BY sender').fetchall()==[('ai3','received'),('aion','received')]
        assert db.execute('SELECT role FROM messages').fetchone()[0]=='assistant'
    assert room_save.await_args.args[1]=='ai3'
