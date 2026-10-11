const test = require('node:test');
const assert = require('node:assert/strict');
const P = require('../physics.js');
const ball = (id,x,y,vx=0,vy=0) => ({id,x,y,vx,vy,pocketed:false});
const near = (a,b,t=0.03) => assert.ok(Math.abs(a-b)<t, `${a} != ${b}`);
test('head-on equal masses transfer velocity without creating momentum',()=>{
  const w=P.createWorld([ball(0,.6,.6,1),ball(1,.6+2*P.R+.001,.6)]);
  P.step(w); near(w.balls[0].vx,0); near(w.balls[1].vx,1); near(w.balls.reduce((n,b)=>n+b.vx,0),1);
});
test('oblique impact preserves two-axis momentum before friction loss',()=>{
  const w=P.createWorld([ball(0,.6,.6,1),ball(1,.6+1.7*P.R,.6+P.R)]);
  P.step(w); near(w.balls.reduce((n,b)=>n+b.vx,0),1); near(w.balls.reduce((n,b)=>n+b.vy,0),0); assert.ok(w.balls[1].vy>0);
});
test('cushion rebound and event are physical',()=>{
  const w=P.createWorld([ball(0,.8,P.R+.0001,0,-1)]); P.step(w);
  assert.ok(w.balls[0].vy>0); assert.ok(w.events.some(e=>e.type==='rail'));
});
test('cloth friction stops a ball at finite distance',()=>{
  const w=P.createWorld([ball(0,1,.6,.4,0)]); for(let i=0;i<600;i++)P.step(w);
  assert.equal(w.balls[0].vx,0); assert.ok(w.balls[0].x>1 && w.balls[0].x<1.5);
});
test('side-pocket throat captures a shot but a neighboring near miss rebounds',()=>{
  const hit=P.createWorld([ball(0,P.W/2,.12,0,-1)]); for(let i=0;i<80;i++)P.step(hit);
  assert.equal(hit.balls[0].pocketed,true); assert.equal(hit.events.find(e=>e.type==='pocket').pocketId,1);
  const miss=P.createWorld([ball(0,P.W/2+.14,.12,0,-1)]); for(let i=0;i<80;i++)P.step(miss);
  assert.equal(miss.balls[0].pocketed,false); assert.ok(miss.events.some(e=>e.type==='rail'));
});
test('maximum-power shot cannot tunnel through another ball',()=>{
  const result=P.simulateShot([ball(0,.3,.6),ball(1,.9,.6)],{angle:0,power:100});
  assert.ok(result.events.some(e=>e.type==='contact' && e.ids.includes(1))); assert.equal(result.settled,true);
});
test('seeded rack and simulator are reproducible and do not mutate input',()=>{
  const rack=P.createRack(17), copy=structuredClone(rack); const shot={angle:0,power:90};
  assert.deepEqual(P.createRack(17),rack); assert.deepEqual(P.simulateShot(rack,shot),P.simulateShot(rack,shot)); assert.deepEqual(rack,copy);
  assert.equal(rack.filter(b=>b.id!==0).length,15); const eight=rack.find(b=>b.id===8); near(eight.x,P.FOOT+2*Math.sqrt(3)*P.R,.001);
});
test('restricted-shot crossing is recorded before target contact',()=>{
  const w=P.createWorld([ball(0,.3,.6),ball(1,.9,.6)]);P.strike(w,{angle:0,power:45});for(let i=0;i<400;i++)P.step(w);
  const crossing=w.events.find(e=>e.type==='crossHead'),contact=w.events.find(e=>e.type==='contact');assert.ok(crossing);assert.ok(crossing.time<=contact.time);
});
test('a ball initially frozen to a rail does not count pushing into that same rail',()=>{
  const w=P.createWorld([ball(0,.8,P.R,0,-1)]);P.step(w);assert.equal(w.events.find(e=>e.type==='rail').counts,false);
});
