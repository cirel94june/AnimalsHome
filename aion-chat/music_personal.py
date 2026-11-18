"""Each companion's own playlist; NetEase writes retain the owner's existing opt-in."""
from __future__ import annotations
import hashlib
import json
import logging
import netease_account as account

log=logging.getLogger(__name__)


def profiles():
    import actors
    cfg=account.load()
    enabled=[a for a in actors.list_actors() if a.get('enabled')]
    bases=[(a['name']+'的歌单')[:40].casefold() for a in enabled]
    return [{'actor':a['id'],'name':a['name'],'playlist_name':((a['name']+'的歌单')[:30]+'·'+a['id']
                if bases.count((a['name']+'的歌单')[:40].casefold())>1 else (a['name']+'的歌单')[:40]),
             'cloud_playlist_id':cfg['actor_playlists'].get(a['id'],''),
             'status':cfg['personal_status'].get(a['id'],{})}
            for a in enabled]


def bind(actor,playlist_id,generation):
    with account.LOCK:
        cfg=account.load()
        if type(generation) is not int or generation!=cfg['generation']:
            raise account.NeteaseError('登录已改变，请刷新后再设置。')
        if actor not in {p['actor'] for p in profiles()}:
            raise account.NeteaseError('请选择已启用的 AI。')
        playlist_id=str(playlist_id or '')
        if playlist_id:
            owned={p['id'] for p in account.read('playlists')['playlists'] if p['owned'] and not p['liked']}
            if playlist_id not in owned:raise account.NeteaseError('只能选择当前账号自己创建的普通歌单。')
            cfg=account.load()
            if cfg['generation']!=generation:raise account.NeteaseError('登录已改变，请刷新后再设置。')
            if any(a!=actor and p==playlist_id for a,p in cfg['actor_playlists'].items()):
                raise account.NeteaseError('这张歌单已分给另一位 AI，请分别选择。')
        cfg['actor_playlists'][actor]=playlist_id
        cfg['generation']+=1
        account._save(cfg)
        return {'profiles':profiles(),'account':account.public()}


def _status(actor,generation,value):
    import time
    with account.LOCK:
        cfg=account.load()
        if cfg['generation']!=generation:return
        cfg['personal_status'][actor]={'ok':bool(value.get('ok')),
            'message':str(value.get('message') or '')[:180],'updated_at':time.time()}
        account._save(cfg)


async def add(songs,actor,action_id,*,generation=None,cloud=True):
    from capabilities import is_capability_enabled
    from music_station import get_music_station_store
    from music_actions import execute
    cfg=account.load()
    profile=next((p for p in profiles() if p['actor']==actor),None)
    if not profile or not is_capability_enabled('music'):
        return {'ok':False,'message':'AI 点歌已关闭，本次未收歌。'}
    if generation is not None and generation!=cfg['generation']:
        return {'ok':False,'message':'登录已改变，本次未收歌。'}
    messages=[]
    if cfg['permissions']['local_playlists']:
        def allowed():
            current=account.load()
            return (current['generation']==cfg['generation'] and is_capability_enabled('music')
                    and current['permissions']['local_playlists'])
        store=get_music_station_store()
        existing={p['id'] for p in await store.list_playlists()}
        local_id=cfg['actor_local_playlists'].get(actor,'')
        if local_id not in existing:local_id=''
        local=await store.save_song_playlist(profile['playlist_name'],songs,playlist_id=local_id,
            actor=actor,actor_name=profile['name'],allowed=allowed,owner_actor=actor)
        with account.LOCK:
            current=account.load()
            if current['generation']==cfg['generation']:
                current['actor_local_playlists'][actor]=local['playlist_id'];account._save(current)
        messages.append('已加入自己的点歌台歌单。')
    if cloud and cfg['user_id'] and cfg['permissions']['cloud_playlists']:
        pid=cfg['actor_playlists'].get(actor,'')
        data={'target':'netease','op':'add' if pid else 'create','name':profile['playlist_name'],
              'playlist_id':pid,'account_generation':cfg['generation'],'songs':[]}
        result=await execute(data,'personal-cloud:'+action_id,actor=actor,actor_name=profile['name'],
                             as_ai=True,resolved_songs=songs,personal_actor=actor)
        _status(actor,cfg['generation'],result)
        if result.get('ok') and result.get('playlist_id'):
            with account.LOCK:
                current=account.load()
                if current['generation']==cfg['generation']:
                    current['actor_playlists'][actor]=result['playlist_id'];account._save(current)
        messages.append(result['message'])
        return {**result,'message':' '.join(messages)}
    return {'ok':bool(messages),'message':' '.join(messages) or '主人尚未开启自己的歌单写入权限。'}


async def collect(song,actor,source_type,source_message_id,generation):
    cfg=account.load()
    key=hashlib.sha256(json.dumps([actor,source_type,source_message_id,song.get('id')],ensure_ascii=False).encode()).hexdigest()
    try:
        return await add([song],actor,key,generation=generation)
    except Exception:
        value={'ok':False,'message':'个人歌单暂未收好，请稍后查看；不会自动重复提交网易云写入。'}
        _status(actor,cfg['generation'],value)
        log.info('personal music collection unavailable: %s',actor)
        return value
