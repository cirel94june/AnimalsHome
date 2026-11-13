'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_PATH || 'playwright');
const read=file=>fs.readFileSync(path.join(__dirname,file),'utf8');
const slice=(text,start,end)=>text.slice(text.indexOf(start),text.indexOf(end,text.indexOf(start)));
const launch=()=>chromium.launch({headless:true,...(process.env.BROWSER_PATH?{executablePath:process.env.BROWSER_PATH}:{})});

test('both real chat renderers expose a safe card; click collects and opens the sandbox viewer',async()=>{
 const browser=await launch();
 try{
  const page=await browser.newPage({viewport:{width:375,height:812}}),requests=[],leaks=[];
  const code='<!doctype html><html><head><title>给你的一朵花</title><style>body{background:#eef4fc;color:#284263;text-align:center;padding:32px;font-family:serif}button{padding:14px;border-radius:20px;background:#c8d9f2;border:0}</style></head><body><h1>给你的一朵花</h1><p id="flower">✿</p><button onclick="document.getElementById(\'flower\').textContent=\'❀\'">让花开</button><script>try{parent.document.body.dataset.leaked="yes"}catch(e){};fetch("http://leak.test/secret").catch(()=>{});</script></body></html>';
  const work={id:'w_1234567890abcdef',html:code,title:'给你的一朵花',name:'TEST_AI',note:'送给你',created_at:1};
  const privateRenderer=slice(read('static/chat.js'),'function renderPrivateMessageHTML(m) {','function updatePrivateStreamingMessage(');
  const roomRenderer=slice(read('static/chatroom.js'),'function msgHTML(m, nextMessage = null) {','let crMsgFeedbackPopover');
  const stubs=`const worldBook={user_name:'TEST_USER',ai_name:'TEST_AI'},currentConvId='c';const fmtTime=()=>'',withWishFallbackAttachments=m=>m.attachments||[],renderBandVibrationNote=()=>'',stripWishFulfillmentMarker=x=>x,renderAttachments=()=>'';const escHtml=x=>String(x).replaceAll('<','&lt;');const privateMessageContentItemsWithInlinePats=x=>[{type:'message',text:x}],privateMarkdownMessageHtml=escHtml;const crName=x=>x,AVATARS={},crStripWishFulfillmentMarker=x=>x,crWithWishFallbackAttachments=m=>m.attachments||[],renderToyAttachments=()=>'',crBandVibrationNoteHtml=()=>'',crIsSeat=()=>true,escWithTransfer=escHtml,escWithImages=escHtml,crMessageContentItemsWithInlinePats=x=>[{text:x}],crRenderMessageItems=x=>x.map(i=>escHtml(i.text)).join('');`;
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url());
   if(url.hostname==='leak.test'){leaks.push(url.href);return route.abort();}
   if(url.origin!=='http://art.test')return route.abort();
   if(url.pathname==='/chat')return route.fulfill({contentType:'text/html; charset=utf-8',body:`<meta charset="utf-8"><meta name="viewport" content="width=device-width"><link rel="stylesheet" href="/static/chat-artifacts.css"><div id="messages"></div><script src="/static/chat-artifacts.js"></script><script>${stubs}${privateRenderer}${roomRenderer}</script>`});
   if(url.pathname==='/api/chat-artifacts/collect'){requests.push(route.request().postDataJSON());return route.fulfill({json:{work_id:work.id}});}
   if(url.pathname==='/works')return route.fulfill({contentType:'text/html; charset=utf-8',body:read('static/works.html')});
   if(url.pathname==='/api/works/'+work.id)return route.fulfill({json:work});
   if(url.pathname==='/api/works')return route.fulfill({json:{works:[work]}});
   if(url.pathname.startsWith('/static/'))return route.fulfill({path:path.join(__dirname,url.pathname)});
   return route.fulfill({status:404,body:''});
  });
  for(const kind of ['conv','room']){
   await page.goto('http://art.test/chat');
   await page.evaluate(({kind,code})=>{
    const m={id:'test_message',content:'送给你\n```html\n'+code+'\n```',role:'assistant',sender:'ai3',attachments:[]};
    document.getElementById('messages').innerHTML=kind==='conv'?renderPrivateMessageHTML(m):msgHTML(m);
   },{kind,code});
   assert.equal(await page.locator('[data-chat-work-message]').count(),1);
   assert.equal(await page.locator('#flower').count(),0,'untrusted HTML must never enter chat DOM');
   await page.getByRole('button',{name:'打开并收藏'}).click();
   await page.waitForURL('**/works?work='+work.id);
   await page.waitForFunction(()=>document.getElementById('frame').srcdoc.includes('给你的一朵花'));
   const frame=page.frameLocator('#frame');
   await frame.getByRole('button',{name:'让花开'}).click();
   assert.equal(await frame.locator('#flower').textContent(),'❀');
   assert.equal(await page.locator('body').getAttribute('data-leaked'),null);
   assert.equal(await page.locator('#frame').getAttribute('sandbox'),'allow-scripts');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  }
  assert.deepEqual(requests,[{kind:'conv',message_id:'test_message'},{kind:'room',message_id:'test_message'}]);
  assert.deepEqual(leaks,[],'work cannot contact an external origin');
  if(process.env.SCREENSHOT_PATH)await page.screenshot({path:process.env.SCREENSHOT_PATH,fullPage:true});
 }finally{await browser.close();}
});

test('settings can save code with no keys and retain independent Banana/OpenAI configurations',async()=>{
 const browser=await launch();
 try{
  const page=await browser.newPage({viewport:{width:375,height:812}});
  const source=read('static/settings.html');
  const markup=source.replace(/<script\b[\s\S]*?<\/script>/gi,'');
  const functions=slice(source,'var imageProfiles = {}, imageDisplayedProfile = null;','function updateSentinelRouteUi()')+slice(source,'async function saveSettings() {','function enablePhoneScreenSupervision()');
  const stubs=`const $=id=>document.getElementById(id);window.sent=[];let customRoutesRev=0,customModelRoutes=[];const showToast=x=>window.toast=x,collectCustomRoutes=()=>[],currentRouteCollapsedState=()=>({}),elevenSettings=()=>({}),collectModelTransportModes=()=>({}),normalizeRoutesForUi=x=>x,renderCustomRoutes=()=>{};async function api(method,url,body){window.sent.push(body);return{}};`;
  await page.route('**/*',route=>route.request().url()==='http://art.test/settings'?route.fulfill({contentType:'text/html',body:markup.replace('</body>',`<script>${stubs}${functions}</script></body>`)}):route.abort());
  await page.goto('http://art.test/settings');
  await page.evaluate(()=>{
   for(const id of ['asrBaseUrlInput','asrApiKeyInput','asrModelInput'])$(id).value='';
   $('imageGenProviderInput').value='code';updateImageGenUi();
  });
  await page.evaluate(()=>saveSettings());
  assert.equal(await page.evaluate(()=>sent.length),1,'code mode must save without image credentials');
  assert.equal(await page.evaluate(()=>sent[0].image_gen_provider),'code');
  await page.locator('details').filter({has:page.locator('#imageGenProviderInput')}).locator('summary').click();
  await page.selectOption('#imageGenProviderInput','gemini');
  await page.fill('#imageGenApiKeyInput','BANANA_TEST');
  await page.selectOption('#imageGenProviderInput','openai');
  await page.fill('#imageGenBaseUrlInput','https://images.invalid/v1');
  await page.fill('#imageGenApiKeyInput','OPENAI_TEST');
  await page.fill('#imageGenModelInput','gpt-image-test');
  await page.selectOption('#imageGenProviderInput','gemini');
  assert.equal(await page.inputValue('#imageGenApiKeyInput'),'BANANA_TEST');
  await page.evaluate(()=>saveSettings());
  const payload=await page.evaluate(()=>sent.at(-1));
  assert.equal(payload.image_gen_provider,'gemini');
  assert.equal(payload.image_gen_profiles.gemini.api_key,'BANANA_TEST');
  assert.equal(payload.image_gen_profiles.openai.api_key,'OPENAI_TEST');
  assert.equal(payload.image_gen_base_url,'');
  assert.equal(await page.locator('#geminiKeyInput').getAttribute('type'),'password');
 }finally{await browser.close();}
});

test('Love Marks displays webpages beside pictures without executing webpage source',async()=>{
 const browser=await launch();
 try{
  const page=await browser.newPage({viewport:{width:375,height:812}});
  const workId='w_1234567890abcdef';
  const gifts=[{id:'chat_test',image_path:'album/test.png',message:'测试画',created_at:10,sender:'ai3'},
    {id:'web_'+workId,kind:'webpage',work_id:workId,title:'测试小网页',message:'给你',created_at:11,sender:'ai3'}];
  const html=read('static/gift.html').replace(/<script src="[^"]+"><\/script>/g,'').replace('<script>', '<script>function connectCommonWS(){};function escHtml(x){return String(x).replaceAll("<","&lt;")};function showToast(){};');
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname==='/gift')return route.fulfill({contentType:'text/html; charset=utf-8',body:html});
   if(url.pathname==='/api/gift/list')return route.fulfill({json:{ok:true,gifts}});
   if(url.pathname.startsWith('/api/gift/thumbnail/web_'))throw new Error('webpage must not load an image thumbnail');
   if(url.pathname==='/api/gift/thumbnail/chat_test')return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200"><rect width="200" height="200" fill="#c8d9f2"/></svg>'});
   if(url.pathname==='/works')return route.fulfill({contentType:'text/html; charset=utf-8',body:'<h1>安全的小网页预览</h1>'});
   return route.abort();
  });
  await page.goto('http://art.test/gift');
  await page.waitForFunction(()=>document.querySelectorAll('.gift-thumb').length===2);
  assert.equal(await page.locator('.web-gift-cover').count(),1);
  assert.equal(await page.locator('.gift-thumb-img').count(),1);
  await page.getByText('测试小网页',{exact:true}).click();
  await page.waitForURL('**/works?work='+workId);
 }finally{await browser.close();}
});
