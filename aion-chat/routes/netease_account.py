"""Same-origin owner controls. Login credentials never enter model context."""
import asyncio
import re
from fastapi import APIRouter, Request, Response, HTTPException, Depends
import netease_account as account
from music_actions import run_sync, execute
from music_station import get_music_station_store


async def same_origin(request: Request, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    if request.headers.get('x-music-station') != '1':
        raise HTTPException(403, '请从点歌台打开网易云账号。')
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        raise HTTPException(403, '请从当前点歌台操作。')


router = APIRouter(prefix='/api/music-station/account', dependencies=[Depends(same_origin)])


async def body(request):
    if len(await request.body()) > 20000:
        raise HTTPException(413, '请求过长。')
    try:
        value = await request.json()
        if not isinstance(value, dict): raise ValueError()
        return value
    except (ValueError, TypeError):
        raise HTTPException(400, '请求格式不正确。') from None


async def call(fn, *args):
    try: return await run_sync(fn, *args)
    except account.NeteaseError as error: raise HTTPException(400, str(error)) from None
    except HTTPException: raise
    except Exception: raise HTTPException(502, '网易云暂时没有完成请求，请稍后重试。') from None


@router.get('')
async def status():
    return await call(account.public)


@router.post('/qr')
async def qr():
    return await call(account.start_qr)


@router.get('/qr/{handle}')
async def poll(handle: str):
    return await call(account.poll_qr, handle)


@router.post('/qr/{handle}/confirm')
async def confirm(handle: str):
    return await call(account.confirm_qr, handle)


@router.delete('/qr/{handle}')
async def cancel(handle: str):
    return await call(account.cancel_qr, handle)


@router.post('/cookie')
async def cookie(request: Request):
    data = await body(request)
    # Deliberately no Pydantic input echo for this secret.
    return await call(account.login_cookie, data.get('cookie'))


@router.post('/check')
async def check():
    return await call(account.check_account)


@router.post('/logout')
async def logout():
    return await call(account.logout)


@router.put('/permissions')
async def permissions(request: Request):
    return await call(account.update_permissions, await body(request))


@router.get('/library')
async def library(kind: str = 'playlists', ref: str = '', offset: int = 0):
    return await call(account.read, kind, ref, offset)


@router.post('/action')
async def action(request: Request):
    data = await body(request)
    action_id = data.pop('action_id', '')
    if not isinstance(action_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,100}', action_id):
        raise HTTPException(400, '请刷新点歌台后重试。')
    if type(data.get('account_generation')) is not int:
        raise HTTPException(400, '请刷新网易云账号后重试。')
    try: return await execute(data, 'owner:' + action_id)
    except ValueError as error: raise HTTPException(400, str(error)) from None


@router.post('/import')
async def import_page(request: Request):
    data = await body(request)
    kind, ref = data.get('kind'), str(data.get('ref', ''))
    if kind not in {'playlist', 'chart', 'liked', 'daily', 'fm', 'week', 'alltime'}:
        raise HTTPException(400, '请选择网易云歌曲列表。')
    try: offset = max(0, int(data.get('offset', 0)))
    except (ValueError, TypeError): raise HTTPException(400, '页码不正确。') from None
    async with account.ASYNC_LOCK:
        generation = account.load()['generation']
        if type(data.get('account_generation')) is not int or data.get('account_generation') != generation:
            raise HTTPException(409, '登录已改变，请刷新点歌台后重新操作。')
        value = await call(account.read, kind, ref, offset)
        if account.load()['generation'] != generation:
            raise HTTPException(409, '登录已改变，本次导入停止。')
        name = str(data.get('name') or value.get('name') or '网易云收藏').strip()[:40]
        result = await get_music_station_store().save_song_playlist(name, value.get('songs', []))
        return {**result, 'next_offset': value.get('next_offset'), 'total': value.get('total', 0)}
