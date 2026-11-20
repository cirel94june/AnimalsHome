"""Temporary ledgers, fake Fly API and browser; no real accounts or billable VM."""
import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
import xhs_fly_budget as budget
import xhs_remote as remote
import xhs_fly_service as service

CFG={'url':'https://animalshome-xhs-test.fly.dev','token':'a'*64,
     'machine_id':'123456789abcde','fly_token':'fly-test-'+'x'*60,'image_digest':'sha256:'+'b'*64}


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(budget,'PATH',tmp_path/'budget.json')
    monkeypatch.setattr(remote,'PATH',tmp_path/'remote.json')
    monkeypatch.setattr(budget,'LOCK',asyncio.Lock())
    monkeypatch.setattr(remote,'LOCK',asyncio.Lock())
    return tmp_path


def install_fly(monkeypatch,*,started=False,region='iad',fail_start=False):
    calls=[];status={'state':'started' if started else 'stopped'}
    def handler(request):
        calls.append((request.method,request.url.path))
        if request.method=='GET':
            return httpx.Response(200,json={'id':CFG['machine_id'],'state':status['state'],'region':region,'image_ref':{'digest':CFG['image_digest']},
                'config':{'guest':{'cpu_kind':'shared','cpus':2,'memory_mb':2048},
                          'restart':{'policy':'no'},'services':[{'autostart':False}]}})
        if request.url.path.endswith('/start'):
            status['state']='started'
            if fail_start:return httpx.Response(500,json={})
        else:status['state']='stopped'
        return httpx.Response(200,json={})
    original=httpx.AsyncClient
    monkeypatch.setattr(budget.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    return calls,status


def test_concurrent_start_is_debited_once_and_survives_restart(isolated,monkeypatch):
    calls,_=install_fly(monkeypatch)
    async def run():await asyncio.gather(*(budget.ensure_started(CFG) for _ in range(4)))
    asyncio.run(run())
    assert sum(path.endswith('/start') for _,path in calls)==1
    assert budget.load()['spent_micro']==budget.RESERVE_MICRO+budget.LEASE_MICRO
    assert budget.load()['target']=='animalshome-xhs-test/'+CFG['machine_id']


def test_unknown_start_result_never_refunds_reserved_charge(isolated,monkeypatch):
    install_fly(monkeypatch,fail_start=True)
    with pytest.raises(httpx.HTTPStatusError):asyncio.run(budget.ensure_started(CFG))
    assert budget.load()['spent_micro']==budget.RESERVE_MICRO+budget.LEASE_MICRO


def test_running_without_lease_is_stopped_not_reused(isolated,monkeypatch):
    calls,status=install_fly(monkeypatch,started=True)
    with pytest.raises(RuntimeError):asyncio.run(budget.ensure_started(CFG))
    assert status['state']=='stopped'
    assert not any(path.endswith('/start') for _,path in calls)


def test_cap_is_durable_and_never_starts_again(isolated,monkeypatch):
    calls,_=install_fly(monkeypatch)
    budget.save({'spent_micro':budget.LIMIT_MICRO-1,'lease_until':0,'locked':False})
    with pytest.raises(RuntimeError):asyncio.run(budget.ensure_started(CFG))
    assert budget.load()['locked'] is True
    with pytest.raises(RuntimeError):asyncio.run(budget.ensure_started(CFG))
    assert not any(path.endswith('/start') for _,path in calls)


def test_wrong_hardware_region_or_broken_ledger_fails_closed(isolated,monkeypatch):
    calls,_=install_fly(monkeypatch,region='sin')
    with pytest.raises(RuntimeError):asyncio.run(budget.ensure_started(CFG))
    assert budget.load()['locked'] is True
    budget.PATH.write_text('{bad',encoding='utf-8')
    with pytest.raises(RuntimeError):asyncio.run(budget.ensure_started(CFG))
    assert not any(path.endswith('/start') for _,path in calls)


@pytest.mark.parametrize('url',['http://animalshome-xhs-test.fly.dev','https://localhost',
    'https://animalshome-xhs-test.fly.dev.evil.invalid','https://animalshome-xhs-test.fly.dev@127.0.0.1',
    'https://jasper-telegrambot.fly.dev','https://animalshome-xhs-test.fly.dev/path',
    'https://animalshome-xhs-test.fly.dev\n.evil.invalid'])
def test_connection_rejects_other_apps_and_url_bypasses(isolated,url):
    with pytest.raises(ValueError):remote.validate(**{**CFG,'url':url})


def test_connection_roundtrip_is_private_and_does_not_reset_budget(isolated):
    budget.save({'spent_micro':2_100_000,'lease_until':0,'locked':False})
    remote.save(**CFG)
    assert remote.load()==CFG
    assert budget.load()['spent_micro']==2_100_000
    remote.PATH.write_text('{broken',encoding='utf-8')
    with pytest.raises(RuntimeError):remote.load()


def test_gateway_auth_allowlist_transfer_and_hidden_credentials(isolated,monkeypatch):
    monkeypatch.setattr(service,'ROOT',isolated)
    monkeypatch.setattr(service,'TOKEN','a'*64)
    monkeypatch.setattr(service,'LOCK',asyncio.Lock())
    browser=type('Browser',(),{'call':AsyncMock(return_value=({'available':True},'application/json'))})()
    monkeypatch.setattr(service,'BROWSER',browser)
    client=TestClient(service.app) # no lifespan/browser launch in this boundary test
    assert client.post('/candidate').status_code==401
    headers={'Authorization':'Bearer '+'a'*64}
    assert client.post('/arbitrary-url',headers=headers,json={}).status_code==404
    assert client.post('/worker',headers=headers,json={'endpoint':'publish','body':{}}).status_code==503
    browser.call.assert_not_awaited()
    assert client.post('/status',headers=headers,json={}).json()=={'available':True}
    assert client.post('/status',headers=headers,content=b'x'*96001).status_code==413
    service.atomic_json(isolated/'transfer.json',{'bytes':service.MAX_OUTPUT})
    assert client.post('/status',headers=headers,json={}).status_code==423


def test_vm_deadline_and_no_proxy_autostart_are_in_deployment_source():
    from pathlib import Path
    root=Path(__file__).parent.parent/'deploy'/'fly-xhs'
    script=(root/'entrypoint.sh').read_text(encoding='utf-8')
    config=(root/'fly.toml').read_text(encoding='utf-8')
    assert '--kill-after=15s 300s' in script
    assert 'auto_start_machines = false' in config and 'policy = "no"' in config
