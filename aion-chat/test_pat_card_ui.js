const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync('static/pat.js','utf8');
let handlers={},timers=[],appended=[];
const mk=()=>({style:{},children:[],appendChild(n){this.children.push(n);return n},addEventListener(){},set textContent(v){this._t=v},get textContent(){return this._t},showModal(){this.open=true},close(){},remove(){}});
const ctx={window:{},self:{},document:{createElement:()=>mk(),querySelector:()=>null,body:{appendChild:n=>appended.push(n)},activeElement:null},
 setTimeout:(f,ms)=>{timers.push(f);return timers.length},clearTimeout:id=>{timers[id-1]=null},location:{},console,
 fetch:async()=>({json:async()=>({actors:[{id:'ai3',name:'狗蛋',sign:'汪',avatar:'/a.png',meet_since:'2026-01-01'}]})})};
ctx.window=ctx; ctx.self=ctx; vm.createContext(ctx); vm.runInContext(src,ctx);
const Pat=ctx.Pat||ctx.AionPat||Object.values(ctx).find(v=>v&&typeof v.bind==='function'&&v.isPat);
const container={addEventListener:(t,f)=>{handlers[t]=f}};
Pat.bind({container,getContext:()=>({}),onSent(){}});
const avatar={dataset:{patTarget:'ai3'}}, user={dataset:{patTarget:'user'}};
const ev=(a,extra)=>Object.assign({target:{closest:()=>a},detail:1,preventDefault(){},isPrimary:true,clientX:1,clientY:1},extra);
(async()=>{
 handlers.click(ev(user)); assert.equal(timers.filter(Boolean).length,0,'user avatar no card');
 handlers.click(ev(avatar)); assert.equal(timers.filter(Boolean).length,1);
 handlers.dblclick(ev(avatar)); assert.equal(timers.filter(Boolean).length,0,'double click cancels card');
 handlers.click(ev(avatar)); timers.filter(Boolean)[0](); await new Promise(r=>setImmediate(r)); await new Promise(r=>setImmediate(r));
 assert.equal(appended.length,1); const texts=JSON.stringify(appended[0].children.map(c=>c._t));
 assert.ok(texts.includes('狗蛋')&&texts.includes('汪')&&texts.includes('在一起'),texts);
 console.log('pat card ok');
})().catch(e=>{console.error(e);process.exit(1)});
