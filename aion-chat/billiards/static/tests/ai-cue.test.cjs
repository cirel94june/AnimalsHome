const test=require('node:test'),assert=require('node:assert/strict'),G=require('../game.js'),R=require('../rules.js');
const angleError=(a,b)=>Math.abs(Math.atan2(Math.sin(a-b),Math.cos(a-b)));
test('cue approaches the planned direction along shortest path across pi and settles exactly',()=>{const from=179*Math.PI/180,to=-179*Math.PI/180;for(const t of [0,500,1000,1299]){const p=G.cuePose(from,to,75,t,2000);assert.ok(angleError(p.angle,Math.PI)<.08);}const p=G.cuePose(from,to,75,1500,2000);assert.equal(p.phase,'pullback');assert.equal(p.angle,to);assert.equal(G.cuePose(0,Math.PI,75,1999,2000).angle,Math.PI);assert.equal(G.cuePose(0,Math.PI,75,2000,2000).phase,'ready');});
test('frozen shot drives visible cue and exact one-time physical strike',async()=>{const initial=R.newGame(17,1);initial.phase='aim';initial.placement=null;const action={type:'shot',shot:{angle:Math.PI,power:80,call:null}};const c=G.createController({initialState:initial,delay:()=>2000,scheduler:()=>{},planner:async()=>action});c.setVisible(true);await new Promise(r=>setImmediate(r));assert.equal(c.getState().aiCue.targetAngle,Math.PI);action.shot.angle=-1;action.shot.power=3;for(let i=0;i<39;i++)c.tick(50);let s=c.getState();assert.equal(s.aiCue.angle,Math.PI);assert.equal(s.phase,'aim');c.tick(50);s=c.getState();assert.equal(s.phase,'motion');assert.equal(s.lastSubmittedShot.angle,Math.PI);assert.equal(s.lastSubmittedShot.power,80);assert.equal(s.submittedShotCount,1);for(let i=0;i<20;i++)c.tick(50);assert.equal(c.getState().submittedShotCount,1);});
test('hide and restart cancel planned cue without firing stale shot',async()=>{const initial=R.newGame(17,1);initial.phase='aim';initial.placement=null;const c=G.createController({initialState:initial,delay:()=>2000,scheduler:()=>{},planner:async()=>({type:'shot',shot:{angle:1,power:70}})});c.setVisible(true);await new Promise(r=>setImmediate(r));c.tick(500);c.setVisible(false);assert.equal(c.getState().aiCue,null);for(let i=0;i<50;i++)c.tick(50);assert.equal(c.getState().submittedShotCount,0);c.reset(19,0);c.setVisible(true);for(let i=0;i<50;i++)c.tick(50);assert.equal(c.getState().phase,'placement');assert.equal(c.getState().submittedShotCount,0);});
test('turn banner triggers real player changes, not continuation, restart or terminal state',()=>{const s=R.newGame();s.lastShot={shooter:0};s.turn=1;assert.equal(G.shouldAnnounceTurn(0,s),true);assert.equal(G.shouldAnnounceTurn(1,s),false);assert.equal(G.shouldAnnounceTurn(0,R.newGame(2,1)),false);s.phase='over';assert.equal(G.shouldAnnounceTurn(0,s),false);});
test('saved-room cue follows server preview for either AI seat without firing locally',()=>{
 for(const actor of [0,1]){
  const initial=R.newGame(17,actor);initial.phase='aim';
  const c=G.createController({initialState:initial,autoPlay:false});
  const preview={id:'remote-'+actor,actor,action:{type:'shot',shot:{angle:1.2,power:80}},duration_ms:2500,elapsed_ms:0};
  c.preview(preview);const first=c.getState().aiCue.angle;
  for(let i=0;i<15;i++)c.tick(50);assert.notEqual(c.getState().aiCue.angle,first);
  const halfway=c.getState().aiCue.angle;c.preview({...preview,elapsed_ms:600});assert.equal(c.getState().aiCue.angle,halfway);
  for(let i=0;i<50;i++)c.tick(50);const s=c.getState();assert.equal(s.aiCue.angle,1.2);assert.equal(s.phase,'aim');assert.equal(s.submittedShotCount,0);
  c.restore(initial);c.submit(preview.action,actor);assert.equal(c.getState().aiLastAngle,c.getState().lastSubmittedShot.angle);assert.equal(c.getState().submittedShotCount,1);
 }
});
test('pausing a saved-room preview clears movement and cannot release a stale shot',()=>{
 const initial=R.newGame(17,1);initial.phase='aim';const c=G.createController({initialState:initial,autoPlay:false});
 c.preview({id:'cancel',actor:1,action:{type:'shot',shot:{angle:.7,power:60}},duration_ms:2500,elapsed_ms:500});c.setVisible(false);
 assert.equal(c.getState().aiCue,null);c.setVisible(true);for(let i=0;i<60;i++)c.tick(50);assert.equal(c.getState().submittedShotCount,0);
});
