"""Cloud login boundary tests: no live browser, model, account, or chat writes."""
import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import xhs_browser
import xhs_browser_service as service
import xhs_lite
from routes import xhs_lite as routes


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(xhs_lite,'CONFIG_PATH',tmp_path/'config.json')
    monkeypatch.setattr(xhs_lite,'actor_label',lambda actor:actor)
    monkeypatch.setattr(xhs_lite,'_seat_ready',lambda actor:False)
    xhs_lite.save_config({'cookie':'old','logged_in_user_id':'old-account','enabled':True,'auto_enabled':False})
    app=FastAPI();app.include_router(routes.router)
    routes._LOGIN_LOCK=asyncio.Lock()
    return TestClient(app)


HEADERS={'X-Requested-With':'AnimalsHome'}


def test_login_ui_rejects_cross_site_and_hidden_operations(setup,monkeypatch):
    call=AsyncMock();monkeypatch.setattr(xhs_browser,'call',call)
    for operation in ['start','frame','confirm','cancel']:
        assert setup.post('/api/xhs-lite/cloud-login/'+operation).status_code==403
        assert setup.post('/api/xhs-lite/cloud-login/'+operation,headers={**HEADERS,'Sec-Fetch-Site':'cross-site'}).status_code==403
    for operation in ['credentials','accept','candidate']:
        assert setup.post('/api/xhs-lite/cloud-login/'+operation,headers=HEADERS).status_code==422
    call.assert_not_awaited()


def test_failed_verification_never_replaces_old_cookie(setup,monkeypatch):
    call=AsyncMock(return_value={'cookie':'new-secret'})
    monkeypatch.setattr(xhs_browser,'call',call)
    monkeypatch.setattr(xhs_lite,'_run_worker_local',AsyncMock(return_value={'logged_in':False}))
    result=setup.post('/api/xhs-lite/cloud-login/confirm',headers=HEADERS)
    assert result.status_code==503 and 'new-secret' not in result.text
    assert xhs_lite.load_config()['cookie']=='old'
    assert [item.args[0] for item in call.await_args_list]==['candidate']


def test_verified_confirm_keeps_switches_and_never_exposes_credentials(setup,monkeypatch):
    call=AsyncMock(side_effect=[{'cookie':'new-secret'},{'ok':True}])
    monkeypatch.setattr(xhs_browser,'call',call)
    monkeypatch.setattr(xhs_lite,'_run_worker_local',AsyncMock(return_value={'logged_in':True,'user_id':'chosen-account','nickname':'reader'}))
    result=setup.post('/api/xhs-lite/cloud-login/confirm',headers=HEADERS)
    assert result.status_code==200 and result.json()['ok']
    assert 'new-secret' not in result.text and result.headers['cache-control']=='no-store'
    cfg=xhs_lite.load_config()
    assert cfg['cookie']=='new-secret' and cfg['cloud_login']
    assert cfg['enabled'] and not cfg['auto_enabled'] and not cfg['allow_write_comments']
    assert call.await_args_list[1].args==('accept',{'expected_hash':hashlib.sha256(b'new-secret').hexdigest()})
    public=setup.get('/api/xhs-lite/config').json()
    assert 'new-secret' not in json.dumps(public) and 'cookie' not in public


def test_accept_failure_keeps_old_account(setup,monkeypatch):
    monkeypatch.setattr(xhs_browser,'call',AsyncMock(side_effect=[{'cookie':'changed'},RuntimeError('account changed')]))
    monkeypatch.setattr(xhs_lite,'_run_worker_local',AsyncMock(return_value={'logged_in':True,'user_id':'chosen-account'}))
    assert setup.post('/api/xhs-lite/cloud-login/confirm',headers=HEADERS).status_code==503
    assert xhs_lite.load_config()['cookie']=='old'


def test_expired_old_login_is_not_reported_as_success(setup,monkeypatch):
    monkeypatch.setattr(xhs_lite,'check_login',AsyncMock(return_value={'logged_in':False}))
    assert setup.post('/api/xhs-lite/check-login').json()['ok'] is False


def test_auto_refresh_enforces_account_and_never_falls_back(setup,monkeypatch):
    xhs_lite.save_config({'cloud_login':True})
    monkeypatch.setattr(xhs_browser,'call',AsyncMock(return_value={'cookie':'renewed'}))
    local=AsyncMock(return_value={'logged_in':True,'user_id':'different-account'})
    monkeypatch.setattr(xhs_lite,'_run_worker_local',local)
    with pytest.raises(RuntimeError,match='账号有变化'):
        asyncio.run(xhs_lite._run_worker('list-feeds',cookie='old'))
    assert local.await_count==1 and local.await_args.args[0]=='check-login'
    assert xhs_lite.load_config()['cookie']=='old'
    local.reset_mock();local.return_value={'logged_in':True,'user_id':'old-account'}
    asyncio.run(xhs_lite._run_worker('list-feeds',cookie='old'))
    assert local.await_count==2 and local.await_args.kwargs['cookie']=='renewed'
    assert xhs_lite.load_config()['cookie']=='renewed'


def test_cloud_login_rejects_all_write_paths_even_if_enabled(setup,monkeypatch):
    xhs_lite.save_config({'cloud_login':True,'allow_write_comments':True})
    assert not xhs_lite.load_config()['allow_write_comments']
    call=AsyncMock();local=AsyncMock()
    monkeypatch.setattr(xhs_browser,'call',call)
    monkeypatch.setattr(xhs_lite,'_run_worker_local',local)
    for endpoint in ['post-comment','reply-comment','like-feed','publish','unknown']:
        with pytest.raises(RuntimeError,match='只开放阅读'):
            asyncio.run(xhs_lite._run_worker(endpoint,cookie='old'))
    call.assert_not_awaited();local.assert_not_awaited()


def test_service_accept_requires_exact_verified_snapshot(tmp_path,monkeypatch):
    monkeypatch.setattr(service,'ROOT',tmp_path)
    browser=service.LoginBrowser()
    browser.candidate=AsyncMock(return_value={'cookie':'actual','updated_at':123})
    browser.close=AsyncMock()
    with pytest.raises(RuntimeError,match='account_changed'):
        asyncio.run(browser.call('/accept',{'expected_hash':hashlib.sha256(b'verified-old').hexdigest()}))
    assert not (tmp_path/'session.json').exists()
    asyncio.run(browser.call('/accept',{'expected_hash':hashlib.sha256(b'actual').hexdigest()}))
    assert json.loads((tmp_path/'session.json').read_text())['cookie']=='actual'


def test_atomic_config_failure_preserves_prior_credentials(setup,monkeypatch):
    def failed(*args):raise OSError('disk full')
    monkeypatch.setattr(xhs_lite.os,'replace',failed)
    with pytest.raises(OSError):xhs_lite.save_config({'cookie':'new'})
    assert xhs_lite.load_config()['cookie']=='old'
    assert not list(xhs_lite.CONFIG_PATH.parent.glob('.xhs-config-*'))


def test_browser_launch_refuses_low_memory(monkeypatch):
    class Info:
        def read_text(self):return 'MemAvailable: 280000 kB\n'
    monkeypatch.setattr(service,'Path',lambda value:Info())
    browser=service.LoginBrowser();browser._launch=AsyncMock()
    with pytest.raises(RuntimeError,match='insufficient_memory'):asyncio.run(browser.open())
    browser._launch.assert_not_awaited()


def test_supervisor_exits_and_cancels_server_after_reaper_failure():
    async def probe():
        stopped=asyncio.Event()
        class Server:
            async def serve_forever(self):
                try:await asyncio.Event().wait()
                finally:stopped.set()
        browser=service.LoginBrowser()
        browser.reap=AsyncMock(side_effect=RuntimeError('cleanup failed'))
        with pytest.raises(RuntimeError,match='cleanup failed'):
            await asyncio.wait_for(service.supervise(Server(),browser),1)
        assert stopped.is_set()
    asyncio.run(probe())


def test_old_roam_cannot_restore_cleared_cookie_between_reads(setup,monkeypatch):
    xhs_lite.save_config({'cookie':'','cloud_login':False,'logged_in_user_id':''})
    local=AsyncMock();monkeypatch.setattr(xhs_lite,'_run_worker_local',local)
    with pytest.raises(RuntimeError,match='登录设置刚刚改变'):
        asyncio.run(xhs_lite._run_worker('list-feeds',cookie='old'))
    local.assert_not_awaited()


def test_original_roam_cookie_allows_renewal_of_same_account(setup,monkeypatch):
    xhs_lite.save_config({'cloud_login':True})
    monkeypatch.setattr(xhs_browser,'call',AsyncMock(side_effect=[{'cookie':'old'},{'cookie':'renewed'},{'cookie':'renewed'}]))
    reads=[]
    async def local(endpoint,*args,**kwargs):
        if endpoint=='check-login':return {'logged_in':True,'user_id':'old-account'}
        reads.append(kwargs['cookie']);return {}
    monkeypatch.setattr(xhs_lite,'_run_worker_local',local)
    async def roam():
        await xhs_lite._run_worker('list-feeds',cookie='old')
        await xhs_lite._run_worker('get-feed-detail',cookie='old')
        await xhs_lite._run_worker('get-feed-detail',cookie='old')
    asyncio.run(roam())
    assert reads==['old','renewed','renewed']
    assert xhs_lite.load_config()['cookie']=='renewed'
