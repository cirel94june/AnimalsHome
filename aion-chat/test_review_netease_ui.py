"""Isolated real browser counterexamples; all account traffic is mocked."""
import functools
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
import json
import os
from pathlib import Path
import threading

import pytest
sync_playwright=pytest.importorskip('playwright.sync_api').sync_playwright
pytestmark=pytest.mark.skipif(not os.environ.get('EDGE_EXECUTABLE'),reason='optional isolated browser tools')


@pytest.fixture
def browser_page():
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(Path(__file__).parent)))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path=os.environ['EDGE_EXECUTABLE'],headless=True)
        page=browser.new_page(viewport={'width':390,'height':844})
        yield page,f'http://127.0.0.1:{server.server_port}/static/music-station.html'
        browser.close()
    server.shutdown();server.server_close();thread.join()


def install_mock(page,*,delay_daily=False):
    state={'logged_in':True,'cookie_configured':True,'account':{'user_id':'1','nickname':'account-A'},
           'permissions':{'local_playlists':True,'cloud_playlists':False,'cloud_likes':False}}
    writes=[];held=[]
    def mock(route):
        path=route.request.url.split('/api/music-station',1)[1]
        data=state
        if path=='/playlists':data={'playlists':[]}
        elif path.startswith('/tracks'):data={'tracks':[]}
        elif path=='/account/logout':state.update(logged_in=False,cookie_configured=False,account={'user_id':'','nickname':''})
        elif path=='/account/cookie':state.update(logged_in=True,cookie_configured=True,account={'user_id':'2','nickname':'account-B'})
        elif path=='/account/action':writes.append(route.request.post_data_json);data={'ok':True,'message':'saved'}
        elif '/library?' in path:
            if delay_daily and 'kind=daily' in path:held.append(route);return
            data={'playlists':[]}
        route.fulfill(status=200,content_type='application/json',body=json.dumps(data))
    page.route('**/api/music-station**',mock)
    return state,writes,held


def test_new_account_does_not_reuse_previous_accounts_operation_id(browser_page):
    page,url=browser_page
    _,writes,_=install_mock(page)
    page.goto(url);page.locator('#accountButton').click()
    page.locator('#accountStatus').get_by_text('已登录：account-A',exact=False).wait_for()
    page.locator('#closeAccountButton').click();page.locator('#cloudLibraryButton').click()
    page.locator('#cloudPlaylistTools summary').click()
    page.locator('#cloudPlaylistName').fill('same-playlist')
    page.locator('#createCloudPlaylistButton').click()
    page.wait_for_function('!document.querySelector("#createCloudPlaylistButton").disabled')
    page.locator('#accountButton').click();page.locator('#logoutAccountButton').click()
    page.wait_for_function('document.querySelector("#accountStatus").textContent.includes("尚未登录")')
    page.locator('summary').filter(has_text='已有 MUSIC_U').click()
    page.locator('#neteaseCookieInput').fill('synthetic-B')
    page.locator('#cookieLoginForm button').click()
    page.wait_for_function('document.querySelector("#accountStatus").textContent.includes("account-B")')
    page.locator('#closeAccountButton').click()
    page.locator('#createCloudPlaylistButton').click()
    page.wait_for_function('!document.querySelector("#createCloudPlaylistButton").disabled')
    assert len(writes)==2
    assert writes[0]['action_id']!=writes[1]['action_id'], 'new account reuses old action id, so real backend rejects its changed account fingerprint'


def test_logout_invalidates_pending_private_library_response(browser_page):
    page,url=browser_page
    _,_,held=install_mock(page,delay_daily=True)
    page.goto(url);page.locator('#accountButton').click()
    page.wait_for_function('document.querySelector("#accountStatus").textContent.includes("account-A")')
    page.locator('#closeAccountButton').click();page.locator('#cloudLibraryButton').click()
    page.locator('[data-kind=daily]').click()
    page.locator('#accountButton').click();page.locator('#logoutAccountButton').click()
    page.wait_for_function('document.querySelector("#accountStatus").textContent.includes("尚未登录")')
    assert len(held)==1
    held[0].fulfill(status=200,content_type='application/json',body=json.dumps({'songs':[{'id':1,'name':'PRIVATE_ACCOUNT_A_RESULT','artist':'A','album':''}],'next_offset':None}))
    page.wait_for_timeout(150)
    assert 'PRIVATE_ACCOUNT_A_RESULT' not in page.locator('#cloudResults').inner_text(), 'late library response repopulated account A data after logout'
