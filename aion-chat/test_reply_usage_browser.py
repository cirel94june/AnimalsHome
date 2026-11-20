"""Mobile layout acceptance using synthetic receipts and the actual styles."""
import os
from pathlib import Path
import tempfile
import re

import pytest


def test_mobile_cache_receipt_remains_readable():
    playwright = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('EDGE_EXECUTABLE')
    if not executable:
        pytest.skip('isolated Edge executable not configured')
    root = Path(__file__).parent / 'static'
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=executable, headless=True)
        try:
            page = browser.new_page(viewport={'width': 375, 'height': 812})
            page.set_content('<html data-skin="ib"><body data-theme="dark"><main style="padding:12px"><div class="msg-role-row">AI · 14:20 <div id="receipt"></div></div></main></body></html>')
            for name in ('common.css', 'ib/ib-skin.css', 'reply-usage.css'):
                page.add_style_tag(content=(root / name).read_text(encoding='utf-8'))
            page.add_script_tag(content=(root / 'reply-usage.js').read_text(encoding='utf-8'))
            page.evaluate('''() => { document.querySelector('#receipt').innerHTML = ReplyUsage.render({attachments:[{
                type:'reply_usage',calls:1,reported_calls:1,cache_reported_calls:1,
                input_tokens:1000,output_tokens:120,cache_read_tokens:750}]}); }''')
            page.get_by_text('缓存 75.0%', exact=True).click()
            assert page.get_by_text('已返回用量：', exact=False).is_visible()
            assert page.locator('details').evaluate('(e)=>e.scrollWidth <= 351')
            assert page.locator('.reply-usage > span').evaluate('(e)=>getComputedStyle(e).backgroundColor') == 'rgb(247, 249, 252)'
            page.screenshot(path=str(Path(tempfile.gettempdir())/'animalshome-reply-cache-mobile.png'))
        finally:
            browser.close()


def test_xhs_login_remains_visible_after_scrolling():
    playwright = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('EDGE_EXECUTABLE')
    if not executable:
        pytest.skip('isolated Edge executable not configured')
    root = Path(__file__).parent / 'static'
    source = (root/'xhs-lite.html').read_text(encoding='utf-8')
    source = re.sub(r'<script\b[^>]*>[\s\S]*?</script>', '', source)
    source = source.replace('<html lang="zh-CN">', '<html lang="zh-CN" data-skin="ib">')
    source = source.replace('<link rel="stylesheet" href="/static/common.css">',
                            '<style>'+(root/'common.css').read_text(encoding='utf-8')+'</style>')
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=executable, headless=True)
        try:
            page = browser.new_page(viewport={'width':375,'height':812})
            page.set_content(source)
            page.add_style_tag(content=(root/'ib/ib-skin.css').read_text(encoding='utf-8'))
            page.evaluate('''() => {
                document.querySelector('#configDetails').open=true;
                document.querySelector('#cloudLoginBox').hidden=false;
                const image=document.querySelector('#cloudLoginFrame');image.hidden=false;
                image.src='data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="800"><rect width="1280" height="800" fill="red"/></svg>';
            }''')
            image=page.locator('#cloudLoginFrame')
            image.scroll_into_view_if_needed()
            assert image.is_visible()
            assert page.locator('body').evaluate('(e)=>getComputedStyle(e).backgroundAttachment')=='scroll'
            assert page.locator('.panel').first.evaluate('(e)=>getComputedStyle(e).backdropFilter')=='none'
            assert page.locator('.wrap').evaluate('(e)=>getComputedStyle(e).zIndex')=='1'
            assert image.evaluate('(e)=>{const r=e.getBoundingClientRect();return document.elementFromPoint(r.left+r.width/2,r.top+r.height/2)===e;}')
            page.screenshot(path=str(Path(tempfile.gettempdir())/'animalshome-xhs-scroll-fixed.png'))
        finally:
            browser.close()
