/* Saved state drives the room; the original game only renders and collects input. */
(() => {
  const $ = id => document.getElementById(id);
  const makeId=()=>globalThis.crypto?.randomUUID?.()||`pool-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  const client = makeId();
  let game=null, mounted=false, busy=false, animating=false, startingAnimation=false;
  let polling=false, visible=!document.hidden, heartbeatAt=0, revision=-1, rack=0, eventId='';
  let pageVisible=true, appForeground=true;
  let labels={user:'你',aion:'AI',connor:'另一位伴侣'}, chatConfig={}, messagesSeen=new Set(), audioQueue=Promise.resolve();
  const bubbleTimers=new Map();
  let statusKey='',messagesKey='',controlEnabled=null,chatBusy=false,voiceRecording=false,activeSpeech=null;
  const error=text=>{$('roomError').textContent=text||'';$('lobbyError').textContent=text||'';};
  async function request(path, data, keepalive=false) {
    const response=await fetch(path,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined,cache:'no-store',keepalive});
    const result=await response.json();
    if(!response.ok)throw Error(result.detail||result.error||'台球室暂时连不上');
    return result;
  }
  const endpoint=part=>`/api/billiards/${game.id}/${part}`;
  const canControl=()=>!!(game&&game.status==='running'&&game.controlled_here&&game.players[0]==='user'&&!busy&&!animating&&game.busy_ms<=0&&visible);
  const label=seat=>labels[game.players[seat]];
  function status() {
    if(!game)return;
    const key=JSON.stringify([game.status,game.controlled_here,game.score,game.rack,game.players,labels,busy,animating,game.error,game.chat_error,game.state.phase,game.state.turn,game.state.result,chatBusy]);
    if(statusKey===key)return;statusKey=key;
    $('seriesScore').textContent=`${game.score[0]} : ${game.score[1]}`;
    $('seriesScore').setAttribute('aria-label',`${label(0)} ${game.score[0]} 比 ${game.score[1]} ${label(1)}`);
    $('rackBadge').textContent=`第 ${game.rack} 局`;
    const watching=!game.players.includes('user')||!game.controlled_here;
    $('matchStatus').textContent=game.status==='finished'?'本局已结束':game.status==='paused'?'已暂停':watching?'正在旁观':'正在打球';
    $('saveState').textContent=busy?'正在保存…':game.error||'已保存到小家 · 随时回来继续';
    $('pauseBtn').textContent=game.status==='running'&&(game.controlled_here||!game.players.includes('user'))?'暂停':'继续';
    $('pauseBtn').setAttribute('aria-label',$('pauseBtn').textContent==='暂停'?'暂停球局':'继续球局');
    $('pauseBtn').hidden=game.status==='finished';
    $('pauseBtn').disabled=busy;
    $('roomCover').hidden=game.status!=='paused'||animating;
    $('roomCoverText').textContent=game.error||'球局已暂停 · 点击继续';
    $('humanCard').querySelector('strong').textContent=label(0);
    $('aiCard').querySelector('strong').textContent=label(1);
    $('leftSeatAvatar').src=game.players[0]==='user'?'/public/UserIcon.png':avatar(game.players[0]);
    $('rightSeatAvatar').src=avatar(game.players[1]);
    $('opponentAvatar').src=avatar(game.players[1]);
    $('opponentAvatar').alt=label(1);
    document.querySelector('.tray-label').firstChild.textContent=`${label(0)}的目标 `;
    if(game.players[0]!=='user')$('turnTitle').textContent=game.state.phase==='over'?`${label(game.state.result.winner)}赢了`:`${label(game.state.turn)}的回合`;
    if(game.players[0]!=='user'||!game.controlled_here){$('placeBtn').disabled=true;$('shootBtn').disabled=true;$('aimLeft').disabled=$('aimRight').disabled=true;}
    $('endTitle').textContent=game.state.result?`${label(game.state.result.winner)}赢了这局`: $('endTitle').textContent;
    $('chatState').textContent=chatBusy?'正在接话…':game.chat_error||'';
  }
  const avatar=actor=>actor==='connor'?'/public/codexicon.png':'/public/gropicon1.png';
  function renderMessages(initial=false) {
    const key=JSON.stringify([game.id,labels,game.messages]);if(key===messagesKey)return;messagesKey=key;
    const list=$('tableMessages'),nearBottom=list.scrollHeight-list.scrollTop-list.clientHeight<70;
    list.replaceChildren();
    for(const message of game.messages){
      const row=document.createElement('div');row.className='table-message';
      const author=document.createElement('strong');author.textContent=labels[message.sender]||message.sender;
      const time=document.createElement('small');time.textContent=`第${message.rack}局 · 第${message.shot}杆`;
      const text=document.createElement('p');text.textContent=message.text;row.append(author,time,text);
      list.append(row);
      if(!initial&&!messagesSeen.has(message.id)&&message.sender!=='user'&&visible){
        showBubble(message);
        if($('tableVoice').checked)speak(message);
      }
      messagesSeen.add(message.id);
    }
    if(initial||nearBottom)list.scrollTop=list.scrollHeight;
    if(!game.messages.length)list.textContent='可以聊球，也可以随便说点什么。';
  }
  function showBubble(message){
    const seat=game.players.indexOf(message.sender);
    if(seat<0||message.sender==='user'||message.rack!==game.rack)return;
    const card=$(seat===0?'humanCard':'aiCard');
    let bubble=card.querySelector('.table-bubble');
    if(!bubble){
      bubble=document.createElement('button');bubble.className='table-bubble';bubble.type='button';
      bubble.setAttribute('aria-live','polite');bubble.onclick=()=>toggleTableChat(true);card.append(bubble);
    }
    const copy=document.createElement('span');copy.className='bubble-copy';
    copy.textContent=`${labels[message.sender]}：`+(message.text.length>110?message.text.slice(0,110)+'…':message.text);bubble.replaceChildren(copy);
    bubble.setAttribute('aria-label',`${labels[message.sender]}说：${message.text}，点击打开桌边聊天`);
    bubble.hidden=false;
    if(portraitRoom())for(const other of document.querySelectorAll('.table-bubble'))if(other!==bubble)other.hidden=true;
    const box=card.getBoundingClientRect(),avatar=card.querySelector('.seat-avatar').getBoundingClientRect();
    const width=Math.min(portraitRoom()?174:200,innerWidth-24),center=avatar.left+avatar.width/2;
    const left=Math.max(12,Math.min(innerWidth-width-12,center-width/2));
    bubble.style.width=width+'px';bubble.style.left=left-box.left+'px';bubble.style.setProperty('--bubble-tail',Math.max(12,Math.min(width-12,center-left))+'px');
    clearTimeout(bubbleTimers.get(message.sender));
    bubbleTimers.set(message.sender,setTimeout(()=>bubble.hidden=true,12000));
    if(!$('roomChat').classList.contains('chat-open'))$('tableChatToggle').classList.add('unread');
  }
  function speak(message){
    const voice=chatConfig[message.sender==='connor'?'tts_connor_voice':'tts_aion_voice'];
    if(!voice){error('先在小家的语音设置里选择这位伴侣的音色');return;}
    audioQueue=audioQueue.then(async()=>{
      if(!visible||voiceRecording)return;
      const response=await fetch('/api/tts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:message.text,voice,msg_id:message.id})});
      if(!response.ok)throw Error('这句语音没能播放，文字和球局都已保存');
      const url=URL.createObjectURL(await response.blob()),audio=new Audio(url);
      if(!visible||voiceRecording){URL.revokeObjectURL(url);return;}
      showBubble(message);
      await new Promise(resolve=>{activeSpeech=()=>{audio.pause();resolve();};audio.onended=audio.onerror=resolve;audio.play().catch(resolve);});activeSpeech=null;URL.revokeObjectURL(url);
    }).catch(e=>error(e.message));
  }
  function apply(data, animate=false, initial=false){
    if(game&&game.rack!==data.rack)for(const bubble of document.querySelectorAll('.table-bubble'))bubble.hidden=true;
    game=data;labels=data.names||labels;
    if(!mounted){
      document.body.classList.add('in-match');
      $('lobby').hidden=true;$('gameApp').hidden=false;
      $('difficulty').value=data.difficulty;$('accuracy').value=data.accuracy;$('rulesMode').value=data.mode;
      Billiards.Game.mount({state:data.state,playerName:label,isAI:seat=>game.players[seat]!=='user',canControl,canPresent:()=>visible,action:submit,restart,onState:s=>{
        if(animating&&!startingAnimation&&!['motion','presenting'].includes(s.phase))animating=false;
        statusKey='';status();
      }});mounted=true;
      eventId=data.last_event?.id||'';
    } else if(!animating&&(data.revision!==revision||data.rack!==rack)){
      if(animate&&data.last_event&&data.last_event.id!==eventId&&data.busy_ms>0){
        startingAnimation=true;animating=true;eventId=data.last_event.id;
        Billiards.session.play(data.last_event.before,data.last_event.action);startingAnimation=false;
      }else if(!data.ai_preview||data.ai_preview.id!==Billiards.session.controller.getState().aiCue?.id)Billiards.session.load(data.state);
    }
    if(!animating){Billiards.session.preview(data.status==='running'?data.ai_preview:null);revision=data.revision;rack=data.rack;}
    status();renderMessages(initial);
    const enabled=canControl();if(enabled!==controlEnabled){controlEnabled=enabled;Billiards.session.refresh();statusKey='';status();}
  }
  async function loadGame(id){
    error('');const data=await request(`/api/billiards/${id}?client=${encodeURIComponent(client)}`);
    history.replaceState(null,'',`/billiards?game=${id}`);apply(data,false,true);
  }
  async function lobby(){
    const data=await request('/api/billiards');labels=data.names;
    $('aionChoiceName').textContent=labels.aion;$('connorChoiceName').textContent=labels.connor;
    for(const button of document.querySelectorAll('[data-opponent]'))button.disabled=!data.available;
    if(!data.available)error('台球室暂时不可用，其他玩法可以正常使用');
    const list=$('savedGames');list.replaceChildren();
    for(const saved of data.games){
      const card=document.createElement('div');card.className='saved-game';
      const title=document.createElement('strong');title.textContent=saved.players.map(p=>labels[p]).join(' & ');
      const description=document.createElement('p');description.textContent=`${{paused:'已暂停',running:'正在打球',finished:'已结束'}[saved.status]} · 局数 ${saved.score.join(':')} · 第${saved.rack}局`;
      const button=document.createElement('button');button.className='quiet';button.textContent=saved.players.includes('user')?'打开球局':'旁观';button.onclick=()=>loadGame(saved.id).catch(e=>error(e.message));card.append(title,description,button);list.append(card);
    }
    $('savedTitle').hidden=!data.games.length;
  }
  async function submit(action,local){
    if(!canControl())return;
    busy=true;status();error('');
    if(action.type==='confirmPlacement'){const cue=local.balls.find(b=>b.id===0);action={...action,position:{x:cue.x,y:cue.y}};}
    try{
      const data=await request(endpoint('action'),{client,revision:game.revision,action_id:makeId(),action});
      busy=false;apply(data,action.type==='shot');
    }catch(e){busy=false;error(e.message);await poll();Billiards.session.load(game.state);status();}
  }
  async function pause(manual=false){
    if(!game||game.status!=='running'||(game.players.includes('user')?!game.controlled_here:!manual))return;
    Billiards.session.cancelCharge();
    try{const data=await request(endpoint('pause'),{client},true);apply(data);}catch(e){error(e.message);}
  }
  async function poll(){
    if(!game||polling||!visible)return;
    polling=true;
    try{
      let data;
      if(game.controlled_here&&game.status==='running'&&Date.now()-heartbeatAt>6000){data=await request(endpoint('heartbeat'),{client});heartbeatAt=Date.now();}
      else data=await request(`/api/billiards/${game.id}?client=${encodeURIComponent(client)}`);
      if(!busy)apply(data,true);
    }catch(e){error(e.message);game.controlled_here=false;status();}
    finally{polling=false;}
  }
  async function restart(){
    if(!game.players.includes('user')){error('这是伴侣们的球局，可以继续旁观');return;}
    if(game.status!=='finished'&&!confirm('结束当前这局并重新摆球？这局不计入胜负。'))return;
    busy=true;status();
    try{
      if(game.status!=='finished')await request(endpoint('finish'),{client});
      animating=false;eventId='';
      const data=await request(endpoint('next'),{client,difficulty:$('difficulty').value,accuracy:+$('accuracy').value,mode:$('rulesMode').value,breaker:+$('breaker').value});
      busy=false;apply(data);Billiards.session.load(data.state);
    }catch(e){busy=false;error(e.message);status();}
  }
  function setVisible(){
    const next=pageVisible&&appForeground&&!document.hidden;
    if(next===visible)return;
    visible=next;
    if(!visible){tableRecorder.cancel();activeSpeech?.();pause();}
    else{animating=false;if(game){Billiards.session.load(game.state);poll();}}
  }
  window.onAionSubPageVisibilityChanged=value=>{pageVisible=!!value;setVisible();};
  window.onAionAppForegroundChanged=value=>{appForeground=!!value;setVisible();};
  document.addEventListener('visibilitychange',setVisible);
  window.addEventListener('pagehide',()=>{if(game?.controlled_here&&game.status==='running')navigator.sendBeacon(endpoint('pause'),new Blob([JSON.stringify({client})],{type:'application/json'}));});
  $('pauseBtn').onclick=async()=>{
    try{if(game.status==='running'&&(game.controlled_here||!game.players.includes('user')))await pause(true);else apply(await request(endpoint('resume'),{client}));}catch(e){error(e.message);}
  };
  $('finishMatch').onclick=async()=>{
    if(!confirm('结束这场球局并离开？已完成的胜负和聊天记录会保留。'))return;
    try{await request(endpoint('finish'),{client});location.href='/billiards';}catch(e){error(e.message);}
  };
  $('roomBack').onclick=async()=>{await pause();location.href='/playground';};
  $('lobbyBack').onclick=()=>location.href='/playground';
  const portraitRoom=()=>matchMedia('(max-width:760px) and (orientation:portrait)').matches;
  function toggleTableChat(open){
    $('roomChat').classList.toggle('mobile-open',open);
    $('roomChat').classList.toggle('chat-open',open);
    $('gameApp').classList.toggle('chat-open',open);
    $('chatDismiss').hidden=!open;
    $('tableChatToggle').setAttribute('aria-expanded',String(open));
    if(open)$('tableChatToggle').classList.remove('unread');
  }
  $('tableChatToggle').onclick=()=>toggleTableChat(!$('roomChat').classList.contains('mobile-open'));
  $('chatDismiss').onclick=()=>toggleTableChat(false);
  matchMedia('(max-width:760px) and (orientation:portrait)').addEventListener('change',()=>toggleTableChat(false));
  async function sendTableText(text,destination={id:game?.id,target:''}){
    if(!text||!destination.id)return;
    chatBusy=true;$('sendChat').disabled=true;status();
    try{await request(`/api/billiards/${destination.id}/chat`,{text,target:destination.target});await poll();}
    finally{chatBusy=false;$('sendChat').disabled=false;status();}
  }
  $('chatForm').onsubmit=async e=>{
    e.preventDefault();const text=$('tableChatInput').value.trim();if(!text||!game||chatBusy)return;
    $('tableChatInput').value='';
    try{await sendTableText(text);}catch(err){error(err.message);$('tableChatInput').value=text;}
  };
  const tableRecorder=Billiards.Voice.bind({button:$('tableVoiceHold'),hint:$('tableVoiceHint'),
    destination:()=>visible&&game?.players.includes('user')&&!chatBusy?{id:game.id,target:game.players.find(p=>p!=='user')}:null,
    send:async(text,target)=>{try{await sendTableText(text,target);}catch(e){$('tableChatInput').value=text;toggleTableChat(true);throw e;}},onRecording:active=>{voiceRecording=active;if(active){activeSpeech?.();Billiards.session?.cancelCharge();}},
    onError:text=>error(text)});
  window.addEventListener('pagehide',()=>{tableRecorder.cancel();activeSpeech?.();});
  $('tableChatInput').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('chatForm').requestSubmit();}};
  for(const button of document.querySelectorAll('[data-opponent]'))button.onclick=async()=>{
    button.disabled=true;
    try{const data=await request('/api/billiards',{opponent:button.dataset.opponent,difficulty:$('lobbyDifficulty').value});await loadGame(data.id);apply(await request(endpoint('resume'),{client}));}
    catch(e){error(e.message);}finally{button.disabled=false;}
  };
  request('/api/chatroom/config').then(config=>chatConfig=config).catch(()=>{});
  const id=new URLSearchParams(location.search).get('game');
  (id?loadGame(id):lobby()).catch(e=>error(e.message));
  setInterval(poll,1000);
})();
