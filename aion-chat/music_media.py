"""Small, bounded first-party album-art delivery; no caller-supplied remote URL."""
from __future__ import annotations
import asyncio
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import httpx
from fastapi import HTTPException
from config import DATA_DIR

CACHE_DIR=DATA_DIR/'music_covers'
MAX_IMAGE_BYTES=2*1024*1024
MAX_CACHE_BYTES=32*1024*1024
MAX_CACHE_FILES=256
TTL=7*86400
_slots=asyncio.Semaphore(3)


def remote_cover(value):
    try:
        parsed=urlsplit(str(value or ''))
        host=(parsed.hostname or '').lower()
        if parsed.scheme not in {'http','https'} or not host.endswith('.music.126.net'):
            return ''
        if parsed.username or parsed.password or parsed.port not in (None,80,443):return ''
        if not parsed.path or parsed.path=='/':return ''
        query=dict(parse_qsl(parsed.query));query['param']='200y200'
        return urlunsplit(('https',host,parsed.path,urlencode(query),''))
    except (ValueError,TypeError):return ''


def image_type(data):
    if data.startswith(b'\xff\xd8\xff'):return 'image/jpeg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png'
    if data.startswith(b'RIFF') and data[8:12]==b'WEBP':return 'image/webp'
    return ''


def _read(song_id):
    path=CACHE_DIR/(str(song_id)+'.cover')
    try:
        stat=path.stat()
        if path.is_symlink() or not 0<stat.st_size<=MAX_IMAGE_BYTES or time.time()-stat.st_mtime>TTL:return None
        value=path.read_bytes();kind=image_type(value)
        return (value,kind) if kind else None
    except OSError:return None


def _save(song_id,data):
    CACHE_DIR.mkdir(parents=True,exist_ok=True)
    descriptor,temp=tempfile.mkstemp(prefix='.cover-',dir=CACHE_DIR)
    try:
        with os.fdopen(descriptor,'wb') as output:output.write(data)
        os.replace(temp,CACHE_DIR/(str(song_id)+'.cover'))
    finally:
        if os.path.exists(temp):os.unlink(temp)
    # Only this private cache's numeric art files are candidates for eviction.
    files=[]
    for path in CACHE_DIR.glob('*.cover'):
        if re.fullmatch(r'\d+\.cover',path.name) and not path.is_symlink():
            try:files.append((path.stat().st_mtime,path.stat().st_size,path))
            except OSError:pass
    total=sum(size for _,size,_ in files);count=len(files)
    for _,size,path in sorted(files):
        if count<=MAX_CACHE_FILES and total<=MAX_CACHE_BYTES:break
        try:path.unlink();total-=size;count-=1
        except OSError:pass


def _detail_cover(song_id):
    # Public album metadata does not need to switch or refresh the music account.
    from netease_account import _session
    from pyncm.apis.track import GetTrackDetail
    with _session([]):value=GetTrackDetail([song_id])
    songs=value.get('songs',[]) if isinstance(value,dict) else []
    song=next((s for s in songs if str(s.get('id'))==str(song_id)),{})
    return remote_cover((song.get('al') or song.get('album') or {}).get('picUrl'))


async def get_cover(song_id):
    if isinstance(song_id,bool) or not isinstance(song_id,int) or not 0<song_id<=9007199254740991:
        raise HTTPException(422,'歌曲编号无效。')
    cached=await asyncio.to_thread(_read,song_id)
    if cached:return cached
    try:
        await asyncio.wait_for(_slots.acquire(),timeout=20)
    except TimeoutError:raise HTTPException(503,'封面暂时繁忙，请稍后再试。') from None
    try:
        cached=await asyncio.to_thread(_read,song_id)
        if cached:return cached
        from music_actions import run_sync
        url=remote_cover(await run_sync(_detail_cover,song_id))
        if not url:raise HTTPException(404,'这首歌暂无封面。')
        async with httpx.AsyncClient(timeout=httpx.Timeout(12,connect=5),follow_redirects=False) as client:
            async with client.stream('GET',url,headers={'Referer':'https://music.163.com/','Accept':'image/jpeg,image/png,image/webp'}) as response:
                if response.status_code!=200:raise HTTPException(502,'封面暂时无法读取。')
                data=bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data)+len(chunk)>MAX_IMAGE_BYTES:raise HTTPException(502,'封面文件过大。')
                    data.extend(chunk)
        data=bytes(data);kind=image_type(data)
        if not kind:raise HTTPException(502,'封面格式暂不支持。')
        try:await asyncio.to_thread(_save,song_id,data)
        except OSError:pass # An unavailable art cache must not break the image.
        return data,kind
    except HTTPException:raise
    except Exception:raise HTTPException(502,'封面暂时无法读取。') from None
    finally:_slots.release()


def share_text(song):
    def clean(value):return re.sub(r'[\x00-\x1f\[\]［］【】<>`]','',str(value or ''))[:160]
    return '🎵 分享音乐：《'+clean(song.get('name'))+'》 — '+clean(song.get('artist'))
