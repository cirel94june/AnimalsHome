"""Isolated mobile picker + real private/group send handlers; no real messages."""
import functools
import base64
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import json
import os
from pathlib import Path
import re
import threading

import pytest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright=None


def function(source,name):
    return re.search(r'(?:async )?function '+name+r'\([^\n]*\)[\s\S]*?(?=\n(?:async )?function |\Z)',source)[0]


@pytest.mark.skipif(not sync_playwright or not os.environ.get('EDGE_EXECUTABLE'),reason='optional isolated browser tools')
@pytest.mark.parametrize('group',[False,True])
def test_mobile_picker_preview_send_and_old_card(group):
    root=Path(__file__).parent
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(root)))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    source=(root/'static'/('chatroom.js' if group else 'chat.js')).read_text(encoding='utf-8')
    name='crBuildMusicCardHtml' if group else 'buildMusicCardHtml'
    adapter='crOpenMusicShare' if group else 'openMusicShare'
    remove='removeChatroomAttachment' if group else 'removeAttachment'
    code='\n'.join(function(source,f) for f in [name,'renderPreview',remove,adapter])
    if group:
        code+='\n'+source.split("composer.addEventListener('submit', async (e) => {",1)[1].split('\nfunction crHandleReplyFailure',1)[0]
        code=code[:code.rfind("  e.preventDefault();")] + "composer.addEventListener('submit', async (e) => {\n" + code[code.rfind("  e.preventDefault();"):]
    else:
        code+='\n'+source[source.index('async function send()'):source.index('async function _processSSEStream(')]
        code+="\ncomposer.addEventListener('submit',e=>{e.preventDefault();send();});"
    setup=r'''
      var pendingAttachments=[],currentConvId='c',currentRoom={id:'r'},sending=false,isSending=false,streamingAiId=null,_abortController=null;
      var sent=[],released=[],whisperMode=false,ttsEnabled=false,ttsVoiceId='',_clientId='test',chatroomModel='m',chatroomConnorModel='',crTtsEnabled=false,crTtsAionVoice='',crTtsConnorVoice='',crWhisperMode=false,API='/api/chatrooms';
      const $=id=>document.getElementById(id),inputEl=$('input'),composer=$('composer');
      const esc= s=>String(s||'').replace(/</g,'&lt;'),escHtml=esc;
      const ChatImageUpload={release:a=>released.push(a)};
      function noop(){};
      var autoResize=noop,resizeInput=noop,_showStopBtn=noop,_showSendBtn=noop,playSend=noop,renderMessages=noop,crShowGenerationStop=noop,crShowGenerationSend=noop,endStreamingBubble=noop,crRefocusComposerAfterSend=noop,_updateSendBtnState=noop;
      var toast=s=>{window.lastToast=s},crRequireModels=()=>true,_getMaxTokens=()=>100;
      var upsertCurrentMessage=noop,appendMessage=()=>null;
      var _processSSEStream=async()=>{},consumeChatroomSSE=async()=>{};
      var _chatControl={active:false,begin:target=>({target,controller:new AbortController()}),fetch:async(g,u,o)=>{sent.push(JSON.parse(o.body));return{}},isCurrent:()=>true,finish:noop};
      var _crControl=_chatControl;
    '''
    errors=[];requests=[];cover_attempts={}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ['EDGE_EXECUTABLE'],headless=True)
            page=browser.new_page(viewport={'width':390,'height':844})
            page.set_default_timeout(8000)
            page.on('pageerror',lambda e:errors.append(str(e)))
            def mock(route):
                requests.append(route.request.url)
                if '/cover/' in route.request.url:
                    key=route.request.url.split('/cover/',1)[1].split('?')[0]
                    cover_attempts[key]=cover_attempts.get(key,0)+1
                    if key=='456' or (key=='789' and cover_attempts[key]>1):
                        route.fulfill(content_type='image/png',body=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+X2xkAAAAASUVORK5CYII='));return
                    route.fulfill(status=404,body='');return
                song={'id':123,'type':'music','name':'测试歌曲','artist':'测试歌手','album':'测试专辑'}
                data={'songs':[song]} if 'search?' in route.request.url else {'song':song}
                route.fulfill(status=200,content_type='application/json',body=json.dumps(data))
            page.route('**/api/**',mock)
            page.route('https://p1.music.126.net/**',lambda route:route.fulfill(status=403,body=''))
            page.route('**/fixture',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body='<html><meta charset="utf-8"><body></body></html>'))
            page.goto(base+'/fixture')
            page.set_content(f'''<html data-skin="ib"><head><meta charset="utf-8"><link rel="stylesheet" href="{base}/static/common.css"><link rel="stylesheet" href="{base}/static/{'chatroom' if group else 'chat'}.css"><link rel="stylesheet" href="{base}/static/ib/ib-skin.css"><link rel="stylesheet" href="{base}/static/music-share.css"></head><body><form id="composer"><textarea id="input"></textarea><button id="sendBtn">发送</button></form><div id="previewArea"></div><div id="oldCard"></div><input id="contextSlider" value="30"><input id="tempSlider" value=".7"><script src="{base}/static/music-share.js"></script></body></html>''')
            page.wait_for_function('window.MusicShare')
            page.evaluate(setup+'\n'+code)
            page.evaluate(adapter+'()')
            style=page.locator('.music-share-dialog').evaluate('(e)=>({bg:getComputedStyle(e).backgroundColor,color:getComputedStyle(e).color})')
            assert style=={'bg':'rgb(245, 247, 252)','color':'rgb(36, 52, 80)'}
            page.get_by_role('searchbox').fill('测试')
            assert page.get_by_role('button',name='搜索',exact=True).count(), page.locator('dialog').inner_html()
            page.get_by_role('button',name='搜索',exact=True).click()
            page.get_by_role('button',name='选这首').click()
            page.locator('.music-share-preview').wait_for()
            assert page.evaluate('sent.length')==0
            assert page.evaluate('pendingAttachments[0].id')==123
            page.evaluate("pendingAttachments.push({url:'/uploads/test-image.jpg',type:'image/jpeg'});renderPreview()")
            page.locator('#input').fill('一起听这首')
            page.locator('#sendBtn').click()
            page.wait_for_function('sent.length===1')
            payload=page.evaluate('sent[0]')
            assert payload['content']=='一起听这首\n🎵 分享音乐：《测试歌曲》 — 测试歌手'
            assert payload['attachments'][0]['id']==123 and payload['tts_enabled'] is False
            assert payload['attachments'][1]=='/uploads/test-image.jpg'
            assert page.evaluate('released.length')==1
            page.evaluate(f"document.querySelector('#oldCard').innerHTML={name}({{id:123,name:'旧记录',artist:'歌手'}})")
            image=page.locator('#oldCard img[data-music-cover]')
            assert image.get_attribute('src')=='/api/music/cover/123'
            page.wait_for_function('document.querySelector("#oldCard img").hidden')
            page.evaluate(f"document.querySelector('#oldCard').innerHTML={name}({{id:456,cover:'http://p1.music.126.net/art.jpg',name:'旧记录'}})")
            page.wait_for_function('document.querySelector("#oldCard img").naturalWidth===1')
            assert page.locator('#oldCard img').get_attribute('src')=='/api/music/cover/456'
            page.evaluate(f"document.querySelector('#oldCard').innerHTML={name}({{id:789,name:'暂时繁忙'}})")
            page.wait_for_function('document.querySelector("#oldCard img").naturalWidth===1')
            assert cover_attempts['789']==2
            # Switching chat during selection never adds a stale pending song.
            page.evaluate(adapter+'()')
            page.get_by_role('searchbox').fill('测试')
            page.get_by_role('button',name='搜索',exact=True).click()
            page.get_by_role('button',name='选这首').wait_for()
            page.evaluate("currentRoom={id:'another'};currentConvId='another'")
            page.get_by_role('button',name='选这首').click()
            page.get_by_role('status').filter(has_text='聊天已切换').wait_for()
            assert page.evaluate('pendingAttachments.length')==0
            assert page.evaluate('sent.length')==1
            page.screenshot(path=os.path.join(os.environ.get('TEMP','.'),f'animalshome-music-share-{group}.png'))
            assert not errors,errors
            browser.close()
    finally:
        server.shutdown();server.server_close();thread.join()
