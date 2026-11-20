"""Owner image upgrade: temporary private settings, synthetic Fly/gateway HTTP."""
import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import xhs_remote as remote
import xhs_fly_budget as budget
import xhs_lite
from routes import xhs_lite as routes

CFG={'url':'https://animalshome-xhs-review.fly.dev','token':'a'*64,
     'machine_id':'123456789abcde','fly_token':'FlyV1 synthetic-app-only-token-long-enough',
     'image_digest':'sha256:'+'b'*64}
NEW='sha256:'+'c'*64
PATH='/api/xhs-lite/cloud-connection/image'
HEADERS={'X-Requested-With':'AnimalsHome'}


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(remote,'PATH',tmp_path/'remote.json')
    monkeypatch.setattr(budget,'PATH',tmp_path/'budget.json')
    monkeypatch.setattr(xhs_lite,'CONFIG_PATH',tmp_path/'account.json')
    for module,field in [(remote,'LOCK'),(budget,'LOCK'),(xhs_lite,'_ACCOUNT_LOCK'),(routes,'_LOGIN_LOCK')]:
        monkeypatch.setattr(module,field,asyncio.Lock())
    remote.save(**CFG)
    xhs_lite.save_config({'cookie':'synthetic-original-account-cookie','cloud_login':True,
        'logged_in_user_id':'synthetic-original-account','enabled':True,'auto_enabled':False})
    app=FastAPI();app.include_router(routes.router)
    return TestClient(app)


def test_cross_site_and_missing_owner_header_never_reach_gateway(setup,monkeypatch):
    call=AsyncMock();monkeypatch.setattr(remote,'call',call)
    assert setup.post(PATH,json={'image_digest':NEW}).status_code==403
    assert setup.post(PATH,json={'image_digest':NEW},
        headers={**HEADERS,'Sec-Fetch-Site':'cross-site'}).status_code==403
    call.assert_not_awaited();assert remote.load()==CFG


@pytest.mark.parametrize('digest',['','sha256:'+'C'*64,'sha256:'+'c'*63,'https://evil.invalid/image','sha256:'+'c'*64+'\n'])
def test_invalid_digest_is_rejected_before_any_remote_operation(setup,monkeypatch,digest):
    call=AsyncMock();monkeypatch.setattr(remote,'call',call)
    assert setup.post(PATH,json={'image_digest':digest},headers=HEADERS).status_code==400
    call.assert_not_awaited();assert remote.load()==CFG


def test_only_digest_changes_even_if_caller_supplies_other_keys(setup,monkeypatch):
    before=xhs_lite.load_config()
    call=AsyncMock(return_value={'available':True});monkeypatch.setattr(remote,'call',call)
    result=setup.post(PATH,headers=HEADERS,json={'image_digest':NEW,'url':'https://evil.invalid',
        'token':'evil','fly_token':'evil','machine_id':'evil','cookie':'replacement','enabled':False})
    assert result.status_code==200 and result.json()=={'ok':True}
    assert remote.load()=={**CFG,'image_digest':NEW}
    assert xhs_lite.load_config()==before
    call.assert_awaited_once_with('status',connection={**CFG,'image_digest':NEW})
    assert CFG['token'] not in result.text and CFG['fly_token'] not in result.text


@pytest.mark.parametrize('result',[{'available':False},{}])
def test_failed_gateway_readiness_preserves_private_binding(setup,monkeypatch,result):
    monkeypatch.setattr(remote,'call',AsyncMock(return_value=result))
    before=xhs_lite.load_config()
    assert setup.post(PATH,json={'image_digest':NEW},headers=HEADERS).status_code==400
    assert remote.load()==CFG and xhs_lite.load_config()==before


def install_api(monkeypatch,*,digest=NEW):
    calls=[]
    def handler(request):
        calls.append((request.method,request.url.host,request.url.path))
        if request.url.host=='api.machines.dev':
            data={'id':CFG['machine_id'],'region':'iad','state':'stopped',
                  'image_ref':{'digest':digest},'config':{
                      'guest':{'cpu_kind':'shared','cpus':2,'memory_mb':2048},
                      'restart':{'policy':'no'},'services':[{'autostart':False}]}}
            return httpx.Response(200,json=data if request.method=='GET' else {})
        assert request.url.host=='animalshome-xhs-review.fly.dev'
        return httpx.Response(200,json={'available':True})
    client=httpx.AsyncClient
    monkeypatch.setattr(remote.httpx,'AsyncClient',lambda **kw:client(transport=httpx.MockTransport(handler),**kw))
    return calls


def test_real_budget_cap_failure_cannot_save_new_digest(setup,monkeypatch):
    calls=install_api(monkeypatch)
    budget.save({'spent_micro':budget.LIMIT_MICRO,'lease_until':0,'locked':True})
    assert setup.post(PATH,json={'image_digest':NEW},headers=HEADERS).status_code==400
    assert remote.load()==CFG and budget.load()['locked']
    assert not any(path.endswith('/start') for _,_,path in calls)
    assert not any(host=='animalshome-xhs-review.fly.dev' for _,host,_ in calls)


def test_real_machine_digest_mismatch_cannot_save_binding(setup,monkeypatch):
    calls=install_api(monkeypatch,digest=CFG['image_digest'])
    assert setup.post(PATH,json={'image_digest':NEW},headers=HEADERS).status_code==400
    assert remote.load()==CFG and budget.load()['locked']
    assert not any(path.endswith('/start') for _,_,path in calls)


def test_valid_owner_upgrade_unlocks_repaired_config_without_resetting_spend(setup,monkeypatch):
    calls=install_api(monkeypatch)
    before=xhs_lite.load_config()
    budget.save({'spent_micro':2_100_000,'lease_until':0,'locked':True,
                 'target':'animalshome-xhs-review/'+CFG['machine_id']})
    assert setup.post(PATH,json={'image_digest':NEW},headers=HEADERS).status_code==200
    assert remote.load()=={**CFG,'image_digest':NEW}
    assert xhs_lite.load_config()==before
    assert budget.load()['spent_micro']==2_100_000+budget.LEASE_MICRO
    assert not budget.load()['locked']
    assert sum(path.endswith('/start') for _,_,path in calls)==1
