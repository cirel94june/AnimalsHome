"""Owner-configured HTTPS transport to the dedicated browser app."""
import json
import os
import re
import tempfile
import asyncio
from urllib.parse import urlsplit

import httpx
from config import DATA_DIR

PATH = DATA_DIR / 'xhs_remote.json'
OPERATIONS = {'status','start','cancel','frame','candidate','accept','credentials','worker'}
READS = {'check-login','search','list-feeds','get-feed-detail','user-profile','list-followings'}
LOCK=asyncio.Lock()


def validate(url, token, machine_id='', fly_token=''):
    if not isinstance(url,str) or re.search(r'[\x00-\x20\x7f\\]',url):
        raise ValueError('云浏览器地址无效。')
    parsed=urlsplit(url)
    if (parsed.scheme!='https' or not re.fullmatch(r'animalshome-xhs-[a-z0-9-]{3,35}\.fly\.dev',parsed.netloc)
        or parsed.path not in ('','/') or parsed.query or parsed.fragment):
        raise ValueError('请填写专用 Fly 应用的 HTTPS 地址。')
    if not isinstance(token,str) or not re.fullmatch(r'[a-f0-9]{64}',token):
        raise ValueError('云浏览器连接钥匙无效。')
    if not re.fullmatch(r'[a-f0-9]{12,32}',machine_id) or not isinstance(fly_token,str) or not 32<=len(fly_token)<=20000 or re.search(r'[\x00-\x20\x7f]',fly_token):
        raise ValueError('云浏览器启动权限无效。')
    return {'url':f'https://{parsed.netloc}','token':token,'machine_id':machine_id,'fly_token':fly_token}


def load():
    if not PATH.exists():return None
    try:
        data=json.loads(PATH.read_text(encoding='utf-8'))
        return validate(**data)
    except (OSError,ValueError,KeyError,TypeError):
        raise RuntimeError('云浏览器连接设置不可读，请重新连接。') from None


def save(**values):
    data=validate(**values)
    fd,name=tempfile.mkstemp(dir=PATH.parent,prefix='.xhs-remote-')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as output:
            json.dump(data,output);output.flush();os.fsync(output.fileno())
        os.replace(name,PATH)
    finally:
        if os.path.exists(name):os.unlink(name)


async def call(operation,data=None,*,connection=None):
    async with LOCK:
        return await _call_locked(operation,data,connection=connection)


async def _call_locked(operation,data=None,*,connection=None):
    if operation not in OPERATIONS:raise ValueError('unsupported_browser_operation')
    if operation=='worker' and (data or {}).get('endpoint') not in READS:
        raise ValueError('unsupported_read_operation')
    cfg=connection if connection is not None else load()
    if not cfg:raise RuntimeError('尚未连接云浏览器。')
    try:
        from xhs_fly_budget import ensure_started
        await ensure_started(cfg)
        async with httpx.AsyncClient(timeout=90,follow_redirects=False) as client:
            for attempt in range(10):
                try:
                    ready=await client.get(cfg['url']+'/health',timeout=3)
                    if ready.status_code==200:break
                except httpx.HTTPError:pass
                if attempt==9:raise RuntimeError('云浏览器启动尚未完成，请稍后重试。')
                await asyncio.sleep(1)
            async with client.stream('POST',cfg['url']+'/'+operation,
                headers={'Authorization':'Bearer '+cfg['token']},json=data or {}) as response:
                if response.status_code==423:raise RuntimeError('云浏览器已因用量保护暂停，请先检查预算。')
                response.raise_for_status()
                result=bytearray()
                async for chunk in response.aiter_bytes():
                    if len(result)+len(chunk)>8*1024*1024:raise ValueError('response_too_large')
                    result.extend(chunk)
        return bytes(result) if operation=='frame' else json.loads(result)
    except (httpx.HTTPError,ValueError,OSError):
        raise RuntimeError('云浏览器暂时无法连接，请稍后重试。') from None
