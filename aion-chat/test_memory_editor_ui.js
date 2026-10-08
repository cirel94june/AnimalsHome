// Behavioral checks without launching a browser. Mobile/theme geometry is checked in the real page.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname,'static/memory-library.js'),'utf8');
function load(context, start, end) {
  vm.runInContext(source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start))),context);
}
test('page jump requests only the chosen page and rejects invalid input', () => {
  const input={value:'107',blur(){}}, list={scrollTop:300};
  const state={page:1,total:2140,limit:20};
  const requests=[], messages=[];
  const ctx=vm.createContext({state,el:id=>id==='pageInput'?input:list,
    loadList:()=>requests.push({page:state.page,limit:state.limit}),notify:text=>messages.push(text)});
  load(ctx,'  function jumpToPage()', '  async function loadList(');
  ctx.jumpToPage();
  assert.deepEqual(requests,[{page:107,limit:20}]);
  assert.equal(list.scrollTop,0);
  ctx.jumpToPage(); // Already on the requested page.
  for(const value of ['', '0', '108', '1.5', 'oops']) { input.value=value; ctx.jumpToPage(); }
  assert.equal(requests.length,1);
  assert.equal(messages.length,5);
});
test('changing stores ignores a late previous response', async () => {
  const pending = [], nodes = new Map();
  const state = {store:'main',view:'memories',filter:'all',page:1,limit:20};
  const ctx = vm.createContext({state,listRequest:0,searchTimer:null,innerWidth:390,URLSearchParams,
    el:id=>{ if(!nodes.has(id)) nodes.set(id,{value:'',innerHTML:'',setAttribute(){}}); return nodes.get(id); },
    base:()=>'/api/memory-library/'+state.store,request:(method,url)=>new Promise(resolve=>pending.push({url,resolve})),
    clearTimeout(){},renderControls(){},restoreSnapshot:()=>false,saveSnapshot(){},renderList(){},closeDetail(){},status(){}});
  load(ctx,'  async function loadList(', '  function closeDetail()');
  const old = ctx.loadList(); state.store='chatroom'; const next = ctx.loadList();
  pending[1].resolve({items:[{id:'companion'}],total:1,page:1}); await next;
  pending[0].resolve({items:[{id:'main'}],total:50,page:1}); await old;
  assert.equal(state.items[0].id,'companion');
  assert.equal(state.total,1);
  assert.ok(pending[1].url.startsWith('/api/memory-library/chatroom'));
});
test('a pending save cannot be submitted twice and a failure keeps the draft', async () => {
  const save={textContent:'保存'}, close={}, error={textContent:''}, draft={value:'还没保存的记忆'};
  const nodes={closeDialog:close,dialogError:error,dialog:{querySelectorAll:()=>[save,close]},editText:draft};
  const ctx=vm.createContext({dialogBusy:false,el:id=>nodes[id]});
  load(ctx,'  async function submitDialog(', '  const cancelSave');
  let reject, writes=0;
  const pending=ctx.submitDialog(save,()=>{writes++;return new Promise((_,r)=>reject=r);});
  await ctx.submitDialog(save,()=>{writes++;});
  assert.equal(writes,1); assert.equal(save.disabled,true);
  reject(new Error('连接中断')); await pending;
  assert.equal(error.textContent,'连接中断'); assert.equal(draft.value,'还没保存的记忆');
  assert.equal(ctx.dialogBusy,false); assert.equal(save.disabled,false);
});
test('cancelling a dirty editor respects the discard decision', () => {
  let closed=0, discard=false;
  const ctx=vm.createContext({dialogBusy:false,initialDraft:'before',dialogRequest:1,
    draftValues:()=> 'after',confirm:()=>discard,el:()=>({close(){closed++;}})});
  load(ctx,'  function dismissDialog()', '  async function submitDialog(');
  ctx.dismissDialog(); assert.equal(closed,0);
  discard=true; ctx.dismissDialog(); assert.equal(closed,1);
});
test('chatroom entry opens the shared manager and retains the current room', () => {
  const chat = fs.readFileSync(path.join(__dirname,'static/chatroom.js'),'utf8');
  let destination;
  const parent = {openSubPage:url=>destination=url};
  const ctx=vm.createContext({URLSearchParams,encodeURIComponent,currentRoom:{id:'selected-room'},
    location:{pathname:'/chatroom',search:''},window:{parent},closeSidebar(){}});
  vm.runInContext(chat.slice(chat.indexOf('function openMemory()'),chat.indexOf('function closeMemory()')),ctx);
  ctx.openMemory();
  const url=new URL(destination,'http://local.test');
  assert.equal(url.pathname,'/memory');
  assert.equal(url.searchParams.get('store'),'chatroom');
  assert.equal(url.searchParams.get('return'),'/chatroom?room=selected-room');
});
