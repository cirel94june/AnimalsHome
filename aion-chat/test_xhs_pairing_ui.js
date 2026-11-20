const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('static/xhs-lite.html','utf8');
const script=html.slice(html.indexOf('async function connectCloudBrowser()'),html.indexOf('let cloudFrameURL'));
const ids=['pairCloudButton','cloudConnectionUrl','cloudConnectionKey','cloudConnectionMachine','cloudConnectionFlyKey','cloudConnectionImage'];
const nodes=Object.fromEntries(ids.map(id=>[id,{value:'',hidden:false,disabled:false}]));
const bundle={url:'https://animalshome-xhs-test.fly.dev',token:'a'.repeat(64),machine_id:'123456789abcde',fly_token:'FlyV1 '+'b'.repeat(64),image_digest:'sha256:'+'c'.repeat(64)};
let lookups=0,posts=0,ready=false,loaded=0,status='';
const ctx={$:id=>nodes[id],URLSearchParams,AbortSignal,Error,
  location:{pathname:'/static/xhs-lite.html',search:'?maintenance=1&connect_port=23456&connect_nonce='+'n'.repeat(43)},
  history:{replaceState(_s,_t,path){assert.equal(path,'/static/xhs-lite.html');ctx.location.search='';}},
  setStatus:s=>{status=s;},loadConfig:async()=>{loaded++;},
  fetch:async(url,options)=>{
    assert.ok(!url.includes(bundle.fly_token) && !url.includes(bundle.token));
    if(url.startsWith('http://127.0.0.1:')){
      lookups++;assert.equal(url,'http://127.0.0.1:23456/connection?nonce='+'n'.repeat(43));
      return{ok:true,json:async()=>bundle};
    }
    posts++;assert.equal(url,'/api/xhs-lite/cloud-connection');
    assert.equal(options.method,'POST');assert.equal(options.headers['X-Requested-With'],'AnimalsHome');
    assert.deepEqual(JSON.parse(options.body),bundle);
    return{ok:ready,json:async()=>ready?{ok:true}:{ok:false,error:'still starting'}};
  }
};
vm.createContext(ctx);vm.runInContext(script,ctx);
(async()=>{
  await ctx.pairCloudBrowser();
  assert.equal(lookups,1);assert.equal(posts,1);assert.equal(loaded,0);
  assert.equal(ctx.location.search,'');assert.equal(nodes.cloudConnectionFlyKey.value,bundle.fly_token);
  ready=true;await ctx.pairCloudBrowser();
  assert.equal(lookups,1);assert.equal(posts,2);assert.equal(loaded,1);
  assert.equal(nodes.cloudConnectionFlyKey.value,'');assert.equal(nodes.cloudConnectionKey.value,'');
  assert.equal(nodes.pairCloudButton.hidden,true);assert.equal(nodes.pairCloudButton.disabled,false);
  assert.match(status,/扫码/);
  ctx.location.search='?connect_port=evil.invalid&connect_nonce='+'n'.repeat(43);
  assert.equal(ctx.pairingRequest(),null);
  console.log('pairing: one-use bootstrap, manual connect, safe retry and secret cleanup OK');
})().catch(e=>{console.error(e);process.exitCode=1;});
