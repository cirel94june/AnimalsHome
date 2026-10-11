const test=require('node:test'),assert=require('node:assert/strict'),C=require('../controls.js');
test('hold power rises, falls and repeats using elapsed time',()=>{assert.equal(C.powerAt(0),0);assert.equal(C.powerAt(1500),25);assert.equal(C.powerAt(3000),100);assert.equal(C.powerAt(4500),25);assert.equal(C.powerAt(6000),0);assert.equal(C.powerAt(7500),25);});
test('valid hold release shoots once; brief taps and zero power do not shoot',()=>{const c=C.createCharge();c.down(1,100,true);assert.equal(c.up(1,850,true,true),6);assert.equal(c.up(1,900,true,true),null);c.down(2,1000,true);assert.equal(c.up(2,1050,true,true),null);c.down(3,2000,true);assert.equal(c.up(3,8000,true,true),null);});
test('sliding off irreversibly cancels the current hold',()=>{const c=C.createCharge();c.down(1,0,true);c.move(1,false);c.move(1,true);assert.equal(c.up(1,900,true,true),null);c.down(2,1000,true);assert.equal(c.up(2,1750,true,true),6);});
test('multitouch cancels and blocks until every pointer has lifted',()=>{const c=C.createCharge();c.down(1,0,true);c.down(2,300,false);assert.equal(c.active(),false);c.up(2,500,true,true);c.down(3,600,true);assert.equal(c.active(),false);assert.equal(c.up(1,750,true,true),null);assert.equal(c.up(3,800,true,true),null);c.down(4,1000,true);assert.equal(c.up(4,1750,true,true),6);});
test('cancellation, hidden/reset and eligibility changes cannot emit a delayed shot',()=>{for(const clear of [false,true]){const c=C.createCharge();c.down(1,0,true);c.cancel(clear);assert.equal(c.up(1,750,true,true),null);c.down(2,1000,true);assert.equal(c.up(2,1750,true,false),null);}const c=C.createCharge();c.down(1,0,false);assert.equal(c.up(1,750,true,true),null);});
test('normalized coordinates and drag aiming preserve direction across viewport sizes',()=>{const view={w:1200,h:700,x:100,y:100,scale:1000/2.54},cue={x:.4,y:.5};for(const rect of [{left:20,top:40,width:600,height:350},{left:10,top:60,width:360,height:210}]){const x=1.4,y=.5,px=rect.left+(view.x+x*view.scale)/view.w*rect.width,py=rect.top+(view.y+y*view.scale)/view.h*rect.height;const p=C.tablePoint(px,py,rect,view);assert.ok(Math.abs(p.x-x)<1e-10);assert.ok(Math.abs(p.y-y)<1e-10);assert.ok(Math.abs(C.aimAngle(cue,p,.7))<1e-10);}assert.equal(C.aimAngle(cue,{...cue},.7),.7);});
test('portrait table touches map back to unchanged physics coordinates',()=>{
 const view={w:1200,h:700,x:100,y:100,scale:1000/2.54};
 const rect={left:13,top:110,width:350,height:600};
 for(const target of [{x:.3,y:.25},{x:1.5,y:.7},{x:2.3,y:1.1}]){
  const vx=view.x+target.x*view.scale,vy=view.y+target.y*view.scale;
  const screen={x:rect.left+(1-vy/view.h)*rect.width,y:rect.top+vx/view.w*rect.height};
  const point=C.tablePoint(screen.x,screen.y,rect,view,true);
  assert.ok(Math.abs(point.x-target.x)<1e-10);
  assert.ok(Math.abs(point.y-target.y)<1e-10);
 }
});
test('cue-side aiming points away from the finger and keeps a close-touch fallback',()=>{
 const cue={x:1,y:.5};
 assert.ok(Math.abs(C.aimAngle(cue,{x:.5,y:.5},.3,true))<1e-10);
 assert.ok(Math.abs(C.aimAngle(cue,{x:1,y:1},.3,true)+Math.PI/2)<1e-10);
 assert.equal(C.aimAngle(cue,{x:1.02,y:.5},.3,true),.3);
});

test('short holds have fine low-end control while full charge retains full break power',()=>{assert.equal(C.powerAt(100),0);assert.equal(C.powerAt(250),1);assert.equal(C.powerAt(500),3);assert.equal(C.powerAt(3000),100);const P=require('../physics.js');for(const ms of [250,500]){const world=P.createWorld([{id:0,x:1,y:.6,vx:0,vy:0,pocketed:false}]);P.strike(world,{angle:0,power:C.powerAt(ms)});assert.ok(Math.hypot(world.balls[0].vx,world.balls[0].vy)<.5);}});
