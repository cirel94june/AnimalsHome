"""
音乐路由：搜索 + 获取歌曲信息 + 代理推流
"""

import re
from fastapi import APIRouter, Query, Path, HTTPException
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel

import httpx

from music import search_songs, get_song_detail, get_audio_url

router = APIRouter()

MUSIC_CMD_PATTERN = re.compile(r"\[MUSIC:(.+?)\]")


@router.get('/api/music/cover/{song_id}')
async def music_cover(song_id: int=Path(gt=0,le=9007199254740991)):
    from music_media import get_cover
    value,kind=await get_cover(song_id)
    return Response(value,media_type=kind,headers={'Cache-Control':'private, max-age=86400',
        'X-Content-Type-Options':'nosniff'})


@router.get('/api/music/share/{song_id}')
async def music_share(song_id: int=Path(gt=0,le=9007199254740991)):
    from music_actions import run_sync
    from music_media import share_text
    try:info=await run_sync(get_song_detail,song_id)
    except HTTPException:raise
    except Exception:raise HTTPException(502,'歌曲资料暂时无法读取，请稍后再试。') from None
    if not info or str(info.get('id'))!=str(song_id):raise HTTPException(404,'没有找到这首歌。')
    song={'type':'music','id':song_id,'name':str(info.get('name') or '')[:160],
          'artist':str(info.get('artist') or '')[:160],'album':str(info.get('album') or '')[:160],
          'cover':f'/api/music/cover/{song_id}','shared_by':'user'}
    return {'song':song,'text':share_text(song)}


@router.get("/api/music/search")
async def music_search(q: str = Query(..., min_length=1, max_length=200), limit: int = Query(5, ge=1, le=20)):
    """搜索歌曲"""
    results = search_songs(q, limit=limit)
    return {"songs": results}


@router.get("/api/music/detail/{song_id}")
async def music_detail(song_id: int):
    """获取歌曲详情"""
    info = get_song_detail(song_id)
    if not info:
        return {"error": "歌曲不存在"}
    # 尝试获取在线播放 URL
    info["audio_url"] = get_audio_url(song_id)
    return info


class MusicPlayRequest(BaseModel):
    keyword: str


@router.post("/api/music/play")
async def music_play(body: MusicPlayRequest):
    """AI 点歌：搜索并返回第一个结果的完整信息"""
    results = search_songs(body.keyword, limit=5)
    if not results:
        return {"error": "没有找到相关歌曲", "keyword": body.keyword}
    song = results[0]
    song["audio_url"] = get_audio_url(song["id"])
    song["candidates"] = results[1:]  # 备选
    return song


@router.get("/api/music/stream/{song_id}")
async def music_stream(song_id: int):
    """代理推流：后端实时获取网易云 CDN URL 并转发音频流给前端"""
    url = get_audio_url(song_id)
    if not url:
        return Response(content='{"error":"无法获取播放地址，可能是VIP歌曲且未登录"}',
                        status_code=404, media_type="application/json")

    async def _stream():
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            async with client.stream("GET", url, headers={
                "Referer": "https://music.163.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }) as resp:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    yield chunk

    # 猜测 Content-Type
    ct = "audio/mpeg"
    if ".m4a" in url or ".aac" in url:
        ct = "audio/mp4"
    elif ".flac" in url:
        ct = "audio/flac"

    return StreamingResponse(_stream(), media_type=ct, headers={
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-cache",
    })
