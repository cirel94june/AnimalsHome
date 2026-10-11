(function(root,factory){const api=factory(root.Billiards?.Physics||(typeof require==='function'?require('./physics.js'):null),root.Billiards?.Rules||(typeof require==='function'?require('./rules.js'):null));if(typeof module==='object')module.exports=api;else(root.Billiards??={}).AI=api;})(globalThis,function(P,R){
  'use strict';const defaults={low:50,medium:80,high:95},budgets={low:12,medium:36,high:72};
  const yieldTurn=()=>new Promise(resolve=>setTimeout(resolve,0));
  function clearPath(balls,a,z,ignore=[]){const dx=z.x-a.x,dy=z.y-a.y,len=Math.hypot(dx,dy);if(len<1e-7)return false;
    return !balls.some(b=>!b.pocketed&&!ignore.includes(b.id)&&(()=>{const t=((b.x-a.x)*dx+(b.y-a.y)*dy)/(len*len);return t>0&&t<1&&Math.hypot(b.x-a.x-t*dx,b.y-a.y-t*dy)<2*P.R+.002;})());}
  function pots(s,difficulty){const cue=s.balls.find(b=>b.id===0&&!b.pocketed),targets=R.legalTargets(s),candidates=[];if(!cue)return candidates;
    for(const id of targets){const obj=s.balls.find(b=>b.id===id);for(let pi=0;pi<6;pi++){const p=P.pockets[pi],dx=p.x-obj.x,dy=p.y-obj.y,d=Math.hypot(dx,dy);if(d<.03)continue;
        const ghost={x:obj.x-dx/d*2*P.R,y:obj.y-dy/d*2*P.R},q=Math.hypot(ghost.x-cue.x,ghost.y-cue.y),dot=((obj.x-cue.x)*dx+(obj.y-cue.y)*dy)/(Math.hypot(obj.x-cue.x,obj.y-cue.y)*d);
        if(ghost.x<P.R||ghost.x>P.W-P.R||ghost.y<P.R||ghost.y>P.H-P.R||dot<.08)continue;
        if(!clearPath(s.balls,cue,ghost,[0,id])||!clearPath(s.balls,obj,p,[id,0]))continue;
        const cost=d+q*.6+(1-dot)*2,angle=Math.atan2(ghost.y-cue.y,ghost.x-cue.x);for(const power of [28,46,67])candidates.push({angle,power,call:{ballId:id,pocketId:pi},cost});
      }
      if(difficulty==='high')for(const pi of [0,1,2,3,4,5])for(const horizontal of [true,false]){
        const p=P.pockets[pi],reflect=horizontal?{x:p.x,y:obj.y<P.H/2?2*P.H-p.y:-p.y}:{x:obj.x<P.W/2?2*P.W-p.x:-p.x,y:p.y};
        const d=Math.hypot(reflect.x-obj.x,reflect.y-obj.y),ghost={x:obj.x-(reflect.x-obj.x)/d*2*P.R,y:obj.y-(reflect.y-obj.y)/d*2*P.R};
        if(clearPath(s.balls,cue,ghost,[0,id]))candidates.push({angle:Math.atan2(ghost.y-cue.y,ghost.x-cue.x),power:50,call:{ballId:id,pocketId:pi},cost:d+2});
      }
    }return candidates.sort((a,b)=>a.cost-b.cost);
  }
  function safeties(s,difficulty){const cue=s.balls.find(b=>b.id===0&&!b.pocketed),out=[];for(const id of R.legalTargets(s)){const b=s.balls.find(b=>b.id===id),angle=Math.atan2(b.y-cue.y,b.x-cue.x);for(const power of [18,36])out.push({angle,power,call:{safety:true},cost:10});if(difficulty!=='low')for(const y of [-b.y,2*P.H-b.y])out.push({angle:Math.atan2(y-cue.y,b.x-cue.x),power:42,call:{safety:true},cost:12});}return out;}
  function combinations(s){const cue=s.balls.find(b=>b.id===0),out=[];for(const callId of R.legalTargets(s))for(const firstId of R.legalTargets(s)){if(callId===firstId||callId===8)continue;const obj=s.balls.find(b=>b.id===callId),first=s.balls.find(b=>b.id===firstId);for(let pi=0;pi<6;pi++){const p=P.pockets[pi],dx=p.x-obj.x,dy=p.y-obj.y,d=Math.hypot(dx,dy),contact={x:obj.x-dx/d*2*P.R,y:obj.y-dy/d*2*P.R},sx=contact.x-first.x,sy=contact.y-first.y,sd=Math.hypot(sx,sy);if(sd<.01)continue;const ghost={x:first.x-sx/sd*2*P.R,y:first.y-sy/sd*2*P.R};if(clearPath(s.balls,cue,ghost,[0,firstId])&&clearPath(s.balls,first,contact,[firstId,callId]))out.push({angle:Math.atan2(ghost.y-cue.y,ghost.x-cue.x),power:46,call:{ballId:callId,pocketId:pi},cost:4+d+sd});}}return out.sort((a,b)=>a.cost-b.cost);}
  async function planTurn(state,{difficulty='medium',accuracy=defaults[difficulty],seed=1,cancelled=()=>false}={}){
    if(cancelled()||state.phase==='over'||state.phase==='error')return null;const s=structuredClone(state),rng=P.random(seed),level=budgets[difficulty]?difficulty:'medium';accuracy=Math.max(0,Math.min(100,Number.isFinite(+accuracy)?+accuracy:defaults[level]));
    if(s.phase==='decision'){const ids=s.decision.options.map(o=>o.id),preferred=['spot-head','spot-play','head','rebreak-self','accept','rebreak'];return{type:'decision',id:preferred.find(id=>ids.includes(id))||ids[0]};}
    if(s.phase==='placement'){
      const spot=R.spotCandidates(s);if(spot.length)return{type:'spot',ballId:spot[0]};
      if(s.placement==='any'){for(const id of R.legalTargets(s)){const obj=s.balls.find(b=>b.id===id);for(const p of P.pockets){const d=Math.hypot(p.x-obj.x,p.y-obj.y),x=obj.x-(p.x-obj.x)/d*.18,y=obj.y-(p.y-obj.y)/d*.18;if(R.validatePlacement(s,x,y).ok&&clearPath(s.balls,obj,p,[id,0]))return{type:'place',x,y};}}}
      for(const [x,y]of[[P.HEAD*.6,P.H/2],[P.HEAD*.5,P.H*.7],[P.HEAD*.5,P.H*.3]])if(R.validatePlacement(s,x,y).ok)return{type:'place',x,y};
      for(let x=P.R+.02;x<(s.placement==='head'?P.HEAD:P.W)-P.R;x+=.065)for(let y=P.R+.02;y<P.H-P.R;y+=.065)if(R.validatePlacement(s,x,y).ok)return{type:'place',x,y};return null;
    }
    const claim=R.claimable(s);if(s.mode!=='casual'&&claim.length&&!s.tempGroup)return{type:'claim',group:claim[0]};
    if(s.isBreak){const cue=s.balls.find(b=>b.id===0),apex=s.balls.filter(b=>b.id&&!b.pocketed).sort((a,b)=>a.x-b.x)[0];return{type:'shot',shot:{angle:Math.atan2(apex.y-cue.y,apex.x-cue.x)+(rng()*2-1)*4*(1-accuracy/100)*Math.PI/180,power:Math.max(1,Math.min(100,90+(rng()*2-1)*12*(1-accuracy/100))),call:null}};}
    let candidates=pots(s,level),safe=safeties(s,level),budget=budgets[level];
    // Reserve search budget for real safety/bank/combo trials, rather than silently truncating them away.
    const direct=candidates.filter(c=>c.cost<4),bank=candidates.filter(c=>c.cost>=4);
    candidates=[...direct.slice(0,Math.floor(budget*.65)),...(level==='high'?[...bank.slice(0,8),...combinations(s).slice(0,8)]:[]),...safe].slice(0,budget);
    if(!candidates.length)return null;const ranked=[];
    for(let i=0;i<candidates.length;i++){
      if(cancelled())return null;const candidate=candidates[i],simulation=P.simulateShot(s.balls,candidate),n=R.applyShot(s,candidate,simulation);if(n.error)continue;
      const made=n.turn===s.turn&&!n.lastShot.foul,won=n.result?.winner===s.turn,lost=n.result&&n.result.winner!==s.turn;
      let score=won?10000:lost?-10000:n.lastShot.foul?-600:made?250:0;score+=simulation.events.filter(e=>e.type==='pocket'&&R.group(e.ids[0])===s.groups[s.turn]).length*15;
      if(level!=='low'&&!n.result&&!n.lastShot.foul){const next={...n,phase:'aim',turn:s.turn,placement:null};score+=Math.min(pots(next,'low').length,12)*2;const opponent={...n,phase:'aim',turn:1-s.turn,placement:null};score-=Math.min(pots(opponent,'low').length,12)*(candidate.call?.safety?3:1);}
      ranked.push({shot:candidate,score:score-candidate.cost*.2});if(i%3===2)await yieldTurn();
    }
    if(cancelled()||!ranked.length)return null;ranked.sort((a,b)=>b.score-a.score);const range=Math.max(1,Math.ceil((1-accuracy/100)*Math.min(3,ranked.length))),best=ranked[Math.floor(rng()*range)].shot;
    const shot={angle:best.angle+(rng()*2-1)*4*(1-accuracy/100)*Math.PI/180,power:Math.max(1,Math.min(100,best.power+(rng()*2-1)*12*(1-accuracy/100))),call:best.call};return{type:'shot',shot};
  }
  return{planTurn,defaults,budgets,clearPath};
});
