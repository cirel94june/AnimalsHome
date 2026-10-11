(function(root,factory){const api=factory(root.Billiards?.Physics||(typeof require==='function'?require('./physics.js'):null));if(typeof module==='object')module.exports=api;else(root.Billiards??={}).Renderer=api;})(globalThis,function(P){
  'use strict';const VIEW={w:1200,h:700,x:100,y:100,scale:1000/P.W},PORTRAIT_CROP={w:1112,h:616};
  const colors=['#f0eddf','#ddb22c','#235ca9','#bd3f3e','#815393','#e78638','#2b8669','#903d4b','#171c1c'];
  function color(id){return colors[id>8?id-8:id]||colors[0];}
  function point(b){return{x:VIEW.x+b.x*VIEW.scale,y:VIEW.y+b.y*VIEW.scale};}
  function screenToTable(px,py){return{x:(px-VIEW.x)/VIEW.scale,y:(py-VIEW.y)/VIEW.scale};}
  const rr=(c,x,y,w,h,r)=>{c.beginPath();c.roundRect(x,y,w,h,r);};
  function label(c,text,x,y,portrait){c.save();c.translate(x,y);if(portrait)c.rotate(-Math.PI/2);c.fillText(text,0,0);c.restore();}
  let grain=null;const backgrounds=new WeakMap();
  function tableBackground(c,state,aim){
    c.save();const sx=c.canvas.width/VIEW.w,sy=c.canvas.height/VIEW.h;c.setTransform(sx,0,0,sy,0,0);c.clearRect(0,0,VIEW.w,VIEW.h);
    if(!aim.portrait){const room=c.createRadialGradient(600,250,70,600,340,720);room.addColorStop(0,'#344531');room.addColorStop(.6,'#1b271d');room.addColorStop(1,'#152019');c.fillStyle=room;c.fillRect(0,0,1200,700);}
    c.shadowColor='#000a';c.shadowBlur=35;c.shadowOffsetY=17;rr(c,44,42,1112,616,36);c.fillStyle='#1a120e';c.fill();c.shadowBlur=0;c.shadowOffsetY=0;
    const wood=c.createLinearGradient(0,40,0,655);wood.addColorStop(0,'#69513a');wood.addColorStop(.16,'#4d3728');wood.addColorStop(.48,'#36251c');wood.addColorStop(.85,'#573e2a');wood.addColorStop(1,'#795d3b');rr(c,46,42,1108,613,33);c.fillStyle=aim.portrait?'#304337':wood;c.fill();c.strokeStyle=aim.portrait?'#8f9e7055':'#a5814c';c.lineWidth=aim.portrait?1.5:2;c.stroke();
    c.save();rr(c,46,42,1108,613,33);c.clip();const rng=P.random(117);
    if(!aim.portrait)for(let i=0;i<190;i++){let y=43+rng()*610;c.strokeStyle=i%3?'#e1bb7410':'#0b080930';c.lineWidth=.5+rng()*2;c.beginPath();c.moveTo(43,y);c.bezierCurveTo(400,y+4+rng()*9,800,y-10+rng()*15,1160,y+3);c.stroke();}c.restore();
    rr(c,79,78,1042,544,19);c.fillStyle='#13291f';c.fill();c.strokeStyle='#121b11';c.lineWidth=5;c.stroke();
    c.save();rr(c,100,100,1000,500,4);c.clip();const felt=c.createRadialGradient(590,230,10,590,360,620);felt.addColorStop(0,'#268461');felt.addColorStop(.58,'#1e6d50');felt.addColorStop(1,'#164735');c.fillStyle=felt;c.fillRect(100,100,1000,500);
    if(!grain){const off=document.createElement('canvas');off.width=96;off.height=96;const gc=off.getContext('2d'),rd=P.random(71);for(let i=0;i<1100;i++){gc.fillStyle=rd()>.5?'#e2f9cc0c':'#0a220e14';gc.fillRect(rd()*96,rd()*96,1,1);}grain=c.createPattern(off,'repeat');}c.fillStyle=grain;c.fillRect(100,100,1000,500);
    const placement=state.phase==='placement';if(placement){c.fillStyle='#d3dfa113';c.fillRect(100,100,state.placement==='head'?P.HEAD*VIEW.scale:1000,500);}
    c.setLineDash([5,9]);c.strokeStyle=placement?'#e4e6c787':'#d2ddbe22';c.lineWidth=1;c.beginPath();c.moveTo(100+P.HEAD*VIEW.scale,100);c.lineTo(100+P.HEAD*VIEW.scale,600);c.stroke();c.setLineDash([]);
    for(const x of [P.HEAD,P.FOOT]){const s=point({x,y:P.H/2});c.fillStyle='#ced8b165';c.beginPath();c.arc(s.x,s.y,3,0,Math.PI*2);c.fill();}
    c.restore();
    for(let i=0;i<P.rails.length;i++){const left=P.bevels[i*2],right=P.bevels[i*2+1],a=point(left.a),b=point(right.a),outsideB=point(right.outer),outsideA=point(left.outer),tipB=point(right.b),tipA=point(left.b);
      c.beginPath();c.moveTo(a.x,a.y);c.lineTo(b.x,b.y);c.lineTo(outsideB.x,outsideB.y);c.lineTo(outsideA.x,outsideA.y);c.closePath();c.fillStyle='#153d2b';c.fill();
      c.strokeStyle='#79af7150';c.lineWidth=1.5;c.beginPath();c.moveTo(tipA.x,tipA.y);c.lineTo(a.x,a.y);c.lineTo(b.x,b.y);c.lineTo(tipB.x,tipB.y);c.stroke();}
    c.fillStyle='#e2cca58c';for(let n=1;n<8;n++){if(n===4)continue;for(const y of [65,635])diamond(c,100+n*125,y);}for(let n=1;n<4;n++)for(const x of [65,1135])diamond(c,x,100+n*125);
    for(let i=0;i<6;i++){const well=P.wells[i],p=point(well),r=well.radius*VIEW.scale;c.save();c.translate(p.x,p.y);c.fillStyle='#100f0c';c.shadowColor='#000a';c.shadowBlur=8;c.beginPath();c.arc(0,0,r,0,Math.PI*2);c.fill();c.shadowBlur=0;c.strokeStyle='#a6804844';c.lineWidth=4;c.stroke();c.fillStyle='#040c08';c.beginPath();c.arc(0,0,well.innerRadius*VIEW.scale,0,Math.PI*2);c.fill();c.fillStyle='#b8c8b175';c.font='bold 12px Segoe UI';c.textAlign='center';c.textBaseline='middle';label(c,i+1,0,1,aim.portrait);if(state.call?.pocketId===i&&state.phase==='aim'){c.strokeStyle='#f3dba2';c.lineWidth=3;c.beginPath();c.arc(0,0,r+3,0,Math.PI*2);c.stroke();}c.restore();}
    c.restore();
  }
  function draw(c,state,aim={},alpha=0){
    // Cache wood, felt and pockets; only balls and aiming change during a shot.
    const key=[c.canvas.width,c.canvas.height,!!aim.portrait,state.phase==='placement'?state.placement:'',state.phase==='aim'?state.call?.pocketId:''].join(':');
    let cached=backgrounds.get(c.canvas);
    if(!cached||cached.key!==key){const canvas=document.createElement('canvas');canvas.width=c.canvas.width;canvas.height=c.canvas.height;tableBackground(canvas.getContext('2d'),state,aim);cached={key,canvas};backgrounds.set(c.canvas,cached);}
    c.save();c.setTransform(1,0,0,1,0,0);c.clearRect(0,0,c.canvas.width,c.canvas.height);c.drawImage(cached.canvas,0,0);c.setTransform(c.canvas.width/VIEW.w,0,0,c.canvas.height/VIEW.h,0,0);
    const placement=state.phase==='placement',cue=state.balls.find(b=>b.id===0&&!b.pocketed),targets=state.targets||[];
    if(cue&&state.phase==='aim')drawAim(c,cue,state,aim);
    const ghosts=(state.pocketDrops||[]).map(d=>{const q=Math.min(1,d.elapsedMs/d.durationMs),p=P.wells[d.pocketId];return{id:d.ballId,x:d.x+(p.x-d.x)*q,y:d.y+(p.y-d.y)*q,pocketed:false,visualScale:Math.max(.05,(1-q)**.7),visualAlpha:1-q,roll:q*5};});
    for(const b of [...state.balls,...ghosts]){if(b.pocketed)continue;const p=point(b),r=P.R*VIEW.scale*(b.visualScale??1);c.save();c.globalAlpha=b.visualAlpha??1;if(b.roll){c.translate(p.x,p.y);c.rotate(b.roll);c.translate(-p.x,-p.y);}
      c.fillStyle='#05190b66';c.beginPath();c.ellipse(p.x+3,p.y+6,r*1.08,r*.77,0,0,Math.PI*2);c.fill();
      if((state.phase==='aim'||state.phase==='placement')&&targets.includes(b.id)){c.strokeStyle=state.call?.ballId===b.id?'#fae2a8':'#e2ebd475';c.lineWidth=state.call?.ballId===b.id?2.5:1;c.beginPath();c.arc(p.x,p.y,r+4.5,0,Math.PI*2);c.stroke();}
      const g=c.createRadialGradient(p.x-r*.36,p.y-r*.42,r*.06,p.x+r*.17,p.y+r*.2,r*1.16);g.addColorStop(0,b.id===0?'#fffff0':color(b.id));g.addColorStop(.7,b.id===0?'#d4dacd':color(b.id));g.addColorStop(1,'#142217');c.fillStyle=g;c.beginPath();c.arc(p.x,p.y,r,0,Math.PI*2);c.fill();
      if(b.id>8){c.save();c.beginPath();c.arc(p.x,p.y,r,0,Math.PI*2);c.clip();c.fillStyle='#eeeadd';c.fillRect(p.x-r,p.y-r,r*2,r*.47);c.fillRect(p.x-r,p.y+r*.53,r*2,r*.5);c.restore();}
      if(b.id){c.fillStyle='#f7f1df';c.beginPath();c.arc(p.x,p.y,r*.45,0,Math.PI*2);c.fill();c.fillStyle='#16201a';c.textAlign='center';c.textBaseline='middle';c.font='bold '+Math.max(1,9*(b.visualScale??1))+'px Segoe UI';label(c,b.id,p.x,p.y+.5,aim.portrait);}
      const shine=c.createRadialGradient(p.x-r*.4,p.y-r*.45,0,p.x-r*.4,p.y-r*.45,r*.75);shine.addColorStop(0,'#ffffffc0');shine.addColorStop(.35,'#ffffff40');shine.addColorStop(1,'#ffffff00');c.fillStyle=shine;c.beginPath();c.arc(p.x,p.y,r,0,Math.PI*2);c.fill();
      if(b.id===0&&placement){c.strokeStyle='#ecd59c';c.lineWidth=2;c.setLineDash([3,3]);c.beginPath();c.arc(p.x,p.y,r+8,0,Math.PI*2);c.stroke();c.setLineDash([]);}c.restore();}
    if(state.phase==='placement'&&!aim.portrait){c.font='14px Microsoft YaHei UI';c.fillStyle='#e2e7cba8';c.textAlign='center';c.fillText(state.placement==='head'?'开球区 · 放置白球':'自由球 · 选择白球位置',state.placement==='head'?100+P.HEAD*VIEW.scale/2:600,560);}
    c.restore();
  }
  function diamond(c,x,y){c.beginPath();c.moveTo(x,y-3);c.lineTo(x+2,y);c.lineTo(x,y+3);c.lineTo(x-2,y);c.closePath();c.fill();}
  function drawAim(c,cue,state,aim){const angle=Number.isFinite(aim.aim)?aim.aim:0,dx=Math.cos(angle),dy=Math.sin(angle),p=point(cue),r=P.R*VIEW.scale;
    c.save();c.translate(p.x,p.y);c.rotate(angle);const gap=24+(aim.cuePullback??(aim.power||50)*.16);
    c.fillStyle='#d3b885';c.beginPath();c.moveTo(-gap, -2.2);c.lineTo(-gap-220,-5);c.lineTo(-gap-220,5);c.lineTo(-gap,2.2);c.closePath();c.fill();c.fillStyle='#2b2220';c.fillRect(-gap-270,-6,51,12);c.fillStyle='#83bed0';c.fillRect(-gap-3,-2.3,3,4.6);
    c.restore();
    if(aim.charging){
      // Keep the cue-side meter visible even when the cue extends past the rail.
      const along=-gap-110,x=Math.max(100,Math.min(VIEW.w-100,p.x+dx*along-dy*19)),y=Math.max(100,Math.min(VIEW.h-100,p.y+dy*along+dx*19));
      c.save();c.translate(x,y);c.rotate(angle);rr(c,-55,-6,110,12,6);c.fillStyle='#0c1c16c9';c.fill();rr(c,-55,-6,Math.max(2,110*(aim.power||0)/100),12,6);c.fillStyle='#e3cb8e';c.fill();c.restore();
      c.save();c.fillStyle='#fae7b6';c.shadowColor='#09130e';c.shadowBlur=3;c.font='bold 18px Segoe UI';c.textAlign='center';c.textBaseline='middle';label(c,(aim.power||0)+'%',x-dy*25,y+dx*25,aim.portrait);c.restore();
    }
    if(aim.guide==='off')return;
    let distance=1.4,hit=null;
    for(const b of state.balls){if(b.id===0||b.pocketed)continue;const vx=b.x-cue.x,vy=b.y-cue.y,proj=vx*dx+vy*dy,perp=vx*dy-vy*dx;if(proj<=0||Math.abs(perp)>2*P.R)continue;const t=proj-Math.sqrt(4*P.R*P.R-perp*perp);if(t>=0&&t<distance){distance=t;hit=b;}}
    for(const t of [(P.R-cue.x)/dx,(P.W-P.R-cue.x)/dx,(P.R-cue.y)/dy,(P.H-P.R-cue.y)/dy])if(t>0&&t<distance){distance=t;hit=null;}
    const end=point({x:cue.x+dx*distance,y:cue.y+dy*distance});c.strokeStyle='#f4edc799';c.lineWidth=1.5;c.setLineDash([7,8]);c.beginPath();c.moveTo(p.x+dx*(r+7),p.y+dy*(r+7));c.lineTo(end.x,end.y);c.stroke();c.setLineDash([]);
    if(aim.guide==='ghost'&&hit){c.strokeStyle='#f2e9bfbb';c.lineWidth=1.5;c.beginPath();c.arc(end.x,end.y,r,0,Math.PI*2);c.stroke();const h=point(hit),nx=h.x-end.x,ny=h.y-end.y,len=Math.hypot(nx,ny);c.strokeStyle='#f2e9bf66';c.beginPath();c.moveTo(h.x,h.y);c.lineTo(h.x+nx/len*75,h.y+ny/len*75);c.stroke();}
  }
  function avatarPose(state,angle,scale=1){
    if(state.turn!==1||state.phase!=='aim'||!state.aiCue)return null;const cue=state.balls.find(b=>b.id===0&&!b.pocketed);if(!cue)return null;
    const r=16/Math.max(.2,scale),p=point(cue),dx=Math.cos(angle),dy=Math.sin(angle);
    for(const length of [190,130,250,80])for(const side of [1,-1])for(const offset of [r+18,r+60,r+105]){const x=p.x-dx*length-dy*offset*side,y=p.y-dy*length+dx*offset*side;
      if(x<r+8||x>VIEW.w-r-8||y<r+8||y>VIEW.h-r-8)continue;
      if(state.balls.some(b=>!b.pocketed&&Math.hypot(point(b).x-x,point(b).y-y)<r+P.R*VIEW.scale+8))continue;
      if(P.wells.some(b=>Math.hypot(point(b).x-x,point(b).y-y)<r+b.radius*VIEW.scale+8))continue;
      return{x,y,r,docked:false};}
    return{docked:true};
  }
  return{VIEW,PORTRAIT_CROP,draw,point,screenToTable,color,avatarPose};
});
