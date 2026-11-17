"""Independent, local-only counterexamples for cloud account/lifecycle boundaries."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
import xhs_browser
import xhs_browser_service as service
import xhs_lite


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(xhs_lite,'CONFIG_PATH',tmp_path/'config.json')
    monkeypatch.setattr(xhs_lite,'actor_label',lambda value:value)
    monkeypatch.setattr(xhs_lite,'_seat_ready',lambda value:False)
    xhs_lite.save_config({'cloud_login':True,'cookie':'account-A-old',
                          'logged_in_user_id':'account-A'})
    return tmp_path


@pytest.mark.parametrize('replacement',[
    {'cloud_login':True,'cookie':'account-B-confirmed','logged_in_user_id':'account-B'},
    {'cloud_login':False,'cookie':'','logged_in_user_id':''},
])
def test_refresh_cannot_overwrite_concurrently_confirmed_or_cleared_account(isolated,monkeypatch,replacement):
    reads=[]
    async def credentials(*args,**kwargs):return {'cookie':'account-A-renewed'}
    async def worker(endpoint,*args,**kwargs):
        if endpoint=='check-login':
            # A second owner request completes while the old refresh is suspended.
            xhs_lite.save_config(replacement)
            return {'logged_in':True,'user_id':'account-A'}
        reads.append(kwargs['cookie'])
        return {}
    monkeypatch.setattr(xhs_browser,'call',credentials)
    monkeypatch.setattr(xhs_lite,'_run_worker_local',worker)
    try:asyncio.run(xhs_lite._run_worker('list-feeds',cookie='account-A-old'))
    except RuntimeError:pass
    actual=xhs_lite.load_config()
    assert actual['cookie']==replacement['cookie'], 'stale refresh restored old-account credentials over the latest owner decision'
    assert reads==[], 'old account must not be read after another account is confirmed or credentials are cleared'


def test_background_credentials_never_commit_unconfirmed_interactive_login(tmp_path,monkeypatch):
    monkeypatch.setattr(service,'ROOT',tmp_path)
    accepted={'cookie':'accepted-A','updated_at':1}
    (tmp_path/'session.json').write_text(json.dumps(accepted),encoding='utf-8')
    browser=service.LoginBrowser()
    browser.login_deadline=service.time.monotonic()+300
    browser.open=AsyncMock()
    browser.page=type('Page',(),{'reload':AsyncMock()})()
    browser.candidate=AsyncMock(return_value={'cookie':'unconfirmed-B','updated_at':service.time.time()})
    monkeypatch.setattr(service.asyncio,'sleep',AsyncMock())
    try:asyncio.run(browser.call('/credentials'))
    except RuntimeError:pass
    assert json.loads((tmp_path/'session.json').read_text())==accepted, 'background access persisted the unconfirmed login draft before /accept'


def test_reaper_does_not_silently_die_on_browser_close_failure(monkeypatch):
    browser=service.LoginBrowser()
    browser.page=object()
    browser.last_used=0
    browser.close=AsyncMock(side_effect=RuntimeError('browser pipe closed'))
    class EndProbe(BaseException):pass
    ticks=0
    async def sleep(_):
        nonlocal ticks
        ticks+=1
        if ticks>1:raise EndProbe()
    monkeypatch.setattr(service.asyncio,'sleep',sleep)
    async def probe():
        try:await browser.reap()
        except EndProbe:return
        except RuntimeError:
            pytest.fail('close failure killed only the reaper; serve_forever stays alive without idle cleanup')
    asyncio.run(probe())


def test_cloud_roam_keeps_account_between_worker_calls(isolated,monkeypatch):
    reads=[]
    async def worker(endpoint,*args,**kwargs):
        reads.append(kwargs['cookie'])
        return {}
    async def credentials(*args,**kwargs):
        return {'cookie':xhs_lite.load_config()['cookie']}
    monkeypatch.setattr(xhs_browser,'call',credentials)
    monkeypatch.setattr(xhs_lite,'_run_worker_local',worker)
    async def roam():
        # Same cookie snapshot is carried by _run_explore/_run_actor_roam.
        snapshot=xhs_lite.load_config()['cookie']
        await xhs_lite._run_worker('list-feeds',cookie=snapshot)
        # Owner's new confirmation occurs while the roam is between workers.
        async with xhs_lite._ACCOUNT_LOCK:
            xhs_lite.save_config({'cookie':'account-B-confirmed','logged_in_user_id':'account-B'})
        try:await xhs_lite._run_worker('get-feed-detail',cookie=snapshot)
        except RuntimeError:pass
    asyncio.run(roam())
    assert reads==['account-A-old'], 'one running roam silently mixed A and B despite its original A cookie snapshot'
