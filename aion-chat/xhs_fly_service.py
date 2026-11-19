"""Private, fixed-operation HTTPS gateway. No arbitrary browsing or model tools."""
import asyncio
from contextlib import asynccontextmanager
import hmac
import json
import os
import re

from fastapi import FastAPI, Request
from fastapi.responses import Response, JSONResponse
from xhs_browser_service import LoginBrowser, ROOT, atomic_json

READS={'check-login','search','list-feeds','get-feed-detail','user-profile','list-followings'}
OPERATIONS={'status','start','cancel','frame','candidate','accept','credentials','worker'}
TOKEN=os.environ.get('XHS_GATEWAY_TOKEN','')
BROWSER=LoginBrowser()
LOCK=asyncio.Lock()
MAX_OUTPUT=1024*1024*1024


@asynccontextmanager
async def lifespan(app):
    if not re.fullmatch(r'[a-f0-9]{64}',TOKEN):raise RuntimeError('gateway_token_required')
    ROOT.mkdir(parents=True,exist_ok=True);ROOT.chmod(0o700)
    reaper=asyncio.create_task(BROWSER.reap())
    def failed(task):
        if not task.cancelled():os._exit(1)  # VM stops with restart=no.
    reaper.add_done_callback(failed)
    try:yield
    finally:
        reaper.cancel();await asyncio.gather(reaper,return_exceptions=True)
        await BROWSER.close()


app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)


def outgoing_bytes():
    path=ROOT/'transfer.json'
    if not path.exists():return 0
    value=json.loads(path.read_text(encoding='utf-8'))['bytes']
    if type(value) is not int or value<0:raise ValueError('invalid_transfer_ledger')
    return value


async def run_worker(data):
    if data.get('endpoint') not in READS:raise ValueError('read_only')
    cookie=data.get('cookie','');body=data.get('body',{})
    if not isinstance(cookie,str) or len(cookie)>16000 or not isinstance(body,dict):raise ValueError('invalid_request')
    proc=await asyncio.create_subprocess_exec('node','xhs_lite_worker_runner.mjs',
        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
        limit=8*1024*1024)
    try:
        proc.stdin.write(json.dumps(data).encode());await proc.stdin.drain();proc.stdin.close()
        output=bytearray()
        async def read():
            while chunk:=await proc.stdout.read(65536):
                if len(output)+len(chunk)>8*1024*1024:raise ValueError('worker_response_too_large')
                output.extend(chunk)
            await proc.wait()
        await asyncio.wait_for(read(),60)
        result=json.loads(output)
        if not result.get('ok') or proc.returncode!=0:raise ValueError('worker_failed')
        return result['data']
    finally:
        if proc.returncode is None:proc.kill();await proc.wait()


@app.get('/health')
async def health():return {'ok':True}


@app.post('/{operation}')
async def operation(operation: str,request: Request):
    if not TOKEN or not hmac.compare_digest(request.headers.get('authorization',''),'Bearer '+TOKEN):
        return JSONResponse({'error':'unauthorized'},status_code=401)
    if operation not in OPERATIONS:return JSONResponse({'error':'unsupported_operation'},status_code=404)
    raw=bytearray()
    async for chunk in request.stream():
        if len(raw)+len(chunk)>96000:return JSONResponse({'error':'request_too_large'},status_code=413)
        raw.extend(chunk)
    async with LOCK:
        try:
            transferred=outgoing_bytes()
            if transferred>=MAX_OUTPUT:return JSONResponse({'error':'transfer_budget_locked'},status_code=423)
            data=json.loads(raw or b'{}')
            if not isinstance(data,dict):raise ValueError('object_required')
            if operation=='worker':
                result=await run_worker(data);kind='application/json'
            else:
                result,kind=await asyncio.wait_for(BROWSER.call('/'+operation,data),80)
            payload=result if isinstance(result,bytes) else json.dumps(result).encode()
            if len(payload)>8*1024*1024:raise ValueError('response_too_large')
            if transferred+len(payload)>MAX_OUTPUT:return JSONResponse({'error':'transfer_budget_locked'},status_code=423)
            atomic_json(ROOT/'transfer.json',{'bytes':transferred+len(payload)})
            return Response(payload,media_type=kind,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
        except Exception:
            return JSONResponse({'error':'browser_operation_failed'},status_code=503,headers={'Cache-Control':'no-store'})
