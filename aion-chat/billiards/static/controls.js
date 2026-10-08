(function(root,factory){const api=factory();if(typeof module==='object')module.exports=api;else(root.Billiards??={}).Controls=api;})(globalThis,function(){
  'use strict';
  function powerAt(elapsed){const t=Math.max(0,elapsed)%6000,u=(t<=3000?t:6000-t)/3000;return Math.round(u*u*100);}
  function createCharge(){
    const pointers=new Set();let owner=null,started=0,blocked=false;
    function cancel(clear=false){owner=null;if(clear){pointers.clear();blocked=false;}else blocked=pointers.size>0;}
    function down(id,now,eligible){if(pointers.has(id))return false;pointers.add(id);if(pointers.size>1){cancel();return false;}if(blocked||!eligible)return false;owner=id;started=now;return true;}
    function move(id,inside){if(id===owner&&!inside)cancel();}
    function up(id,now,inside,eligible){const elapsed=now-started,power=powerAt(elapsed),fire=id===owner&&!blocked&&inside&&eligible&&elapsed>=120&&power>0;const result=fire?power:null;owner=null;pointers.delete(id);if(!pointers.size)blocked=false;return result;}
    return{down,move,up,cancel,active:()=>owner!==null,power:now=>owner===null?0:powerAt(now-started),allowsAim:id=>!blocked&&owner===null&&pointers.size===1&&pointers.has(id),owner:()=>owner};
  }
  function tablePoint(x,y,rect,view,portrait=false){
    const u=(x-rect.left)/rect.width,v=(y-rect.top)/rect.height;
    return{x:((portrait?v:u)*view.w-view.x)/view.scale,y:((portrait?1-u:v)*view.h-view.y)/view.scale};
  }
  function aimAngle(cue,p,fallback,cueSide=false){
    if(Math.hypot(p.x-cue.x,p.y-cue.y)<.09)return fallback;
    return cueSide?Math.atan2(cue.y-p.y,cue.x-p.x):Math.atan2(p.y-cue.y,p.x-cue.x);
  }
  return{powerAt,createCharge,tablePoint,aimAngle};
});
