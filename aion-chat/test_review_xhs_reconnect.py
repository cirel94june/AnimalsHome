"""Independent owner-reconnect checks: fake Fly responses, temporary ledgers."""
import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import xhs_fly_budget as budget

CFG={'url':'https://animalshome-xhs-review.fly.dev','token':'a'*64,
     'machine_id':'123456789abcde','fly_token':'FlyV1 synthetic-app-token-long-enough',
     'image_digest':'sha256:'+'b'*64}


@pytest.fixture
def fake(tmp_path,monkeypatch):
    monkeypatch.setattr(budget,'PATH',tmp_path/'budget.json')
    monkeypatch.setattr(budget,'LOCK',asyncio.Lock())
    machine={'id':CFG['machine_id'],'region':'iad','state':'stopped',
             'image_ref':{'digest':CFG['image_digest']},
             'config':{'guest':{'cpu_kind':'shared','cpus':2,'memory_mb':2048},
                       'restart':{'policy':'no'},'services':[{'autostart':False}]}}
    calls=[]
    def transport(request):
        calls.append((request.method,request.url.path))
        return httpx.Response(200,json=machine if request.method=='GET' else {})
    client=httpx.AsyncClient
    monkeypatch.setattr(budget.httpx,'AsyncClient',lambda **kw:client(transport=httpx.MockTransport(transport),**kw))
    return machine,calls


def locked(spent):
    budget.save({'spent_micro':spent,'lease_until':123456789,'locked':True,
                 'target':'animalshome-xhs-review/'+CFG['machine_id']})


def test_explicit_owner_repair_preserves_spend_and_debits_new_start(fake):
    machine,calls=fake;locked(2_100_000)
    asyncio.run(budget.ensure_started(CFG,owner_reconnect=True))
    state=budget.load()
    assert state['spent_micro']==2_100_000+budget.LEASE_MICRO
    assert not state['locked'] and state['lease_until']>123456789
    assert sum(path.endswith('/start') for _,path in calls)==1


def test_normal_status_never_clears_configuration_lock(fake):
    _,calls=fake;locked(2_100_000)
    with pytest.raises(RuntimeError):asyncio.run(budget.ensure_started(CFG))
    assert budget.load()['locked'] and budget.load()['spent_micro']==2_100_000
    assert not any(path.endswith('/start') for _,path in calls)


@pytest.mark.parametrize('spent',[budget.LIMIT_MICRO,budget.LIMIT_MICRO+1])
def test_owner_never_unlocks_exhausted_budget(fake,spent):
    _,calls=fake;locked(spent)
    with pytest.raises(RuntimeError):asyncio.run(budget.ensure_started(CFG,owner_reconnect=True))
    assert budget.load()['locked'] and budget.load()['spent_micro']==spent
    assert not any(path.endswith('/start') for _,path in calls)


@pytest.mark.parametrize('bad',['digest','autostart','init','region'])
def test_owner_cannot_unlock_invalid_replacement(fake,bad):
    machine,calls=fake;locked(2_100_000)
    if bad=='digest':machine['image_ref']['digest']='sha256:'+'c'*64
    elif bad=='autostart':machine['config']['services'][0]['autostart']=True
    elif bad=='init':machine['config']['init']={'cmd':['sleep','infinity']}
    else:machine['region']='sin'
    with pytest.raises(RuntimeError):asyncio.run(budget.ensure_started(CFG,owner_reconnect=True))
    assert budget.load()['locked'] and budget.load()['spent_micro']==2_100_000
    assert not any(path.endswith('/start') for _,path in calls)


def test_exact_cap_boundary_remains_latched_even_when_stopping_live_vm(fake):
    machine,calls=fake;machine['state']='started'
    locked(budget.LIMIT_MICRO-budget.LEASE_MICRO)
    with pytest.raises(RuntimeError):asyncio.run(budget.ensure_started(CFG,owner_reconnect=True))
    assert budget.load()['locked'],'a budget-insufficient reconnect cleared the durable stop latch'
    assert not any(path.endswith('/start') for _,path in calls)


def test_only_protected_owner_post_can_pass_candidate_connection(monkeypatch):
    import xhs_remote
    import xhs_lite
    from routes import xhs_lite as routes
    monkeypatch.setattr(routes,'_LOGIN_LOCK',asyncio.Lock())
    monkeypatch.setattr(xhs_lite,'_ACCOUNT_LOCK',asyncio.Lock())
    remote_call=AsyncMock(return_value={'available':True})
    monkeypatch.setattr(xhs_remote,'call',remote_call)
    monkeypatch.setattr(xhs_remote,'load',lambda:None)
    monkeypatch.setattr(xhs_remote,'save',lambda **kw:None)
    monkeypatch.setattr(xhs_lite,'save_config',lambda updates:None)
    app=FastAPI();app.include_router(routes.router);client=TestClient(app)
    assert client.post('/api/xhs-lite/cloud-connection',json=CFG).status_code==403
    assert client.post('/api/xhs-lite/cloud-connection',json=CFG,
        headers={'X-Requested-With':'AnimalsHome','Sec-Fetch-Site':'cross-site'}).status_code==403
    remote_call.assert_not_awaited()
    response=client.post('/api/xhs-lite/cloud-connection',json=CFG,headers={'X-Requested-With':'AnimalsHome'})
    assert response.status_code==200
    remote_call.assert_awaited_once_with('status',connection=CFG)
