const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'static/memory-compression.html'),'utf8');
function fixture(){
  const timers=new Map();let id=0,calls=0,response={jobs:{}},release;
  const c=vm.createContext({document:{getElementById:()=>({value:'main'})},jobs:{},jobPollTimer:null,jobPollBusy:false,jobPollAgain:false,
    visible:true,compressionPageVisible:()=>c.visible,
    api:async()=>{calls++;if(release)await new Promise(resolve=>{release=resolve;});return response;},
    renderJob:(level,job)=>{c.jobs[level]=job;},loadPreviews:async()=>{},loadHistory:async()=>{},status(){},
    setTimeout(fn,delay){timers.set(++id,{fn,delay});return id;},clearTimeout(id){timers.delete(id);}});
  vm.runInContext(source.slice(source.indexOf('    async function pollCompressionJobs('),source.indexOf('    async function runCompression(')),c);
  return {c,timers,get calls(){return calls;},reply(jobs){response={jobs};},hold(){release=true;},release(){release();release=null;}};
}
test('compression idle stops, active polls every five seconds, completion stops again',async()=>{
  const f=fixture();await f.c.pollCompressionJobs();assert.equal(f.timers.size,0);
  f.reply({daily:{id:'one',status:'running'}});await f.c.pollCompressionJobs();assert.equal([...f.timers.values()][0].delay,5000);
  f.reply({daily:{id:'one',status:'completed'}});await f.c.pollCompressionJobs(true);assert.equal(f.timers.size,0);
});
test('hidden compression page does not fetch; concurrent triggers coalesce',async()=>{
  const f=fixture();f.c.visible=false;await f.c.pollCompressionJobs();assert.equal(f.calls,0);
  f.c.visible=true;f.hold();const first=f.c.pollCompressionJobs();await f.c.pollCompressionJobs();assert.equal(f.calls,1);
  f.c.visible=false;f.release();await first;assert.equal(f.timers.size,0);
});
test('inline scripts parse',()=>{for(const match of source.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g))new vm.Script(match[1]);});

test('chatroom memory list still renders dates after legacy draft retirement',()=>{
  const chat=fs.readFileSync(require('node:path').join(__dirname,'static/chatroom.js'),'utf8');
  const nodes={memList:{innerHTML:''},memCountBadge:{textContent:''},memSearch:{value:''}};
  const c=vm.createContext({document:{getElementById:id=>nodes[id]},
    chatroomMemoryCache:[{id:'one',content:'Saved memory',memory_time_label:'2026-09-25',memory_kind:'daily'}],
    chatroomMemoryKindFilter:'all',chatroomMemoryKindMenuId:null,esc:String,
    restoreChatroomMemoryPosition(){}});
  for(const name of ['crFormatMemoryOccurrence','chatroomMemoryKind','renderChatroomMemories']){
    const start=chat.indexOf(`function ${name}(`);
    assert.ok(start>=0,`missing shared helper: ${name}`);
    vm.runInContext(chat.slice(start,chat.indexOf('\n}',start)+2),c);
  }
  c.renderChatroomMemories();
  assert.match(nodes.memList.innerHTML,/Saved memory/);
  assert.match(nodes.memList.innerHTML,/2026-09-25/);
});
test('job push wakes an idle visible page, hidden page closes connection',()=>{
  let polls=0,closed=0;const sockets=[];
  class Socket{constructor(){sockets.push(this);}close(){closed++;}}
  const c=vm.createContext({jobPollTimer:null,reconnectTimer:null,jobSocket:null,visible:true,
    compressionPageVisible:()=>c.visible,pollCompressionJobs:()=>{polls++;},clearTimeout(){},setTimeout(){},
    location:{protocol:'http:',host:'test'},WebSocket:Socket,document:{getElementById:()=>({value:'main'})}});
  vm.runInContext(source.slice(source.indexOf('    function syncCompressionConnection()'),source.indexOf('    const oldCompressionVisibility')),c);
  c.syncCompressionConnection();const before=polls;
  sockets[0].onmessage({data:JSON.stringify({type:'memory_compression_job',data:{target:'main'}})});assert.equal(polls,before+1);
  c.visible=false;c.syncCompressionConnection();assert.equal(closed,1);
  sockets[0].onmessage({data:JSON.stringify({type:'memory_compression_job',data:{target:'main'}})});assert.equal(polls,before+1);
});
