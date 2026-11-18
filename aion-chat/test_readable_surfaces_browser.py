"""Optional visual acceptance with actual page CSS and synthetic content only."""
import functools
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import os
from pathlib import Path
import re
import tempfile
import threading
import unittest
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright=None


@unittest.skipUnless(sync_playwright and os.environ.get('EDGE_EXECUTABLE'),'optional isolated browser tools')
class ReadableSurfacesTests(unittest.TestCase):
    def test_ib_labels_and_repair_overlays_remain_readable_without_blur(self):
        root=Path(__file__).parent
        class Handler(SimpleHTTPRequestHandler):
            def log_message(self,*_):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(root)))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        settings=root/'static/settings.html'
        inline=re.search(r'<style>([\s\S]*?)</style>',settings.read_text(encoding='utf-8'))[1]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=os.environ['EDGE_EXECUTABLE'],headless=True)
                page=browser.new_page(viewport={'width':390,'height':780})
                page.goto(base+'/static/repair.html')
                # Replace only the isolated fixture document; no application APIs.
                page.set_content(f'''<!doctype html><html data-skin="ib" style="--ib-card-a:.05;--ib-input-a:.03;--ib-blur:0px;--ib-text-shadow:0 0 2px white">
                <head><link rel="stylesheet" href="{base}/static/common.css"><style>{inline}</style>
                <link rel="stylesheet" href="{base}/static/ib/ib-skin.css"></head>
                <body class="settings-page sub-page" data-theme="dark"><section class="settings-card" style="padding:24px">
                <h2>模型配置</h2><div class="model-card"><p class="model-index">模型 1</p><input value="示例模型">
                <div class="model-options"><label class="vision-toggle"><input type="checkbox" checked>看图</label>
                <label class="vision-toggle"><input type="checkbox">听音频</label><label class="vision-toggle"><input type="checkbox">看视频</label>
                <label class="vision-toggle"><input type="checkbox" checked>默认思考</label></div></div></section></body></html>''')
                page.locator('.vision-toggle').first.wait_for()
                for label in page.locator('.vision-toggle').all():
                    style=label.evaluate('(e)=>({color:getComputedStyle(e).color,bg:getComputedStyle(e).backgroundColor,shadow:getComputedStyle(e).textShadow})')
                    self.assertEqual(style['color'],'rgb(36, 52, 80)')
                    self.assertEqual(style['bg'],'rgb(244, 247, 252)')
                    self.assertEqual(style['shadow'],'none')
                page.locator('.vision-toggle').first.click()
                self.assertFalse(page.locator('.vision-toggle input').first.is_checked())
                page.screenshot(path=str(Path(tempfile.gettempdir())/'animalshome-readable-settings.png'))
                repair_html=(root/'static/repair.html').read_text(encoding='utf-8')
                body=re.search(r'<body[^>]*>([\s\S]*?)</body>',repair_html)[1]
                body=re.sub(r'<script[\s\S]*?</script>','',body)
                page.set_content(f'''<!doctype html><html data-skin="ib" style="--ib-card-a:.03;--ib-input-a:.02;--ib-blur:0px">
                <head><link rel="stylesheet" href="{base}/static/common.css"><link rel="stylesheet" href="{base}/static/repair.css">
                <link rel="stylesheet" href="{base}/static/ib/ib-skin.css"></head><body class="repair-room" data-theme="dark">{body}</body></html>''')
                # Fixture setup exercises the real controls without pairing or writes.
                page.evaluate('''() => {
                  document.querySelector('#pairing').hidden=true;
                  document.querySelector('#workspace').hidden=false;
                  document.querySelector('#taskView').hidden=false;
                  document.querySelector('#messages').innerHTML='<article class="message connor">示例聊天文字，用于检查底层文字是否透出。</article>'.repeat(8);
                  document.querySelector('.composer-tools').hidden=false;
                }''')
                for selector in ['.composer','.composer-tools','.composer textarea']:
                    style=page.locator(selector).evaluate('(e)=>({color:getComputedStyle(e).color,bg:getComputedStyle(e).backgroundColor})')
                    self.assertTrue(style['bg'] in ['rgb(244, 247, 252)','rgb(255, 255, 255)'],(selector,style))
                    self.assertEqual(style['color'],'rgb(36, 52, 80)')
                page.screenshot(path=str(Path(tempfile.gettempdir())/'animalshome-readable-repair-tools.png'))
                page.evaluate('''() => {document.querySelector('.composer-tools').hidden=true;document.querySelector('.task-drawer').showModal();}''')
                style=page.locator('.task-drawer').evaluate('(e)=>({bg:getComputedStyle(e).backgroundColor,color:getComputedStyle(e).color})')
                self.assertEqual(style,{'bg':'rgb(244, 247, 252)','color':'rgb(36, 52, 80)'})
                page.screenshot(path=str(Path(tempfile.gettempdir())/'animalshome-readable-repair-drawer.png'))
                browser.close()
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
