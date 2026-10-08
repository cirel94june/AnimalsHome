(function(root,factory){const api=factory(root.Billiards?.Physics||(typeof require==='function'?require('./physics.js'):null));if(typeof module==='object')module.exports=api;else(root.Billiards??={}).Rules=api;})(globalThis,function(P){
  'use strict';const clone=s=>structuredClone(s),group=id=>id>0&&id<8?'solids':id>8?'stripes':null,other=g=>g==='solids'?'stripes':'solids';
  const groupName=g=>g==='solids'?'全色球（1–7）':g==='stripes'?'花色球（9–15）':'开放球桌';
  function newGame(seed=1,breaker=0,mode='casual'){return{mode:mode==='strict'?'strict':'casual',shotCount:0,balls:P.createRack(seed),phase:'placement',turn:breaker,groups:[null,null],isBreak:true,breaker,placement:'head',call:null,tempGroup:null,decision:null,seed,result:null,error:null,message:'把白球放在左侧开球区，然后确认。',lastShot:null};}
  function remaining(s,g){return s.balls.filter(b=>!b.pocketed&&group(b.id)===g);}
  function effectiveGroup(s){return s.groups[s.turn]||s.tempGroup||(s.mode==='casual'&&!s.groups[0]&&!s.groups[1]?['solids','stripes'].find(g=>!remaining(s,g).length):null);}
  function legalTargets(s){const g=effectiveGroup(s);return s.balls.filter(b=>!b.pocketed&&b.id!==0&&(s.isBreak?true:g?(remaining(s,g).length?group(b.id)===g:b.id===8):b.id!==8)).map(b=>b.id);}
  function validatePlacement(s,x,y){
    if(!Number.isFinite(x)||!Number.isFinite(y)||x<P.R||x>P.W-P.R||y<P.R||y>P.H-P.R)return{ok:false,reason:'白球需要完整放在球桌内，避开球袋。'};
    if(s.placement==='head'&&x>=P.HEAD-1e-8)return{ok:false,reason:'这次白球只能放在左侧开球线后。'};
    if(s.balls.some(b=>b.id!==0&&!b.pocketed&&Math.hypot(b.x-x,b.y-y)<2*P.R-1e-6))return{ok:false,reason:'白球不能与其他球重叠。'};
    if(P.pockets.some(p=>Math.hypot(x-p.x,y-p.y)<P.R*1.8))return{ok:false,reason:'请远离球袋放置白球。'};
    return{ok:true,reason:''};
  }
  function validateCall(s,call){if(s.mode==='casual'||s.isBreak)return{ok:true};if(call?.safety)return{ok:true};if(!call||!Number.isInteger(call.ballId)||!legalTargets(s).includes(call.ballId)||!Number.isInteger(call.pocketId)||call.pocketId<0||call.pocketId>5)return{ok:false,reason:'请选择一颗合法目标球和它的目标球袋，或选择安全球。'};return{ok:true};}
  function spot(s,id){const b=s.balls.find(b=>b.id===id);if(!b)return;const limit=P.W-P.R;
    const intervals=s.balls.filter(o=>o.id!==id&&!o.pocketed).map(o=>{const d=2*P.R+(o.id===0?.0005:.00002),dy=Math.abs(o.y-P.H/2);return dy<d?[o.x-Math.sqrt(d*d-dy*dy),o.x+Math.sqrt(d*d-dy*dy)]:null;}).filter(Boolean);
    let x=P.FOOT;for(let n=0;n<50;n++){const blocked=intervals.find(([a,z])=>x>a-1e-9&&x<z-1e-9);if(!blocked)break;x=blocked[1]+.000001;}
    if(x>limit){x=P.FOOT;for(let n=0;n<50;n++){const blocked=intervals.find(([a,z])=>x>a+1e-9&&x<z+1e-9);if(!blocked)break;x=blocked[0]-.000001;}}
    if(x<P.R||x>limit)throw Error('No valid spotting position');Object.assign(b,{x,y:P.H/2,vx:0,vy:0,pocketed:false});
  }
  function spotCandidates(s){if(s.placement!=='head'||s.isBreak)return[];const targets=legalTargets(s).map(id=>s.balls.find(b=>b.id===id));if(!targets.length||targets.some(b=>b.x>=P.HEAD-1e-8))return[];const nearest=Math.max(...targets.map(b=>b.x));return targets.filter(b=>Math.abs(b.x-nearest)<1e-7).map(b=>b.id);}
  function requestSpot(s,id){if(!spotCandidates(s).includes(id))return{...s,error:'现在不能请求移出这颗球。'};const n=clone(s);spot(n,id);n.message=`${id} 号球已移到脚点附近，现在可以从开球线后击打。`;n.error=null;return n;}
  function claimable(s){return !s.groups[0]&&!s.groups[1]&&!s.isBreak?['solids','stripes'].filter(g=>remaining(s,g).length===0):[];}
  function claimGroup(s,g){if(!claimable(s).includes(g))return{...s,error:'只有已全部离桌的整组球才能临时认领。'};const n=clone(s);n.tempGroup=g;n.call={ballId:8,pocketId:null};n.message=`临时认领${groupName(g)}：这一杆可以指定黑八。`;n.error=null;return n;}
  function placement(s,restriction){s.phase='placement';s.placement=restriction;s.call=null;s.tempGroup=null;const cue=s.balls.find(b=>b.id===0);cue.pocketed=false;cue.vx=cue.vy=0;
    if(!validatePlacement(s,cue.x,cue.y).ok){let found=false;for(let x=P.R+.04;x<(restriction==='head'?P.HEAD:P.W)-P.R;x+=.07){for(let y=P.H/2;y<P.H-P.R;y+=.07)if(validatePlacement(s,x,y).ok){cue.x=x;cue.y=y;found=true;break;}if(found)break;}}
    return s;
  }
  function decision(s,type,actor,options){s.phase='decision';s.turn=actor;s.decision={type,actor,originalTurn:s.lastShot.shooter,options};s.placement=null;s.call=null;return s;}
  function applyShot(s,shot,simulation){
    const callCheck=validateCall(s,shot.call);if(!callCheck.ok)return{...s,error:callCheck.reason};if(!simulation.settled)return{...s,error:'球还没有完全停止，暂不能判断结果。'};
    const n=clone(s);n.balls=clone(simulation.balls);n.error=null;n.call=null;n.decision=null;n.tempGroup=null;n.placement=null;const ev=simulation.events,shooter=s.turn,incoming=1-shooter;
    const contacts=ev.filter(e=>e.type==='contact'&&e.ids.includes(0)),time=contacts.length?Math.min(...contacts.map(e=>e.time)):Infinity;
    const first=contacts.filter(e=>e.time<=time+1e-5).map(e=>e.ids.find(id=>id!==0)),targets=legalTargets(s);
    const pockets=ev.filter(e=>e.type==='pocket'),off=ev.filter(e=>e.type==='offtable');const pots=pockets.map(e=>e.ids[0]);
    let foul=null;
    if(pots.includes(0)||off.some(e=>e.ids.includes(0)))foul='白球落袋（洗袋）';
    else if(!first.length)foul='白球没有碰到任何目标球';
    else if(!s.isBreak&&!first.some(id=>targets.includes(id)))foul='先碰到了不属于合法目标的球';
    else if(s.placement==='head'&&first.every(id=>s.balls.find(b=>b.id===id).x<P.HEAD-1e-8)&&!ev.some(e=>e.type==='crossHead'&&e.time<=time+1e-5))foul='从开球线后出杆，白球须先越过开球线才能碰线后的球';
    else if(!pockets.length&&!ev.some(e=>e.type==='rail'&&e.counts!==false&&e.time>=time-1e-5))foul='碰球后没有球落袋，也没有球再碰库边';
    else if(off.length)foul='有球离开球桌';
    n.shotCount=(s.shotCount||0)+1;n.lastShot={shooter,first,pots,foul,called:shot.call,pocketEvents:pockets.map(e=>({ballId:e.ids[0],pocketId:e.pocketId}))};
    const eightPotted=pots.includes(8),eightOff=off.some(e=>e.ids.includes(8));
    if(s.isBreak){
      if(eightPotted||eightOff){if(foul){n.message='开球黑八离桌且犯规：对方选择移回黑八并在线后自由球，或重新开球。';return decision(n,'break-eight-foul',incoming,[{id:'spot-head',label:'移回黑八 · 线后自由球'},{id:'rebreak',label:'重新摆球 · 我来开球'}]);}n.message='合法开球打进黑八，不直接判胜。请选择移回黑八继续，或重新开球。';return decision(n,'break-eight',shooter,[{id:'spot-play',label:'移回黑八 · 继续打'},{id:'rebreak',label:'重新摆球 · 再开一次'}]);}
      const rails=new Set(ev.filter(e=>e.type==='rail'&&e.counts!==false&&e.ids[0]!==0).map(e=>e.ids[0]));
      if(!pots.some(id=>id!==0)&&rails.size<4){n.message='开球未进目标球，且不足四颗目标球碰库：对方可接受现状或选择重开。';return decision(n,'illegal-break',incoming,[{id:'accept',label:pots.includes(0)?'接受球阵 · 线后自由球':'接受当前球阵'},{id:'rebreak-self',label:'重新摆球 · 我来开球'},{id:'rebreak-opponent',label:'重新摆球 · 原开球者再开'}]);}
      if(foul){n.message=`开球犯规：${foul}。对方可接受球阵或在线后放白球。`;const opts=[];if(!pots.includes(0)&&!off.some(e=>e.ids.includes(0)))opts.push({id:'accept',label:'接受现状 · 原位击球'});opts.push({id:'head',label:'线后自由球'});return decision(n,'break-foul',incoming,opts);}
      n.isBreak=false;n.phase='aim';n.turn=pots.some(id=>id!==0)?shooter:incoming;n.message=n.turn===shooter?'合法开球进球，继续击打；球桌仍开放。':'合法开球没有进球，交换回合；球桌仍开放。';return n;
    }
    const g=effectiveGroup(s),eligible=!!g&&!remaining(s,g).length;
    if(eightPotted||eightOff){const called=shot.call?.ballId===8&&pockets.some(e=>e.ids[0]===8&&e.pocketId===shot.call.pocketId);const win=eightPotted&&!eightOff&&eligible&&!foul&&(s.mode==='casual'||called);
      n.phase='over';n.result={winner:win?shooter:incoming,reason:win?(s.mode==='casual'?'合法打进最后的黑八':'合法打进指定球袋的黑八'):foul?`${eightOff?'黑八离桌':'黑八入袋'}并犯规：${foul}`:!eligible?`自己的球还未清完，黑八提前${eightOff?'离桌':'入袋'}`:eightOff?'黑八飞出球桌':'黑八进入了未指定的球袋'};n.message=n.result.reason;return n;
    }
    n.isBreak=false;
    if(foul){n.turn=incoming;n.message=`犯规：${foul}。对方获得全桌自由球。`;return placement(n,'any');}
    const eligiblePots=pockets.filter(e=>group(e.ids[0])).sort((a,b)=>a.time-b.time||a.ids[0]-b.ids[0]);
    const casual=s.mode==='casual',assigned=s.groups[shooter];
    const made=casual?eligiblePots.some(e=>!assigned||group(e.ids[0])===assigned):!shot.call?.safety&&pockets.some(e=>e.ids[0]===shot.call?.ballId&&e.pocketId===shot.call?.pocketId);
    if(made&&!s.groups[shooter]){n.groups[shooter]=casual?group(eligiblePots[0].ids[0]):group(shot.call.ballId);n.groups[incoming]=other(n.groups[shooter]);}
    n.turn=made?shooter:incoming;n.phase='aim';if(casual){n.message=made?'合法进球，继续击球。':'没有打进本组球，交换回合。';return n;}n.message=made?`合法打进 ${shot.call.ballId} 号球，继续击打。`:shot.call?.safety?'安全球完成，交换回合；落袋球保留。':'指定球没有进入指定球袋，交换回合。';return n;
  }
  function chooseDecision(s,id){if(s.phase!=='decision'||!s.decision.options.some(o=>o.id===id))return{...s,error:'请选择当前有效的开球处理方式。'};const n=clone(s),d=s.decision;
    if(id==='rebreak'||id==='rebreak-self'||id==='rebreak-opponent'){const breaker=id==='rebreak-opponent'?d.originalTurn:d.actor;const fresh=newGame(s.seed+1,breaker,s.mode);fresh.message='重新摆球，请在开球线后放白球。';return fresh;}
    n.decision=null;n.isBreak=false;n.call=null;n.tempGroup=null;n.turn=d.actor;n.message='已接受开球处理结果，球桌仍开放。';if(id==='spot-play'||id==='spot-head')spot(n,8);
    if(id==='head'||id==='spot-head'||(id==='accept'&&n.balls.find(b=>b.id===0).pocketed))return placement(n,'head');n.phase='aim';n.placement=null;return n;
  }
  function stalemate(s){const n=newGame(s.seed+1,s.breaker,s.mode);n.message='僵局申请已接受：重新摆球，由原开球者开球。';return n;}
  return{newGame,legalTargets,remaining,group,groupName,validatePlacement,validateCall,applyShot,chooseDecision,spotCandidates,requestSpot,claimable,claimGroup,stalemate,placement};
});
