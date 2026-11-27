import asyncio
import time
from unittest.mock import AsyncMock
import aiosqlite
import pytest
import moment_feed as f
import routes.moments as m

@pytest.fixture
def db(tmp_path,monkeypatch):
    path=tmp_path/"test.db"
    async def setup():
        async with aiosqlite.connect(path) as c:
            await c.executescript("""
            CREATE TABLE moments(id TEXT PRIMARY KEY,author TEXT,content TEXT,expect_reply INTEGER,created_at REAL);
            CREATE TABLE moment_comments(id TEXT PRIMARY KEY,moment_id TEXT,author TEXT,content TEXT,reply_to_id TEXT,created_at REAL);
            CREATE TABLE moment_reactions(id TEXT PRIMARY KEY,moment_id TEXT,author TEXT,type TEXT,created_at REAL,UNIQUE(moment_id,author));
            CREATE TABLE chatroom_rooms(id TEXT PRIMARY KEY,type TEXT,updated_at REAL);
            INSERT INTO chatroom_rooms VALUES('g','group',1),('p','seat_1v1',2),('billiards_x','group',3);
            """)
            now=time.time()
            await c.executemany("INSERT INTO moments VALUES(?,?,?,?,?)",[
                ("x","ai3","public joke",0,now),("y","connor","public news",0,now-1),
                ("z","ai3","another post",0,now-2),("self","aion","own post",0,now),
                ("invite","ai3","invite",1,now),("user","user","user",0,now),
                ("old","ai3","old",0,now-3*86400)])
            await c.commit()
    asyncio.run(setup())
    monkeypatch.setattr(f,"get_db",lambda:aiosqlite.connect(path))
    monkeypatch.setattr(m,"get_db",lambda:aiosqlite.connect(path))
    monkeypatch.setattr("capabilities.is_capability_enabled",lambda k:True)
    monkeypatch.setattr("actors.get_actor",lambda a:{"enabled":True})
    monkeypatch.setattr("sync_events.broadcast_synced",AsyncMock())
    return path

def test_feed_bounded_and_independent_of_reply_invite(db):
    async def run():
        rows=await f.load_feed("aion")
        assert [r["id"] for r in rows]==["x","y","z"]
        assert "public joke" in f.prompt_for(rows,lambda x:x)
        assert f.select_reviews(rows,{}) is None
        reviews=f.select_reviews(rows,{"moment_reactions":[
            {"id":"x","like":True,"comment":"哈哈"},{"id":"y","comment":"不错"},
            {"id":"z","like":True},{"id":"self","like":True}]})
        assert reviews["z"]=={"like":False,"comment":""}
        await f.apply_reviews("aion",reviews)
        await f.apply_reviews("aion",reviews)
        assert await f.load_feed("aion")==[]
        async with aiosqlite.connect(db) as c:
            assert (await (await c.execute("SELECT count(*) FROM moment_comments")).fetchone())[0]==2
            assert (await (await c.execute("SELECT count(*) FROM moment_reactions")).fetchone())[0]==1
            assert (await (await c.execute("SELECT count(*) FROM moment_feed_reviews")).fetchone())[0]==3
    asyncio.run(run())

def test_explicit_pass_records_no_comment_or_like(db):
    async def run():
        rows=await f.load_feed("aion")
        await f.apply_reviews("aion",f.select_reviews(rows,{"moment_reactions":[]}))
        assert await f.load_feed("aion")==[]
        async with aiosqlite.connect(db) as c:
            assert (await (await c.execute("SELECT count(*) FROM moment_comments")).fetchone())[0]==0
            assert (await (await c.execute("SELECT count(*) FROM moment_reactions")).fetchone())[0]==0
    asyncio.run(run())

def test_group_notice_routing_without_private_source_id(db,monkeypatch):
    note=AsyncMock()
    monkeypatch.setattr("routes.chatroom._chatroom_sys_msg",note)
    monkeypatch.setattr("routes.chatroom._name_for_identity",lambda a:a)
    q=asyncio.Queue()
    asyncio.run(f.notify_family("ai3",room_id="g",msg_id="public",queue=q))
    note.assert_awaited_with("g","📷 ai3发了一条朋友圈",q,after_msg_id="public")
    asyncio.run(f.notify_family("connor",room_id="p",msg_id="private",queue=q))
    note.assert_awaited_with("g","📷 connor发了一条朋友圈",None,after_msg_id=None)

def test_peer_reaction_optional():
    assert m._peer_reaction("[PASS]")==("",False)
    assert m._peer_reaction("[LIKE]")==("",True)
    assert m._peer_reaction("[LIKE] 真棒")==("真棒",True)
    assert m._peer_reaction("哈哈")==("哈哈",False)
