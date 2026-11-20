"""Independent deployment blockers. Synthetic APIs and temporary files only."""
import asyncio
import json
import multiprocessing
from pathlib import Path
from queue import Empty

import httpx
import pytest

import xhs_fly_budget as budget

CFG={'url':'https://animalshome-xhs-review.fly.dev','token':'a'*64,
     'machine_id':'123456789abcde','fly_token':'fly-review-'+'x'*60,
     'image_digest':'sha256:'+'b'*64}


def _machine(*,override=False,started=False):
    config={'guest':{'cpu_kind':'shared','cpus':2,'memory_mb':2048},
            'restart':{'policy':'no'},'services':[{'autostart':False}]}
    if override:config['init']={'entrypoint':['/bin/sleep'],'cmd':['infinity']}
    return {'id':CFG['machine_id'],'region':'iad','state':'started' if started else 'stopped','config':config,
            'image_ref':{'digest':CFG['image_digest']}}


def _debit_process(path,barrier,queue,started,index):
    """Matches the separate Python process used by the xhs-status maintenance command."""
    import xhs_fly_budget as child
    child.PATH=Path(path)
    child.LOCK=asyncio.Lock()
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,url):
            observed=bool(started.value)
            await asyncio.sleep(.3+index*1.5) # Distinct response times avoid Windows replace collisions.
            return httpx.Response(200,json=_machine(started=observed),request=httpx.Request('GET',url))
        async def post(self,url,**kwargs):
            queue.put('start-attempt')
            started.value=1
            return httpx.Response(200,json={},request=httpx.Request('POST',url))
    child.httpx.AsyncClient=Client
    barrier.wait(timeout=15)
    asyncio.run(child.ensure_started(CFG))


def test_two_process_start_attempts_cannot_lose_a_durable_debit(tmp_path):
    ctx=multiprocessing.get_context('spawn')
    path=tmp_path/'budget.json';barrier=ctx.Barrier(2);queue=ctx.Queue();started=ctx.Value('b',0)
    jobs=[ctx.Process(target=_debit_process,args=(str(path),barrier,queue,started,i)) for i in range(2)]
    for job in jobs:job.start()
    for job in jobs:job.join(25)
    try:
        assert all(job.exitcode==0 for job in jobs),[job.exitcode for job in jobs]
        attempts=[]
        while True:
            try:attempts.append(queue.get_nowait())
            except Empty:break
        assert attempts
        ledger=json.loads(path.read_text(encoding='utf-8'))
        assert ledger['spent_micro']==budget.RESERVE_MICRO+len(attempts)*budget.LEASE_MICRO
    finally:
        for job in jobs:
            if job.is_alive():job.terminate();job.join()


def test_fly_init_override_cannot_remove_the_paid_runtime_deadline(tmp_path,monkeypatch):
    monkeypatch.setattr(budget,'PATH',tmp_path/'budget.json')
    monkeypatch.setattr(budget,'LOCK',asyncio.Lock())
    calls=[]
    def handler(request):
        calls.append((request.method,request.url.path))
        return httpx.Response(200,json=_machine(override=True) if request.method=='GET' else {})
    original=httpx.AsyncClient
    monkeypatch.setattr(budget.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    try:asyncio.run(budget.ensure_started(CFG))
    except RuntimeError:pass
    assert not any(path.endswith('/start') for _,path in calls),'unsafe init override was started without the 300s timeout entrypoint'
