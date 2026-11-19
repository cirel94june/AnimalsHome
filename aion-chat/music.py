"""
网易云音乐集成：pyncm 搜索 + 获取歌曲信息
支持 MUSIC_U Cookie 登录（VIP 可播放付费歌曲），未配置时退回匿名登录
会话每 2 小时自动刷新，获取音频失败时自动重试一次
"""

import logging, threading, time
from functools import wraps
from pyncm.apis.login import LoginViaAnonymousAccount, LoginViaCookie
from pyncm.apis.cloudsearch import GetSearchResult
from pyncm.apis.track import GetTrackDetail, GetTrackAudio

log = logging.getLogger(__name__)

_init_lock = threading.RLock()
_inited = False
_credential_version = None
_last_login_time = 0.0
_SESSION_TTL = 2 * 3600  # 会话有效期：2小时


# pyncm 的每个网络请求默认都没有时限：统一给一个（连接 5 秒、每次读 15 秒），网易云卡住时请求自己会结束
NETEASE_TIMEOUT = (5, 15)
import pyncm as _pyncm

if not getattr(_pyncm.Session.request, "_aion_timeout", False):
    _original_request = _pyncm.Session.request

    def _request_with_timeout(self, method, url, *args, **kwargs):
        kwargs.setdefault("timeout", NETEASE_TIMEOUT)
        return _original_request(self, method, url, *args, **kwargs)

    _request_with_timeout._aion_timeout = True
    _pyncm.Session.request = _request_with_timeout


def _ensure_login():
    """确保已登录且会话未过期（优先 MUSIC_U Cookie，否则匿名）"""
    global _inited, _last_login_time, _credential_version
    from netease_account import credentials
    cookies, version = credentials()
    now = time.time()
    if _inited and version == _credential_version and (now - _last_login_time < _SESSION_TTL):
        return
    with _init_lock:
        now = time.time()
        if _inited and version == _credential_version and (now - _last_login_time < _SESSION_TTL):
            return
        _inited = False
        _credential_version = None
        _last_login_time = 0
        try:
            _pyncm.GetCurrentSession().cookies.clear()
            jar={item['name']:item['value'] for item in cookies}
            music_u = jar.get('MUSIC_U','')
            if music_u:
                result=LoginViaCookie(MUSIC_U=music_u,**{k:v for k,v in jar.items() if k!='MUSIC_U'})
                if not (result.get('result') or {}).get('success'):
                    raise RuntimeError('网易云登录已失效，请在点歌台重新登录。')
                _inited = True
                _credential_version = version
                _last_login_time = now
                log.info("pyncm MUSIC_U Cookie 登录成功（VIP）")
            else:
                LoginViaAnonymousAccount()
                _inited = True
                _credential_version = version
                _last_login_time = now
                log.info("pyncm 匿名登录成功（未配置 MUSIC_U）")
        except Exception as e:
            _pyncm.GetCurrentSession().cookies.clear()
            log.error("pyncm 登录失败: %s", type(e).__name__)
            raise


def _force_relogin():
    """强制重新登录（会话可能已失效）"""
    global _inited, _last_login_time
    with _init_lock:
        _inited = False
        _last_login_time = 0
    _ensure_login()


def reload_login():
    """重新登录（settings 更新 MUSIC_U 后调用）"""
    _force_relogin()


def _serialised(fn):
    @wraps(fn)
    def call(*args,**kwargs):
        # Legacy searches/audio share pyncm's default session. Do not replace its
        # credentials halfway through another request when the owner logs out.
        with _init_lock:return fn(*args,**kwargs)
    return call


@_serialised
def search_songs(keyword: str, limit: int = 5) -> list[dict]:
    """搜索歌曲，返回精简结果列表"""
    _ensure_login()
    resp = GetSearchResult(keyword, limit=limit)
    songs = resp.get("result", {}).get("songs", [])
    results = []
    for s in songs:
        artists = [a["name"] for a in s.get("ar", [])]
        album_info = s.get("al", {})
        results.append({
            "id": s["id"],
            "name": s["name"],
            "artists": artists,
            "artist": " / ".join(artists),
            "album": album_info.get("name", ""),
            "cover": __import__('music_media').remote_cover(album_info.get('picUrl')),
            "duration": s.get("dt", 0),  # 毫秒
        })
    return results


@_serialised
def get_song_detail(song_id: int) -> dict | None:
    """获取单曲详情"""
    _ensure_login()
    resp = GetTrackDetail([song_id])
    songs = resp.get("songs", [])
    if not songs:
        return None
    s = songs[0]
    artists = [a["name"] for a in s.get("ar", [])]
    album_info = s.get("al", {})
    return {
        "id": s["id"],
        "name": s["name"],
        "artists": artists,
        "artist": " / ".join(artists),
        "album": album_info.get("name", ""),
        "cover": __import__('music_media').remote_cover(album_info.get('picUrl')),
        "duration": s.get("dt", 0),
    }


@_serialised
def get_audio_url(song_id: int) -> str | None:
    """尝试获取播放 URL，失败时自动重新登录重试一次"""
    _ensure_login()
    resp = GetTrackAudio([song_id])
    for d in resp.get("data", []):
        url = d.get("url")
        if url:
            return url
    # 可能会话过期，强制重新登录后重试
    log.info("get_audio_url(%s) 返回空，尝试重新登录重试", song_id)
    _force_relogin()
    resp = GetTrackAudio([song_id])
    for d in resp.get("data", []):
        url = d.get("url")
        if url:
            return url
    return None
