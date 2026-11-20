"""Real QR detection and enlargement; synthetic codes, no network or account."""
import base64
import os
from pathlib import Path

import pytest


def test_login_qr_is_large_and_sidebar_download_code_is_not_selected():
    pw = pytest.importorskip('playwright.sync_api')
    executable=os.environ.get('EDGE_EXECUTABLE')
    if not executable:pytest.skip('isolated Edge not configured')
    root=Path(__file__).parent/'static'
    with pw.sync_playwright() as engine:
        browser=engine.chromium.launch(executable_path=executable,headless=True)
        try:
            page=browser.new_page(viewport={'width':375,'height':812})
            page.set_content('<div id="generate"></div><img id="source"><canvas id="out" style="width:300px;max-width:100%"></canvas>')
            for name in ('vendor/qrcode.min.js','vendor/jsQR-1.4.0.js','xhs-login-qr.js'):
                page.add_script_tag(content=(root/name).read_text(encoding='utf-8'))
            result=page.evaluate('''async () => {
                const make=text=>{const host=document.createElement('div');document.body.append(host);
                    new QRCode(host,{text,width:140,height:140});return host.querySelector('canvas');};
                const frame=document.createElement('canvas');frame.width=1280;frame.height=800;
                const ctx=frame.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,1280,800);
                ctx.drawImage(make('https://example.invalid/download'),40,590);
                ctx.drawImage(make('https://example.invalid/login-synthetic'),375,330);
                const image=document.querySelector('#source');image.src=frame.toDataURL();await image.decode();
                const output=document.querySelector('#out');
                const found=XhsLoginQR.render(image,output);
                const pixels=output.getContext('2d').getImageData(0,0,output.width,output.height);
                return {found,text:jsQR(pixels.data,output.width,output.height).data,
                    displayed:output.getBoundingClientRect().width,native:output.width};
            }''')
            assert result['found']
            assert result['text']=='https://example.invalid/login-synthetic'
            assert result['displayed']==300 and result['native']>=400
            # Optional one-off local image probe; never persist its code or pixels.
            sample=os.environ.get('ANIMALSHOME_QR_PROBE_PATH')
            if sample:
                encoded=base64.b64encode(Path(sample).read_bytes()).decode('ascii')
                decoded=page.evaluate('''async src=>{const image=document.querySelector('#source');
                    image.src=src;await image.decode();return XhsLoginQR.render(image,document.querySelector('#out'));}''',
                    'data:image/png;base64,'+encoded)
                assert decoded
        finally:browser.close()
