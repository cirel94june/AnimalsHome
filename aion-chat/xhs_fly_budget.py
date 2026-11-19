"""Conservative prepaid runtime leases, independent of delayed Fly invoices."""
import asyncio
import json
import os
import tempfile
import time
import math
from pathlib import Path

import httpx
from config import DATA_DIR

PATH=DATA_DIR/'xhs_fly_budget.json'
LOCK=asyncio.Lock()
RESERVE_MICRO=2_000_000
LIMIT_MICRO=3_000_000
LEASE_SECONDS=600
LEASE_MICRO=3334  # Round up 10 minutes at $0.02/hour; VM self-terminates at 300s.
BASE='https://api.machines.dev/v1/apps/'


def load():
    if not PATH.exists():return {'spent_micro':RESERVE_MICRO,'lease_until':0,'locked':False}
    try:
        data=json.loads(PATH.read_text(encoding='utf-8'))
        assert type(data['spent_micro']) is int and data['spent_micro']>=RESERVE_MICRO
        assert type(data['lease_until']) in (int,float) and math.isfinite(data['lease_until']) and data['lease_until']>=0
        assert type(data['locked']) is bool
        return data
    except (OSError,ValueError,KeyError,TypeError,AssertionError):
        raise RuntimeError('云浏览器预算记录不可读，已停止启动。') from None


def save(data):
    fd,name=tempfile.mkstemp(dir=PATH.parent,prefix='.xhs-budget-')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as output:
            json.dump(data,output);output.flush();os.fsync(output.fileno())
        os.replace(name,PATH)
    finally:
        if os.path.exists(name):os.unlink(name)


async def ensure_started(cfg):
    """Caller holds the transport lock. Fail closed; never refund failed starts."""
    async with LOCK:
        app=cfg['url'].split('//',1)[1].split('.',1)[0]
        target=BASE+app+'/machines/'+cfg['machine_id']
        headers={'Authorization':'Bearer '+cfg['fly_token']}
        async with httpx.AsyncClient(timeout=20,follow_redirects=False,headers=headers) as client:
            async def stop():
                response=await client.post(target+'/stop',json={})
                response.raise_for_status()
            state=load()
            identity=app+'/'+cfg['machine_id']
            if state.get('target') not in (None,identity):
                raise RuntimeError('预算已绑定另一台云浏览器，已拒绝启动。')
            state['target']=identity
            machine=(await client.get(target))
            machine.raise_for_status();machine=machine.json()
            config=machine.get('config') or {};guest=config.get('guest') or {}
            valid=(machine.get('id')==cfg['machine_id'] and machine.get('region')=='iad'
                and guest.get('cpu_kind')=='shared' and guest.get('cpus')==2
                and guest.get('memory_mb')==2048 and config.get('restart',{}).get('policy')=='no'
                and config.get('services') and all(s.get('autostart') is False for s in config['services']))
            if not valid or state['locked']:
                state['locked']=True;save(state)
                await stop()
                raise RuntimeError('云浏览器已锁停，请先检查配置或预算。')
            now=time.time()
            if machine.get('state')=='started' and now<state['lease_until']:
                return
            if machine.get('state') not in {'stopped','created'}:
                await stop()
                raise RuntimeError('云浏览器正在关机，请稍后重试。')
            if state['spent_micro']+LEASE_MICRO>=LIMIT_MICRO:
                state['locked']=True;save(state)
                raise RuntimeError('云浏览器已达到保守用量上限，已锁停。')
            state['spent_micro']+=LEASE_MICRO
            state['lease_until']=now+LEASE_SECONDS
            save(state)  # Durable debit precedes any paid start, including errors.
            response=await client.post(target+'/start',json={});response.raise_for_status()


def public_status():
    state=load()
    return {'estimated_usd':state['spent_micro']/1_000_000,'limit_usd':3,
            'locked':state['locked'],'includes_reserve':True}
