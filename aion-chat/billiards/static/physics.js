(function(root,factory){const api=factory();if(typeof module==='object')module.exports=api;else(root.Billiards??={}).Physics=api;})(globalThis,function(){
  'use strict';
  const W=2.54,H=1.27,R=.028575,FOOT=W*.75,HEAD=W*.25,DT=1/240;
  const FRICTION=.22,SLEEP=.008,REST=.97,CUSHION=.82,CORNER=.085,SIDE=.075;
  const pockets=[{x:0,y:0},{x:W/2,y:0},{x:W,y:0},{x:W,y:H},{x:W/2,y:H},{x:0,y:H}];
  // Preserve each well's horizontal diameter; move its round bowl behind the nose.
  const wells=pockets.map((p,i)=>{const side=i===1||i===4;return{x:p.x+(side?0:p.x===0?-.03:.03),y:p.y+(p.y===0?-1:1)*(side?.04:.03),radius:(side?33:36)*W/1000,innerRadius:(side?25:27)*W/1000};});
  const rails=[{axis:'y',v:0,a:CORNER,b:W/2-SIDE,n:1},{axis:'y',v:0,a:W/2+SIDE,b:W-CORNER,n:1},{axis:'y',v:H,a:CORNER,b:W/2-SIDE,n:-1},{axis:'y',v:H,a:W/2+SIDE,b:W-CORNER,n:-1},{axis:'x',v:0,a:CORNER,b:H-CORNER,n:1},{axis:'x',v:W,a:CORNER,b:H-CORNER,n:-1}];
  // Cushion noses taper into the throat. These exact vertices are also rendered.
  const bevels=rails.flatMap(l=>[[-1,l.a],[1,l.b]].map(([side,t])=>{const a={x:l.axis==='x'?l.v:t,y:l.axis==='y'?l.v:t},outer={x:l.axis==='x'?l.v-l.n*.04:t+side*.04,y:l.axis==='y'?l.v-l.n*.04:t+side*.04},pocketId=pockets.reduce((best,p,i)=>Math.hypot(a.x-p.x,a.y-p.y)<Math.hypot(a.x-pockets[best].x,a.y-pockets[best].y)?i:best,0),well=wells[pocketId],dx=outer.x-a.x,dy=outer.y-a.y,ax=a.x-well.x,ay=a.y-well.y,A=dx*dx+dy*dy,B=2*(ax*dx+ay*dy),C=ax*ax+ay*ay-well.radius*well.radius,k=Math.max(0,Math.min(1,(-B-Math.sqrt(Math.max(0,B*B-4*A*C)))/(2*A)));return{a,b:{x:a.x+k*dx,y:a.y+k*dy},outer,pocketId};}));
  function random(seed){let s=(seed>>>0)||1;return()=>{s+=0x6D2B79F5;let t=s;t=Math.imul(t^(t>>>15),t|1);t^=t+Math.imul(t^(t>>>7),t|61);return((t^(t>>>14))>>>0)/4294967296;};}
  function createRack(seed=1){
    const rng=random(seed),ids=[1,2,3,4,5,6,7,9,10,11,12,13,14,15];
    for(let i=ids.length-1;i>0;i--){const j=Math.floor(rng()*(i+1));[ids[i],ids[j]]=[ids[j],ids[i]];}
    const solid=ids.splice(ids.findIndex(id=>id<8),1)[0],stripe=ids.splice(ids.findIndex(id=>id>8),1)[0];
    const balls=[{id:0,x:HEAD*.68,y:H/2,vx:0,vy:0,pocketed:false}];
    for(let row=0;row<5;row++)for(let col=0;col<=row;col++){
      const id=row===2&&col===1?8:row===4&&col===0?solid:row===4&&col===4?stripe:ids.pop();
      balls.push({id,x:FOOT+row*Math.sqrt(3)*(R+.000015),y:H/2+(col-row/2)*2*(R+.000015),vx:0,vy:0,pocketed:false});
    }return balls.sort((a,b)=>a.id-b.id);
  }
  function createWorld(balls){const frozen=new Set();for(const b of balls)for(const l of rails){const t=l.axis==='y'?b.x:b.y;if(t>=l.a&&t<=l.b&&Math.abs((b[l.axis]-l.v)*l.n-R)<1e-5)frozen.add(b.id+':'+l.axis+l.v);}return{balls:balls.map(b=>({...b})).sort((a,b)=>a.id-b.id),events:[],steps:0,time:0,frozen,leftFrozen:new Set()};}
  function event(w,type,ids,extra={}){w.events.push({type,time:w.time,ids,...extra});}
  function strike(w,shot){
    if(!Number.isFinite(shot.angle)||!Number.isFinite(shot.power)||shot.power<0||shot.power>100)throw Error('Invalid shot');
    const cue=w.balls.find(b=>b.id===0&&!b.pocketed);if(!cue)throw Error('Cue ball is not on the table');
    if(w.balls.some(b=>!b.pocketed&&Math.hypot(b.vx,b.vy)>SLEEP))throw Error('Wait for the balls to stop');
    const speed=.22+7.78*shot.power/100;cue.vx=Math.cos(shot.angle)*speed;cue.vy=Math.sin(shot.angle)*speed;
    w.events=[];w.time=0;w.steps=0;
  }
  function pocketFor(b){
    for(let i=0;i<6;i++){
      const p=pockets[i];if(i===1||i===4){const depth=i===1?b.y:H-b.y;
        if(depth<.006&&Math.abs(b.x-p.x)<SIDE-.012)return i;
      }else{const dx=i===0||i===5?b.x:W-b.x,dy=i<3?b.y:H-b.y;
        if(dx+dy<.071&&Math.hypot(dx,dy)<.082)return i;
      }
    }return -1;
  }
  function step(w){
    const moving=w.balls.filter(b=>!b.pocketed);let max=0;for(const b of moving)max=Math.max(max,Math.hypot(b.vx,b.vy));
    if(max===0)return true;
    const count=Math.max(1,Math.ceil(max*DT/(R*.5))),h=DT/count;
    for(let sub=0;sub<count;sub++){
      w.time+=h;
      for(const b of moving){if(b.pocketed)continue;const oldX=b.x;b.x+=b.vx*h;b.y+=b.vy*h;if(b.id===0&&oldX<HEAD&&b.x>=HEAD)event(w,'crossHead',[0]);
        const pocketId=pocketFor(b);if(pocketId>=0){b.pocketed=true;b.vx=b.vy=0;event(w,'pocket',[b.id],{pocketId});continue;}
        for(const l of rails){const tangent=l.axis==='y'?b.x:b.y;if(tangent<l.a||tangent>l.b)continue;
          const d=(b[l.axis]-l.v)*l.n,key=b.id+':'+l.axis+l.v;if(d>R+1e-5)w.leftFrozen.add(key);if(d<R){b[l.axis]=l.v+l.n*R;const v=l.axis==='y'?'vy':'vx';if(b[v]*l.n<0){b[v]*=-CUSHION;event(w,'rail',[b.id],{rail:l.axis+l.v,counts:!w.frozen.has(key)||w.leftFrozen.has(key)});}}
        }
        for(const face of bevels){const sx=face.b.x-face.a.x,sy=face.b.y-face.a.y,t=Math.max(0,Math.min(1,((b.x-face.a.x)*sx+(b.y-face.a.y)*sy)/(sx*sx+sy*sy))),px=face.a.x+t*sx,py=face.a.y+t*sy;let dx=b.x-px,dy=b.y-py,d=Math.hypot(dx,dy),limit=R;
          if(d<limit&&d>1e-9){const nx=dx/d,ny=dy/d,v=b.vx*nx+b.vy*ny;b.x=px+nx*limit;b.y=py+ny*limit;
            if(v<0){b.vx-=(1+CUSHION)*v*nx;b.vy-=(1+CUSHION)*v*ny;event(w,'rail',[b.id],{rail:'jaw'});}}
        }
        if(!Number.isFinite(b.x+b.y+b.vx+b.vy)||b.x<-.15||b.x>W+.15||b.y<-.15||b.y>H+.15)throw Error('Simulation left valid table geometry');
      }
      for(let i=0;i<moving.length;i++){const a=moving[i];if(a.pocketed)continue;
        for(let j=i+1;j<moving.length;j++){const b=moving[j];if(b.pocketed)continue;let dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy);
          if(d>2*R+1e-8)continue;const nx=d>1e-9?dx/d:1,ny=d>1e-9?dy/d:0,closing=(a.vx-b.vx)*nx+(a.vy-b.vy)*ny;
          const overlap=2*R-d;if(overlap>0){a.x-=nx*overlap/2;a.y-=ny*overlap/2;b.x+=nx*overlap/2;b.y+=ny*overlap/2;}
          if(closing>1e-7){const impulse=(1+REST)*closing/2;a.vx-=impulse*nx;a.vy-=impulse*ny;b.vx+=impulse*nx;b.vy+=impulse*ny;event(w,'contact',[a.id,b.id]);}
        }
      }
      for(const b of moving){if(b.pocketed)continue;const speed=Math.hypot(b.vx,b.vy),after=Math.max(0,speed-FRICTION*h);
        if(after<SLEEP){b.vx=b.vy=0;}else{b.vx*=after/speed;b.vy*=after/speed;}}
    }w.steps++;return !w.balls.some(b=>!b.pocketed&&(b.vx!==0||b.vy!==0));
  }
  function simulateShot(balls,shot,maxSteps=14000){const w=createWorld(balls);strike(w,shot);let settled=false;
    for(let n=0;n<maxSteps;n++)if(step(w)){settled=true;break;}return{balls:w.balls,events:w.events,settled};}
  return{W,H,R,FOOT,HEAD,DT,FRICTION,pockets,wells,rails,bevels,createRack,createWorld,strike,step,simulateShot,random};
});
