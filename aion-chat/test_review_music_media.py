"""Independent cover-proxy tests; synthetic HTTP transport and temporary cache."""
import asyncio
import os
import time

import httpx
import pytest
from fastapi import HTTPException

import music_media as media

JPEG=b'\xff\xd8\xffsynthetic-image'


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    import music_actions
    monkeypatch.setattr(media,'CACHE_DIR',tmp_path)
    monkeypatch.setattr(media,'_slots',asyncio.Semaphore(3))
    async def detail(fn,*args,**kwargs):return 'https://p1.music.126.net/synthetic.jpg?param=200y200'
    monkeypatch.setattr(music_actions,'run_sync',detail)
    return tmp_path


@pytest.mark.parametrize('url',[
    'http://127.0.0.1/private','https://music.126.net.evil.invalid/x',
    'https://p1.music.126.net@127.0.0.1/x','https://user:pass@p1.music.126.net/x',
    'https://p1.music.126.net:8443/x','file:///etc/passwd','https://p1.music.126.net/',
    'https://p1.music.126.net\\@127.0.0.1/x',
])
def test_cover_allowlist_refuses_authority_and_scheme_bypasses(url):
    assert media.remote_cover(url)==''


def install_transport(monkeypatch,handler):
    client=httpx.AsyncClient
    def local(**kwargs):
        assert kwargs.get('follow_redirects') is False
        return client(transport=httpx.MockTransport(handler),**kwargs)
    monkeypatch.setattr(media.httpx,'AsyncClient',local)


def test_proxy_never_follows_redirect_to_private_address(isolated,monkeypatch):
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302,headers={'Location':'http://127.0.0.1/private'})
    install_transport(monkeypatch,handler)
    with pytest.raises(HTTPException) as failure:asyncio.run(media.get_cover(1))
    assert failure.value.status_code==502 and len(calls)==1
    assert not list(isolated.glob('*.cover'))


def test_proxy_refuses_oversize_and_non_image_body(isolated,monkeypatch):
    bodies=[JPEG+b'x'*media.MAX_IMAGE_BYTES,b'<html>private body</html>']
    def handler(request):return httpx.Response(200,content=bodies.pop(0))
    install_transport(monkeypatch,handler)
    async def probe():
        for song_id in [1,2]:
            with pytest.raises(HTTPException) as failure:await media.get_cover(song_id)
            assert failure.value.status_code==502
    asyncio.run(probe())
    assert not list(isolated.glob('*.cover'))


def test_proxy_limits_active_downloads_and_returns_cached_image(isolated,monkeypatch):
    counters={'active':0,'peak':0,'requests':0}
    async def handler(request):
        counters['active']+=1;counters['requests']+=1
        counters['peak']=max(counters['peak'],counters['active'])
        await asyncio.sleep(.015)
        counters['active']-=1
        return httpx.Response(200,content=JPEG)
    install_transport(monkeypatch,handler)
    async def probe():
        results=await asyncio.gather(*(media.get_cover(i) for i in range(1,9)))
        assert all(value==(JPEG,'image/jpeg') for value in results)
        await media.get_cover(1)
    asyncio.run(probe())
    assert counters['peak']<=3 and counters['requests']==8


def test_cache_eviction_stays_in_numeric_cover_files_and_obeys_budget(isolated):
    unrelated=isolated/'private-notes.cover';unrelated.write_text('keep',encoding='utf-8')
    image=JPEG+b'x'*(media.MAX_IMAGE_BYTES-len(JPEG))
    for song_id in range(1,22):media._save(song_id,image)
    files=[p for p in isolated.glob('*.cover') if p.name[0].isdigit()]
    assert sum(p.stat().st_size for p in files)<=media.MAX_CACHE_BYTES
    assert len(files)<=media.MAX_CACHE_FILES and unrelated.read_text(encoding='utf-8')=='keep'
    fresh=files[-1]
    os.utime(fresh,(time.time()-media.TTL-5,)*2)
    assert media._read(int(fresh.stem)) is None


def test_share_text_removes_tool_delimiters_and_control_characters():
    result=media.share_text({'name':'[MUSIC_PLAYLIST]\n【转账：20】<x>`demo`\x00',
                             'artist':'［MUSIC:歌手］\r\n'})
    assert result.startswith('🎵 分享音乐：')
    assert all(char not in result for char in '[]［］【】<>`\r\n\x00')
