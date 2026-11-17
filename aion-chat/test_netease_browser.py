"""Optional isolated browser acceptance: no user profile, account, or live API.

Run with Playwright installed and EDGE_EXECUTABLE pointing to a browser binary.
"""
import functools
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright=None


@unittest.skipUnless(sync_playwright and os.environ.get('EDGE_EXECUTABLE'), 'optional isolated browser tools')
class MusicBrowserTests(unittest.TestCase):
    def test_mobile_account_and_cloud_controls(self):
        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *_): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(Path(__file__).parent)))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        writes=[];errors=[]
        state={'logged_in':False,'cookie_configured':False,'account':{'user_id':'','nickname':''},
               'permissions':{'local_playlists':True,'cloud_playlists':False,'cloud_likes':False}}
        def mock(route):
            request=route.request;path=request.url.split('/api/music-station',1)[1]
            data=state
            if path=='/playlists':data={'playlists':[]}
            elif path.startswith('/tracks'):data={'tracks':[]}
            elif path=='/account/qr' and request.method=='POST':data={'handle':'test-handle','url':'https://music.163.com/login?codekey=synthetic','expires_in':300}
            elif path=='/account/qr/test-handle':data={'state':'confirm','account':{'user_id':'1','nickname':'test-user'}} if request.method=='GET' else {'ok':True}
            elif path.endswith('/confirm'):
                state.update(logged_in=True,cookie_configured=True,account={'user_id':'1','nickname':'test-user'})
            elif path.endswith('/permissions'):
                state['permissions'].update(request.post_data_json)
            elif '/library?' in path:
                if 'kind=playlists' in path:
                    data={'playlists':[{'id':'123','name':'test-playlist','owned':True,'track_count':1}]}
                else:data={'songs':[{'id':1,'name':'test-song','artist':'test-artist','album':'test-album'}],'next_offset':None,'name':'test-playlist','total':1}
            elif path=='/account/action':
                writes.append(request.post_data_json);data={'ok':True,'message':'saved'}
            elif path=='/account/import':data={'ok':True,'added':1,'next_offset':None,'total':1}
            route.fulfill(status=200,content_type='application/json',body=json.dumps(data))
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=os.environ['EDGE_EXECUTABLE'],headless=True)
                page=browser.new_page(viewport={'width':390,'height':844},device_scale_factor=1)
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.route('**/api/music-station**',mock)
                page.goto(f'http://127.0.0.1:{server.server_port}/static/music-station.html')
                page.locator('#accountButton').click()
                page.locator('#qrLoginButton').click()
                page.locator('#neteaseQr canvas').wait_for(state='attached')
                self.assertFalse(state['logged_in'])
                page.locator('#confirmAccountButton').wait_for(state='visible',timeout=10000)
                self.assertFalse(state['logged_in'])
                page.locator('#confirmAccountButton').click()
                page.get_by_role('button',name='test-playlist · 1首',exact=True).wait_for()
                self.assertFalse(state['permissions']['cloud_playlists'])
                page.locator('[data-permission=cloud_playlists]').check()
                page.get_by_role('button',name='test-playlist · 1首',exact=True).click()
                page.get_by_role('button',name='存到点歌台',exact=True).wait_for()
                page.locator('#cloudWriteTarget').select_option('123')
                page.get_by_role('button',name='加入云歌单',exact=True).click()
                page.get_by_role('button',name='加入云歌单',exact=True).wait_for(state='visible')
                page.wait_for_function('document.querySelector("#cloudStatus").textContent === "saved"')
                self.assertEqual(writes[0]['playlist_id'],'123')
                self.assertEqual(writes[0]['songs'][0]['id'],1)
                page.locator('#importCloudButton').click()
                page.wait_for_function('document.querySelector("#cloudStatus").textContent.includes("新增 1 首")')
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
                self.assertEqual(errors,[])
                page.screenshot(path=str(Path(tempfile.gettempdir())/'animalshome-netease-mobile.png'))
                page.locator('#closeAccountButton').click()
                self.assertFalse(page.locator('#neteaseAccountDialog').is_visible())
                browser.close()
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
