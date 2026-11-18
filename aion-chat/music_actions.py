"""Playlist commands shared by private/group/seat replies; writes are never replayed."""
from __future__ import annotations

import asyncio
from functools import partial
import hashlib
import json
import re

MUSIC_PLAYLIST_PATTERN=re.compile(r'\[MUSIC_PLAYLIST\]([\s\S]*?)\[/MUSIC_PLAYLIST\]',re.I)
_EXAMPLES=re.compile(r'```[\s\S]*?(?:```|\Z)|~~~[\s\S]*?(?:~~~|\Z)|`[^`\n]*`|^>[^\n]*',re.M)
ACTORS={'aion','connor','ai3','ai4','ai5','ai6'}


async def run_sync(fn,*args,**kwargs):
    # Reuse the station's bounded pool, including its late-completion accounting.
    from routes.music_station import _run_netease
    return await _run_netease(partial(fn,*args,**kwargs))


def _clean(value):
    return re.sub(r'[\r\n\[\]<>`]','',str(value))[:120]


def _norm(value):return re.sub(r'\s+','',str(value or '')).casefold()


async def resolve_songs(specs):
    from music import search_songs,get_song_detail
    if not isinstance(specs,list) or len(specs)>10:raise ValueError('每个歌单操作最多指定10首歌。')
    result=[];seen=set()
    for spec in specs:
        if not isinstance(spec,dict):raise ValueError('歌曲要包含歌名和歌手。')
        name=str(spec.get('name') or '').strip();artist=str(spec.get('artist') or '').strip()
        if not name or len(name)>160 or len(artist)>160:raise ValueError('请提供明确的歌名和歌手。')
        if spec.get('id'):
            song_id=int(spec['id'])
            if song_id<=0 or song_id>2**53:raise ValueError('歌曲编号不正确。')
            found=await run_sync(get_song_detail,song_id)
            candidates=[found] if found else []
        else:
            if not artist:raise ValueError('为避免加错同名歌，请同时写出歌手。')
            candidates=await run_sync(search_songs,name+' '+artist,10)
        matches={int(s['id']):s for s in candidates if _norm(s.get('name'))==_norm(name)
                 and (not artist or _norm(artist) in {_norm(s.get('artist')), *(_norm(a) for a in s.get('artists',[]))})
                 and (not spec.get('album') or _norm(spec['album'])==_norm(s.get('album')))}
        if len(matches)!=1:raise ValueError(f'《{_clean(name)}》未找到唯一版本，请在点歌台选择具体歌曲。')
        song=next(iter(matches.values()))
        if int(song['id']) not in seen:result.append(song);seen.add(int(song['id']))
    return result


def _allowed(cfg,target,op,as_ai):
    if target not in {'local','netease'} or op not in {'create','add','like'}:
        raise ValueError('歌单操作不正确。')
    if target=='local' and op=='like':raise ValueError('红心只用于网易云歌曲。')
    if as_ai:
        from capabilities import is_capability_enabled
        if not is_capability_enabled('music'):raise ValueError('点歌能力已关闭，本次未执行。')
        permission='local_playlists' if target=='local' else 'cloud_likes' if op=='like' else 'cloud_playlists'
        if not cfg['permissions'].get(permission):raise ValueError('主人尚未在点歌台开启这项写入权限。')


async def execute(data,action_id,*,actor='user',actor_name='我',as_ai=False,resolved_songs=None,personal_actor=None):
    import netease_account as account
    from music_station import get_music_station_store
    if not isinstance(data,dict):raise ValueError('歌单指令格式不正确。')
    if as_ai and actor not in ACTORS:raise ValueError('无法确认点歌身份。')
    if personal_actor is not None and (not as_ai or personal_actor!=actor):raise ValueError('个人歌单身份不一致。')
    if data.get('op')=='personal':
        if not as_ai:raise ValueError('个人收歌需要由已确认的 AI 身份发起。')
        from capabilities import is_capability_enabled
        if not is_capability_enabled('music'):raise ValueError('AI 点歌已关闭。')
        from music_personal import add
        generation=account.load()['generation']
        songs=await resolve_songs(data.get('songs',[]))
        if not songs:raise ValueError('请先选择歌曲。')
        return await add(songs,actor,action_id,generation=generation)
    target=data.get('target','local');op=data.get('op','create')
    name=str(data.get('name') or '').strip();playlist_id=str(data.get('playlist_id') or '').strip()
    if op=='create' and not 1<=len(name)<=40:raise ValueError('歌单名称需要1–40个字符。')
    if op=='add' and not (playlist_id or name):raise ValueError('请指定要加入的歌单。')
    store=get_music_station_store()
    requested_account=account.load()
    async with account.ASYNC_LOCK:
        cfg=account.load()
        if (cfg['generation'],cfg['user_id']) != (requested_account['generation'],requested_account['user_id']):
            return {'ok':False,'state':'failed','message':'等待期间登录已改变，本次操作未执行，请刷新点歌台。'}
        _allowed(cfg,target,op,as_ai)
        if 'account_generation' in data and data['account_generation']!=cfg['generation']:
            raise ValueError('登录已改变，请刷新点歌台后重新操作。')
        if target=='netease' and not cfg['user_id']:raise ValueError('请先在点歌台登录网易云账号。')
        fingerprint=hashlib.sha256(json.dumps({'data':data,'account':cfg['user_id'],
                                  'generation':cfg['generation'],'actor':actor,'as_ai':as_ai},
                                  ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        previous=await store.claim_music_action(action_id,fingerprint)
        if previous:
            return previous if previous.get('message') else {'ok':False,'state':'pending',
                'message':'这项操作正在确认或尚未确认结果，请先查看歌单，暂不重复执行。'}
        try:
            async with asyncio.timeout(90):
                songs=resolved_songs if resolved_songs is not None else await resolve_songs(data.get('songs',[]))
                if op!='create' and not songs:raise ValueError('请先选择要添加的歌曲。')
                if account.load()['generation']!=cfg['generation']:
                    raise ValueError('账号或登录已改变，本次操作停止。')
                _allowed(account.load(),target,op,as_ai)
                if target=='local':
                    if op=='add' and not playlist_id:
                        matches=[p for p in await store.list_playlists() if _norm(p['name'])==_norm(name)]
                        if len(matches)!=1:raise ValueError('找不到唯一的点歌台歌单，请使用准确名称。')
                        playlist_id=matches[0]['id']
                    value=await store.save_song_playlist(name,songs,playlist_id=playlist_id,
                                actor=actor,actor_name=actor_name,action_id=action_id,record_requests=as_ai)
                    value['message']=f'点歌台歌单《{_clean(value["name"])}》已保存，新增{value["added"]}首歌。'
                else:
                    if op=='add' and not playlist_id:
                        playlists=(await run_sync(account.read,'playlists'))['playlists']
                        matches=[p for p in playlists if p['owned'] and _norm(p['name'])==_norm(name)]
                        if len(matches)!=1:raise ValueError('找不到唯一的自建网易云歌单，请在点歌台确认。')
                        playlist_id=matches[0]['id']
                    value=await run_sync(account.mutate,op,name,playlist_id,[s['id'] for s in songs],
                                         as_ai=as_ai,expected_generation=cfg['generation'],
                                         **({'personal_actor':personal_actor} if personal_actor else {}))
                    value['message']=_clean(value['message'])
            await store.finish_music_action(action_id,'succeeded',value)
            return value
        except asyncio.CancelledError:
            await asyncio.shield(store.finish_music_action(action_id,'unknown',{'ok':False,'message':'操作结果尚未确认，请先刷新歌单，暂不重复执行。'}))
            raise
        except Exception as error:
            from fastapi import HTTPException
            unknown=target=='netease' or isinstance(error,(TimeoutError,HTTPException))
            detail=str(error) if isinstance(error,(ValueError,account.NeteaseError)) else '歌曲服务暂时没有完成请求。'
            value={'ok':False,'state':'unknown' if unknown else 'failed','message':_clean(detail)+
                   (' 请先刷新歌单确认结果，暂不重复提交。' if unknown else '')}
            await store.finish_music_action(action_id,value['state'],value)
            return value


async def process(text,*,actor,source_type,source_id,message_id,actor_name='AI'):
    text=text or ''
    if '[MUSIC_PLAYLIST]' not in text.upper():return text
    examples=[m.span() for m in _EXAMPLES.finditer(text)]
    matches=list(MUSIC_PLAYLIST_PATTERN.finditer(text))
    receipts=[]
    for index,match in enumerate(matches[:2]):
        if any(start<=match.start()<end for start,end in examples):continue
        try:
            if len(match[1])>4000:raise ValueError('歌单指令过长，本次没有执行。')
            data=json.loads(match[1])
            key=hashlib.sha256(json.dumps([source_type,source_id,message_id,actor,index],ensure_ascii=False).encode()).hexdigest()
            value=await execute(data,key,actor=actor,actor_name=actor_name,as_ai=True)
            receipts.append('🎵 '+value['message'])
        except Exception:receipts.append('🎵 歌单指令未完成，请检查点歌台中的权限、歌名和歌手。')
    cleaned=MUSIC_PLAYLIST_PATTERN.sub('',text)
    cleaned=re.sub(r'\[MUSIC_PLAYLIST\][\s\S]*$', '', cleaned, flags=re.I).strip()
    return (cleaned+'\n\n'+'\n'.join(receipts)).strip()


def ability_text():
    import netease_account
    try:cfg=netease_account.load()
    except RuntimeError:return '歌单设置暂时不可读，请不要执行歌单写入。'
    enabled=[]
    if cfg['permissions']['local_playlists']:enabled.append('local：点歌台歌单')
    if cfg['user_id'] and cfg['permissions']['cloud_playlists']:enabled.append('netease：网易云自建歌单')
    likes=bool(cfg['user_id'] and cfg['permissions']['cloud_likes'])
    if not enabled and not likes:return '歌单写入权限未开启，只可正常推荐歌曲。'
    return ('\n歌单写入（以系统实际回执为准，不能提前声称成功）：'+ '；'.join(enabled)+
            '。新建或加歌用独立块 [MUSIC_PLAYLIST]{"target":"local","op":"create",'
            '"name":"晚安","songs":[{"name":"歌名","artist":"歌手"}]}[/MUSIC_PLAYLIST]。'
            'target只能选已开放的local/netease；op=create或add，add须给准确name或已知playlist_id。'
            '每个块最多10首、每条回复最多2块；同名版本不唯一时补album，不能编造歌曲ID。'
            + ('红心已开放，可用target=netease、op=like、songs给歌曲点红心。' if likes else '红心写入未开放。')+
            '只想加入自己的歌单而不点播，用[MUSIC_PLAYLIST]{"op":"personal","songs":[{"id":真实歌曲ID,"name":"真实歌名","artist":"歌手"}]}[/MUSIC_PLAYLIST]。'
            'personal由系统按你的身份选择自己的歌单，不要代别的AI收歌。'
            '普通点歌[MUSIC:歌名 歌手]会自动收进自己的歌单，不要为同一首歌额外重复发personal。指令放在代码块外。')
