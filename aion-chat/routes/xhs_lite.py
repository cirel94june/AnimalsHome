from typing import Literal, Any, Optional
import asyncio
import hashlib
import time

from fastapi import APIRouter, Query, Request, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

import xhs_lite
import xhs_browser


router = APIRouter(prefix="/api/xhs-lite", tags=["xhs-lite"])
_LOGIN_LOCK = asyncio.Lock()


class XhsActorConfig(BaseModel):
    enabled: Optional[bool] = None


class XhsConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    auto_enabled: Optional[bool] = None
    cookie: Optional[str] = None
    clear_cookie: Optional[bool] = None
    target_user_id: Optional[str] = None
    target_xsec_token: Optional[str] = None
    target_nickname: Optional[str] = None
    target_profile_url: Optional[str] = None
    task_instruction: Optional[str] = None
    use_following_list: Optional[bool] = None
    roam_mode: Optional[Literal["explore", "target"]] = None  # 随便逛 / 看指定账号（11-03）
    max_following_pages: Optional[int] = None
    allow_write_comments: Optional[bool] = None
    write_delay_seconds: Optional[float] = None
    max_comments_for_prompt: Optional[int] = None
    comment_signature_template: Optional[str] = None
    actors: Optional[dict[str, XhsActorConfig]] = None


class XhsRunRequest(BaseModel):
    actor: str = "aion"


class CloudConnection(BaseModel):
    url: str
    token: str
    machine_id: str
    fly_token: str


@router.post('/cloud-connection')
async def cloud_connection(body: CloudConnection,request: Request):
    if request.headers.get('x-requested-with')!='AnimalsHome' or request.headers.get('sec-fetch-site')=='cross-site':
        raise HTTPException(403,'same-origin request required')
    import xhs_remote
    try:
        connection=xhs_remote.validate(**body.model_dump())
        async with _LOGIN_LOCK,xhs_lite._ACCOUNT_LOCK:
            result=await xhs_remote.call('status',connection=connection)
            if result.get('available') is not True:raise RuntimeError('云浏览器尚未就绪。')
            previous=xhs_remote.load()
            if connection!=previous:
                xhs_remote.save(**body.model_dump())
                xhs_lite.save_config({'cloud_login':False,'cookie':'','logged_in_user_id':'',
                    'logged_in_nickname':'','logged_in_red_id':''})
        return {'ok':True}
    except (RuntimeError,ValueError):
        return JSONResponse({'ok':False,'error':'连接未完成，请检查地址和连接钥匙。'},status_code=503)


@router.get("/config")
async def get_config():
    return xhs_lite.public_config()


@router.put("/config")
async def update_config(body: XhsConfigUpdate):
    updates: dict[str, Any] = {}
    for key in (
        "enabled",
        "auto_enabled",
        "target_user_id",
        "target_xsec_token",
        "target_nickname",
        "target_profile_url",
        "task_instruction",
        "use_following_list",
        "roam_mode",
        "allow_write_comments",
        "comment_signature_template",
    ):
        value = getattr(body, key)
        if value is not None:
            updates[key] = value
    if body.max_following_pages is not None:
        updates["max_following_pages"] = max(1, min(10, int(body.max_following_pages)))
    if body.write_delay_seconds is not None:
        updates["write_delay_seconds"] = max(5.0, float(body.write_delay_seconds))
    if body.max_comments_for_prompt is not None:
        updates["max_comments_for_prompt"] = max(5, min(50, int(body.max_comments_for_prompt)))
    if body.clear_cookie:
        updates["cookie"] = ""
        updates['cloud_login'] = False
    elif body.cookie is not None and body.cookie.strip():
        updates["cookie"] = body.cookie.strip()
        updates['cloud_login'] = False
    if 'cloud_login' in updates:
        updates.update(logged_in_user_id='',logged_in_nickname='',logged_in_red_id='')
    if body.actors is not None:
        updates["actors"] = {
            actor: actor_cfg.dict(exclude_none=True)
            for actor, actor_cfg in body.actors.items()
            if actor in xhs_lite.known_actors()
        }
    async with xhs_lite._ACCOUNT_LOCK:
        xhs_lite.save_config(updates)
    return {"ok": True, "config": xhs_lite.public_config()}


@router.post("/check-login")
async def check_login():
    try:
        result = await xhs_lite.check_login()
        account = {
            "logged_in": bool(result.get("logged_in")),
            "nickname": result.get("nickname") or "",
            "user_id": result.get("user_id") or "",
            "red_id": result.get("red_id") or "",
        }
        return {"ok": account['logged_in'], "account": account,
                **({} if account['logged_in'] else {'error':'小红书登录已失效，请重新登录。'})}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.post('/cloud-login/{operation}')
async def cloud_login(operation: Literal['status','start','frame','confirm','cancel'], request: Request):
    # Browser-only owner UI. Cross-site forms/images cannot send this header;
    # the existing whole-site Access door still authenticates the request.
    if request.headers.get('x-requested-with')!='AnimalsHome' or request.headers.get('sec-fetch-site')=='cross-site':
        raise HTTPException(status_code=403,detail='same-origin request required')
    async with _LOGIN_LOCK, xhs_lite._ACCOUNT_LOCK:
        try:
            if operation=='confirm':
                candidate = await xhs_browser.call('candidate')
                cookie = candidate.get('cookie') or ''
                if not cookie:raise RuntimeError('还没有检测到登录，请先用小红书扫码并在手机确认。')
                account = await xhs_lite._run_worker_local('check-login',{},cookie=cookie,timeout=60)
                if not account.get('logged_in') or not account.get('user_id'):
                    raise RuntimeError('小红书尚未确认登录，请完成手机上的验证后再试。')
                await xhs_browser.call('accept',{'expected_hash':hashlib.sha256(cookie.encode()).hexdigest()})
                xhs_lite.save_config({'cookie':cookie,'cloud_login':True,'allow_write_comments':False,
                                     'logged_in_user_id':account['user_id'],
                                     'logged_in_nickname':account.get('nickname') or '',
                                     'logged_in_red_id':account.get('red_id') or '',
                                     'last_login_check_at':time.time()})
                result={'ok':True,'account':{key:account.get(key) for key in ('logged_in','user_id','nickname','red_id')}}
            elif operation=='frame':
                return Response(await xhs_browser.call('frame'),media_type='image/png',headers={'Cache-Control':'no-store'})
            else:
                result=await xhs_browser.call(operation)
            return JSONResponse(result,headers={'Cache-Control':'no-store'})
        except RuntimeError as exc:
            return JSONResponse({'ok':False,'error':str(exc)},status_code=503,headers={'Cache-Control':'no-store'})


@router.get("/followings")
async def followings():
    try:
        users = await xhs_lite.list_followings()
        return {"ok": True, "users": users}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "users": []}


@router.post("/run-once")
async def run_once(body: XhsRunRequest):
    try:
        result = await xhs_lite.run_actor_roam(body.actor, manual=True)
        return {"ok": True, "result": result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/logs")
async def logs(limit: int = Query(default=50, ge=1, le=300)):
    return {"items": xhs_lite.read_logs(limit)}
