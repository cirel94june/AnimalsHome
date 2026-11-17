"""Owner-scoped NetEase login and account operations, without a browser process."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
from urllib.parse import quote, urlsplit

import pyncm
from pyncm.apis import WeapiCryptoRequest
from pyncm.apis import login, playlist, track, user

from config import DATA_DIR

ACCOUNT_PATH = DATA_DIR / 'netease_account.json'
LOCK = threading.RLock()
ASYNC_LOCK = asyncio.Lock()
_QR: dict[str, dict] = {}
PERMISSIONS = {'local_playlists': True, 'cloud_playlists': False, 'cloud_likes': False}
CHARTS = {'飙升榜':19723756, '新歌榜':3779629, '热歌榜':3778678, '原创榜':2884035}


class NeteaseError(RuntimeError):
    pass


class _Session(pyncm.Session):
    def request(self, method, url, *args, **kwargs):
        kwargs.setdefault('timeout', (5, 12))
        kwargs.setdefault('allow_redirects', False)
        return super().request(method, url, *args, **kwargs)


def load():
    try:
        raw=json.loads(ACCOUNT_PATH.read_text(encoding='utf-8'))
        if not isinstance(raw,dict):raise ValueError()
    except FileNotFoundError:raw={}
    except (OSError,ValueError):raise NeteaseError('网易云账号配置暂时无法读取。') from None
    if not isinstance(raw.get('permissions',{}),dict) or not isinstance(raw.get('cookies',[]),list):
        raise NeteaseError('网易云账号配置暂时无法读取。')
    if any(k not in PERMISSIONS or type(v) is not bool for k,v in raw.get('permissions',{}).items()):
        raise NeteaseError('网易云账号权限配置不正确。')
    if any(not isinstance(c,dict) or not isinstance(c.get('name'),str) or not isinstance(c.get('value'),str)
           for c in raw.get('cookies',[])):
        raise NeteaseError('网易云账号配置暂时无法读取。')
    if type(raw.get('generation',0)) is not int or raw.get('generation',0)<0:
        raise NeteaseError('网易云账号配置暂时无法读取。')
    return {'managed':False,'cookies':[],'user_id':'','nickname':'','generation':0,
            'verified_at':0,**raw,'permissions':{**PERMISSIONS,**raw.get('permissions',{})}}


def _save(cfg):
    ACCOUNT_PATH.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.netease-',dir=ACCOUNT_PATH.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as output:
            json.dump(cfg,output,ensure_ascii=False)
            output.flush();os.fsync(output.fileno())
        os.replace(name,ACCOUNT_PATH)
    finally:
        if os.path.exists(name):os.unlink(name)


def credentials():
    """Internal only. A managed logout must not resurrect an old settings cookie."""
    cfg=load()
    cookies=cfg['cookies'] if cfg['managed'] else []
    if not cfg['managed']:
        from config import SETTINGS
        token=str(SETTINGS.get('netease_music_u') or '').strip()
        if token:cookies=[{'name':'MUSIC_U','value':token,'domain':'.music.163.com','path':'/'}]
    return cookies,hashlib.sha256(json.dumps(cookies,sort_keys=True).encode()).hexdigest()


def public():
    cfg=load()
    cookies,_=credentials()
    return {'logged_in':bool(cfg['user_id'] and cfg['verified_at']),
            'cookie_configured':any(item.get('name')=='MUSIC_U' and item.get('value') for item in cookies),
            'account':{'user_id':cfg['user_id'],'nickname':cfg['nickname']},
            'permissions':cfg['permissions'],'verified_at':cfg['verified_at'],'generation':cfg['generation']}


def _session(cookies):
    session=_Session()
    session.force_http=False
    for cookie in cookies:
        if cookie.get('name') in {'MUSIC_U','__csrf','NMTID','MUSIC_A'}:
            session.cookies.set(cookie['name'],cookie['value'],domain='.music.163.com',path='/')
            if cookie['name']=='__csrf':session.csrf_token=cookie['value']
    return session


def _cookies(session):
    # No arbitrary response header, session object, or login dump leaves this module.
    return [{'name':item.name,'value':item.value,'domain':'.music.163.com','path':'/'}
            for item in session.cookies if item.name in {'MUSIC_U','__csrf','NMTID','MUSIC_A'}
            and (not item.domain or item.domain.lstrip('.')=='music.163.com')]


def _checked(value, codes=(200,)):
    if not isinstance(value,dict):raise NeteaseError('网易云返回异常，请稍后重试。')
    code=value.get('code')
    if code not in codes:
        if code==301:raise NeteaseError('网易云登录已失效，请重新登录。')
        if code==-462:raise NeteaseError('网易云要求进一步验证，请在官方客户端确认后再试。')
        raise NeteaseError('网易云暂时未完成请求，请稍后重试。')
    return value


def _read_checked(fn):
    """A confirmed expiry may renew one read; never retry a mutating request."""
    value=fn()
    if isinstance(value,dict) and value.get('code')==301:
        session=pyncm.GetCurrentSession()
        _checked(login.LoginRefreshToken())
        identity=_identity(session)
        cfg=load()
        if cfg['user_id']!=identity['user_id']:raise NeteaseError('账号已改变，请重新确认网易云登录。')
        cfg.update(cookies=_cookies(session),verified_at=time.time())
        _save(cfg)
        value=fn()
    return _checked(value)


@WeapiCryptoRequest
def _api(path, data):
    return path,data


def _identity(session):
    session.csrf_token=next((c.value for c in session.cookies if c.name=='__csrf'),session.csrf_token)
    with session:
        value=_checked(login.GetCurrentLoginStatus())
    value=value.get('data') if isinstance(value.get('data'),dict) else value
    account=value.get('account') or {};profile=value.get('profile') or {}
    uid=str(account.get('id') or profile.get('userId') or '')
    if not uid.isdecimal() or int(uid)<=0:raise NeteaseError('尚未确认网易云登录，请完成手机验证。')
    return {'user_id':uid,'nickname':str(profile.get('nickname') or '网易云用户')[:100]}


def _commit(session, identity):
    cfg=load()
    cfg.update(managed=True,cookies=_cookies(session),**identity,
               generation=int(cfg['generation'])+1,verified_at=time.time())
    # A new login requires renewed owner permission for cloud writes.
    cfg['permissions']={**cfg['permissions'],'cloud_playlists':False,'cloud_likes':False}
    if not any(c['name']=='MUSIC_U' and c['value'] for c in cfg['cookies']):
        raise NeteaseError('网易云未返回可保存的登录，请重新扫码。')
    _save(cfg)
    return public()


def _expire_qr():
    for handle in list(_QR):
        if _QR[handle]['expires']<=time.time():_QR.pop(handle)['session'].close()


def start_qr():
    with LOCK:
        for item in _QR.values():item['session'].close()
        _QR.clear()
        session=_session([])
        try:
            with session:value=_checked(login.LoginQrcodeUnikey())
            key=str(value.get('unikey') or (value.get('data') or {}).get('unikey') or '')
            if not re.fullmatch(r'[A-Za-z0-9_-]{8,256}',key):raise NeteaseError('网易云未返回二维码，请稍后重试。')
            handle=secrets.token_urlsafe(24)
            _QR[handle]={'session':session,'key':key,'expires':time.time()+300,
                         'generation':load()['generation'],'last_poll':0,'identity':None}
            return {'handle':handle,'url':'https://music.163.com/login?codekey='+quote(key,safe=''),
                    'expires_in':300}
        except BaseException:
            session.close();raise


def poll_qr(handle):
    with LOCK:
        _expire_qr()
        item=_QR.get(handle)
        if not item:raise NeteaseError('二维码已到期，请重新生成。')
        if item['generation']!=load()['generation']:raise NeteaseError('登录设置已改变，请重新生成二维码。')
        if item['identity']:return {'state':'confirm','account':item['identity']}
        if time.time()-item['last_poll']<2.5:return {'state':'waiting'}
        item['last_poll']=time.time()
        with item['session']:value=_checked(login.LoginQrcodeCheck(item['key']),codes=(800,801,802,803))
        if value['code']==800:
            item['expires']=0
            return {'state':'expired'}
        if value['code']==803:
            item['identity']=_identity(item['session'])
            return {'state':'confirm','account':item['identity']}
        return {'state':'scanned' if value['code']==802 else 'waiting'}


def confirm_qr(handle):
    with LOCK:
        _expire_qr()
        item=_QR.get(handle)
        if not item or not item['identity']:raise NeteaseError('请先完成手机扫码确认。')
        if item.get('committed_generation'):
            if load()['generation']==item['committed_generation']:return public()
            raise NeteaseError('登录设置已改变，请重新扫码。')
        if item['generation']!=load()['generation']:raise NeteaseError('登录设置已改变，请重新扫码。')
        # Verify again rather than committing a session changed since the poll.
        identity=_identity(item['session'])
        if identity['user_id']!=item['identity']['user_id']:raise NeteaseError('账号已改变，请重新扫码。')
        result=_commit(item['session'],identity)
        item['committed_generation']=load()['generation']
        item['session'].close()
        return result


def cancel_qr(handle):
    with LOCK:
        item=_QR.pop(handle,None)
        if item:item['session'].close()
        return {'ok':True}


def login_cookie(raw):
    value=str(raw or '').strip()
    if not value or len(value)>16384:raise NeteaseError('请填写 MUSIC_U 登录凭据。')
    match=re.search(r'(?:^|;\s*)MUSIC_U=([^;\s]+)',value)
    token=match[1] if match else value
    if not re.fullmatch(r'[A-Za-z0-9_%+./=-]{20,4096}',token):raise NeteaseError('MUSIC_U 格式不正确。')
    with LOCK:
        with _session([{'name':'MUSIC_U','value':token}]) as session:
            return _commit(session,_identity(session))


def logout():
    with LOCK:
        cfg=load()
        cfg.update(managed=True,cookies=[],user_id='',nickname='',verified_at=0,
                   generation=int(cfg['generation'])+1)
        cfg['permissions']={**cfg['permissions'],'cloud_playlists':False,'cloud_likes':False}
        _save(cfg)
        for item in _QR.values():item['session'].close()
        _QR.clear()
        return public()


def update_permissions(values):
    with LOCK:
        cfg=load()
        for key,value in values.items():
            if key not in PERMISSIONS or not isinstance(value,bool):raise NeteaseError('权限设置不正确。')
            if key!='local_playlists' and value and not cfg['user_id']:raise NeteaseError('请先登录网易云账号。')
            cfg['permissions'][key]=value
        _save(cfg)
        return public()


def check_account():
    with LOCK:
        cookies,_=credentials()
        if not cookies:raise NeteaseError('请先扫码登录网易云。')
        with _session(cookies) as session:
            identity=_identity(session)
            cfg=load()
            if cfg['user_id'] and cfg['user_id']!=identity['user_id']:
                raise NeteaseError('网易云账号已改变，请重新确认登录。')
            if not cfg['managed']:return _commit(session,identity)
            cfg.update(verified_at=time.time(),nickname=identity['nickname'])
            _save(cfg)
            return public()


def song(value):
    artists=value.get('ar') or value.get('artists') or []
    album=value.get('al') or value.get('album') or {}
    cover=str(album.get('picUrl') or value.get('picUrl') or '')
    host=urlsplit(cover).hostname or ''
    if not (cover.startswith('https://') and host.endswith('.music.126.net')):cover=''
    return {'id':int(value['id']),'name':str(value.get('name') or '')[:160],
            'artist':' / '.join(str(a.get('name') or '') for a in artists)[:160],
            'album':str(album.get('name') or '')[:160],'cover':cover,
            'duration':max(0,int(value.get('dt') or value.get('duration') or 0))}


def _playlists(cfg):
    value=_read_checked(lambda:user.GetUserPlaylists(cfg['user_id'],limit=200))
    return [{'id':str(p['id']),'name':str(p.get('name') or '')[:160],
             'track_count':max(0,int(p.get('trackCount') or 0)),
             'owned':str((p.get('creator') or {}).get('userId') or '')==cfg['user_id'],
             'liked':p.get('specialType')==5}
            for p in (value.get('playlist') or [])[:200]]


def _tracks(playlist_id,offset=0,limit=50):
    value=_read_checked(lambda:playlist.GetPlaylistInfo(int(playlist_id)))
    detail=value.get('playlist') or {}
    ids=[int(p['id']) for p in (detail.get('trackIds') or [])]
    selected=ids[offset:offset+limit]
    fetched=_read_checked(lambda:track.GetTrackDetail(selected)).get('songs',[]) if selected else []
    by_id={int(p['id']):song(p) for p in fetched}
    songs=[by_id[i] for i in selected if i in by_id]
    return {'name':str(detail.get('name') or '')[:160], 'songs':songs,'total':len(ids),
            'next_offset':offset+len(selected) if offset+len(selected)<len(ids) else None,
            'owned_by':str((detail.get('creator') or {}).get('userId') or '')}


def read(kind,ref='',offset=0):
    with LOCK:
        cfg=load();cookies,_=credentials()
        if kind not in {'charts'} and not cfg['user_id']:raise NeteaseError('请先登录网易云。')
        with _session(cookies) as session:
            if kind=='playlists':return {'playlists':_playlists(cfg)}
            if kind=='charts':return {'playlists':[{'id':str(pid),'name':name,'owned':False} for name,pid in CHARTS.items()]}
            if kind in {'playlist','chart'}:
                if not str(ref).isdecimal() or int(ref)<=0:raise NeteaseError('请选择有效歌单。')
                if kind=='playlist' and ref not in {p['id'] for p in _playlists(cfg)}:
                    raise NeteaseError('这张歌单不在当前账号列表中，请刷新后再选。')
                if kind=='chart' and int(ref) not in CHARTS.values():raise NeteaseError('请选择官方榜单。')
                value=_tracks(ref,max(0,min(int(offset),10000)),50)
                value.pop('owned_by',None)
                return value
            if kind=='liked':
                found=next((p for p in _playlists(cfg) if p['liked']),None)
                if not found:return {'songs':[],'total':0,'next_offset':None,'name':'我喜欢的音乐'}
                value=_tracks(found['id'],max(0,min(int(offset),10000)),50)
                value.pop('owned_by',None);return value
            if kind=='daily':
                value=_read_checked(lambda:_api('/weapi/v3/discovery/recommend/songs',{}))
                songs=(value.get('data') or {}).get('dailySongs') or []
            elif kind=='fm':songs=_read_checked(lambda:_api('/weapi/v1/radio/get',{})).get('data') or []
            elif kind in {'week','alltime'}:
                value=_read_checked(lambda:_api('/weapi/v1/play/record',{'uid':cfg['user_id'],'type':1 if kind=='week' else 0}))
                songs=[p.get('song') or {} for p in value.get('weekData' if kind=='week' else 'allData',[])]
            else:raise NeteaseError('不支持的网易云列表。')
            return {'songs':[song(s) for s in songs[:50] if s.get('id')],
                    'total':min(len(songs),50),'next_offset':None}


def mutate(op, name='',playlist_id='',song_ids=(), *, as_ai=False,expected_generation=None):
    with LOCK:
        cfg=load()
        if expected_generation is not None and expected_generation!=cfg['generation']:
            raise NeteaseError('账号或登录已经改变，本次操作已停止。')
        permission='cloud_likes' if op=='like' else 'cloud_playlists'
        if as_ai:
            from capabilities import is_capability_enabled
            if not is_capability_enabled('music'):raise NeteaseError('点歌能力已关闭。')
        if as_ai and not cfg['permissions'].get(permission):raise NeteaseError('主人尚未开启这项网易云写入权限。')
        if not cfg['user_id']:raise NeteaseError('请先登录网易云。')
        if op not in {'create','add','like'}:raise NeteaseError('不支持的歌单操作。')
        ids=list(dict.fromkeys(int(i) for i in song_ids))
        if len(ids)>20 or any(i<=0 for i in ids):raise NeteaseError('每次最多添加20首有效歌曲。')
        if op!='create' and not ids:raise NeteaseError('请先选择歌曲。')
        if op=='create' and not 1<=len(name.strip())<=40:raise NeteaseError('歌单名称需要1–40个字符。')
        cookies,_=credentials()
        with _session(cookies) as session:
            if _identity(session)['user_id']!=cfg['user_id']:raise NeteaseError('网易云账号有变化，请重新登录。')
            if op=='like':
                for song_id in ids:_checked(track.SetLikeTrack(song_id,like=True))
                return {'ok':True,'message':f'已将{len(ids)}首歌标为网易云红心。'}
            if op=='create':
                matches=[p for p in _playlists(cfg) if p['owned'] and p['name']==name.strip()]
                if len(matches)>1:raise NeteaseError('有多张同名网易云歌单，请在点歌台选择具体歌单。')
                if matches:playlist_id=matches[0]['id']
                else:
                    # pyncm multiplies this value by one; NetEase requires 10, not 1.
                    value=_checked(playlist.SetCreatePlaylist(name.strip(),privacy=10))
                    playlist_id=str(value.get('id') or (value.get('playlist') or {}).get('id') or '')
                if not playlist_id.isdecimal():raise NeteaseError('网易云未确认新歌单，请先刷新列表，暂勿重复创建。')
            if not str(playlist_id).isdecimal():raise NeteaseError('请选择有效的网易云歌单。')
            detail=_tracks(playlist_id,0,0)
            if detail['owned_by']!=cfg['user_id']:raise NeteaseError('只能添加到当前账号自己创建的歌单。')
            if ids:_checked(playlist.SetManipulatePlaylistTracks(ids,playlist_id,op='add'))
            return {'ok':True,'playlist_id':str(playlist_id),'name':detail['name'],
                    'message':f'网易云歌单《{detail["name"]}》已保存'+(f'，加入{len(ids)}首歌。' if ids else '。')}
