(function(root,factory){const api=factory();if(typeof module==='object')module.exports=api;else root.BilliardsResultCard=api;})(globalThis,function(){
  'use strict';
  const esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
  function render(attachments){
    const card=(attachments||[]).find(item=>item?.type==='billiards_result');
    if(!card||card.players?.length!==2||![0,1].includes(card.winner))return '';
    const winner=card.players[card.winner].name,loser=card.players[1-card.winner].name;
    return `<div class="billiards-result-card"><div class="billiards-result-heading"><span class="billiards-result-badge" aria-hidden="true">8</span><strong>台球战报</strong><span>第 ${esc(card.rack)} 局</span></div><p class="billiards-result-outcome">${esc(winner)}获胜 · ${esc(loser)}落败</p><p class="billiards-result-score">累计胜局：${esc(card.players[0].name)} ${esc(card.score?.[0])} : ${esc(card.score?.[1])} ${esc(card.players[1].name)}</p><p class="billiards-result-detail">${esc(card.shots)} 杆 · ${esc(card.reason)}</p></div>`;
  }
  return{render};
});
