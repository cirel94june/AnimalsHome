const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('static/xhs-lite.html','utf8');
const script = html.slice(html.indexOf('let cloudFrameURL'),html.indexOf('let xhsRunning'));
const nodes = Object.fromEntries(['cloudStartBtn','cloudConfirmBtn','cloudLoginHint','cloudLoginFrame','cloudLoginQR','cloudFullFrame'].map(id=>[id,{hidden:true,disabled:false,removeAttribute(name){delete this[name];}}]));
const calls=[],revoked=[];let frameCalls=0,loaded=0,failConfirm=false;
const ctx={
  $:id=>nodes[id],Date,Error,clearTimeout(){},setTimeout(){return 1;},
  window:{addEventListener(){}},URL:{createObjectURL(){return 'blob:local-frame';},revokeObjectURL(url){revoked.push(url);}},
  api:async(method,url)=>{calls.push([method,url]);if(url.endsWith('/start'))return{ok:true,expires_in:300};if(url.endsWith('/confirm'))return failConfirm?{ok:false,error:'请在手机确认'}:{ok:true,account:{nickname:'reader'}};return{ok:true};},
  fetch:async(url,options)=>{frameCalls++;assert.equal(options.method,'POST');assert.equal(options.headers['X-Requested-With'],'AnimalsHome');return{ok:true,blob:async()=>({})};},
  loadConfig:async()=>{loaded++;},setStatus(){}
};
vm.createContext(ctx);vm.runInContext(script,ctx);
(async()=>{
  await ctx.cloudStart();
  assert.equal(frameCalls,1);assert.equal(nodes.cloudLoginFrame.src,'blob:local-frame');
  assert.equal(nodes.cloudLoginFrame.hidden,false);assert.equal(nodes.cloudConfirmBtn.hidden,false);
  assert.equal(nodes.cloudStartBtn.disabled,false);
  failConfirm=true;await ctx.cloudConfirm();
  assert.equal(loaded,0);assert.equal(nodes.cloudConfirmBtn.hidden,false);assert.match(nodes.cloudLoginHint.textContent,/手机确认/);
  failConfirm=false;await ctx.cloudConfirm();
  assert.equal(loaded,1);assert.equal(nodes.cloudLoginFrame.hidden,true);assert.equal(nodes.cloudLoginFrame.src,undefined);
  assert.equal(nodes.cloudConfirmBtn.hidden,true);assert.ok(revoked.includes('blob:local-frame'));
  assert.match(nodes.cloudLoginHint.textContent,/reader/);assert.equal(nodes.cloudConfirmBtn.disabled,false);
  assert.ok(calls.every(([method])=>method==='POST'));
  console.log('cloud login UI: protected frame, explicit confirmation, retry and cleanup OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
