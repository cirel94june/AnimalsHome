(function(root,factory){const api=factory();if(typeof module==='object')module.exports=api;else(root.Billiards??={}).Audio=api;})(globalThis,function(){
  'use strict';
  const files=Object.fromEntries(['ball-hit','pocket-drop','cue-hit','turn-change','victory','defeat'].map(name=>[name,(globalThis.Billiards?.deferMount?'/billiards-assets/assets/audio/':'assets/audio/')+name+'.mp3']));
  function createAudio({makeMedia=src=>new Audio(src),makeContext=()=>{const Context=globalThis.AudioContext||globalThis.webkitAudioContext;return Context?new Context():null;},now=()=>performance.now()}={}){
    let unlocked=false,muted=false,volume=.35,foreground=true,context=null,generation=0,available=true;
    const seen=new Set(),voices=new Set(),pool=new Map(),pairs=new Map(),counts={},failures={};let fallbackCount=0;
    function rebalance(){const sum=[...voices].reduce((n,v)=>n+v.level,0),factor=Math.min(1,.8/(sum||1),1/(volume*2*(sum||1)));for(const v of voices){const gain=muted||!foreground?0:Math.min(1,volume*2*v.level*factor);if(v.media)v.media.volume=gain;else if(v.gain&&context)v.gain.gain.setValueAtTime(gain,context.currentTime);}}
    function remove(v){if(!voices.has(v))return;voices.delete(v);if(v.media){v.media.pause();v.media.volume=0;}if(v.osc){try{v.osc.stop();}catch{}v.osc.disconnect();v.gain.disconnect();}rebalance();}
    function stop(){generation++;for(const v of [...voices])remove(v);pairs.clear();}
    function configure(c){if(c.volume!==undefined)volume=Math.max(0,Math.min(1,Number(c.volume)||0));if(c.muted!==undefined)muted=!!c.muted;if(muted||volume===0)stop();rebalance();}
    function unlock(event){if(!event?.isTrusted)return false;unlocked=true;if(!context){try{context=makeContext();}catch{context=null;}}if(context?.state==='suspended')context.resume().catch(()=>{});return true;}
    function fallback(event,level){if(!context||context.state!=='running'||!foreground||muted||!unlocked)return false;try{const osc=context.createOscillator(),gain=context.createGain(),t=context.currentTime,terminal=['victory','defeat'].includes(event.type),duration=terminal?.55:event.type==='pocket-drop'?.22:.07;const v={osc,gain,level};osc.type='sine';osc.frequency.setValueAtTime(event.type==='victory'?660:event.type==='defeat'?196:event.type==='pocket-drop'?120:900,t);osc.frequency.exponentialRampToValueAtTime(event.type==='victory'?880:90,t+duration);osc.connect(gain);gain.connect(context.destination);voices.add(v);rebalance();gain.gain.exponentialRampToValueAtTime(.0001,t+duration);osc.onended=()=>remove(v);osc.start(t);osc.stop(t+duration);fallbackCount++;return true;}catch{return false;}}
    function play(event){
      if(!event||!files[event.type]||seen.has(event.id))return false;seen.add(event.id);if(seen.size>2048)seen.delete(seen.values().next().value);
      if(!unlocked||muted||volume===0||!foreground)return false;
      if(event.type==='ball-hit'){const key=event.pair||event.id,time=now();if(time-(pairs.get(key)??-Infinity)<25)return false;pairs.set(key,time);}
      const terminal=['victory','defeat'].includes(event.type);if(terminal)for(const v of [...voices])remove(v);
      if(voices.size>=8){if(event.type==='ball-hit')return false;remove(voices.values().next().value);}
      const strength=Math.max(.08,Math.min(1,event.strength??1)),level=(event.type==='ball-hit'?.3:terminal?.65:.5)*strength,token=generation;
      try{
        const candidates=pool.get(event.type)||[],busy=new Set([...voices].map(v=>v.media));let media=candidates.find(a=>!busy.has(a));
        if(!media){media=makeMedia(files[event.type]);media.preload='auto';candidates.push(media);pool.set(event.type,candidates);}
        media.currentTime=0;media.muted=false;const v={media,level};voices.add(v);rebalance();media.onended=()=>remove(v);
        let handled=false;const failed=e=>{if(handled)return;handled=true;remove(v);if(token!==generation||!foreground||muted)return;failures[event.type]=(failures[event.type]||0)+1;if(e?.name!=='NotAllowedError')fallback(event,level);};
        media.onerror=()=>failed({name:'MediaError'});Promise.resolve(media.play()).then(()=>{if(token===generation&&voices.has(v))counts[event.type]=(counts[event.type]||0)+1;}).catch(failed);return true;
      }catch{available=false;return fallback(event,level);}
    }
    function setForeground(v){foreground=!!v;if(!foreground)stop();}
    function reset(){stop();seen.clear();}
    const stats=()=>({unlocked,muted,volume,foreground,available,activeVoices:voices.size,played:{...counts},failures:{...failures},fallbackCount,files:{...files}});
    return{unlock,configure,play,stop,reset,setForeground,stats};
  }
  return{files,createAudio};
});
