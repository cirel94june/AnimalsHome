"""Mobile layout acceptance using synthetic receipts and the actual styles."""
import os
from pathlib import Path
import tempfile

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
