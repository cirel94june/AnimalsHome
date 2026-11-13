(() => {
  'use strict';
  const escape = text => String(text).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  function prepare(message, kind) {
    if (!message.id) return null;
    const text = String(message.content || '');
    const fence = /```[ \t]*(?:html|svg)[ \t]*\r?\n([\s\S]*?)```/i.exec(text);
    const match = fence || /(?:<!doctype\s+html[^>]*>\s*)?<html\b[\s\S]*?<\/html\s*>/i.exec(text);
    if (!match) return null;
    const code = fence ? match[1] : match[0];
    if (code.length > 120000 || !/<(?:html|body|div|main|svg|canvas|style)\b/i.test(code)) return null;
    return {content: text.replace(match[0], '').trim() || '给你做的小网页，点下面打开。',
      card: `<div class="chat-work-card"><span>✦ 小网页</span><p>点开就能看，同时收进「爱的印记」。</p><button type="button" data-chat-work-kind="${kind}" data-chat-work-message="${escape(message.id)}">打开并收藏</button><span class="chat-work-status" role="status"></span></div>`};
  }
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-chat-work-message]');
    if (!button) return;
    event.stopPropagation();
    if (button.disabled) return;
    const status = button.parentElement.querySelector('.chat-work-status');
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 20000);
    button.disabled = true; status.textContent = '正在打开……';
    try {
      const response = await fetch('/api/chat-artifacts/collect', {method:'POST', signal:controller.signal, headers:{'Content-Type':'application/json','X-AH-Request':'1'},
        body:JSON.stringify({kind:button.dataset.chatWorkKind, message_id:button.dataset.chatWorkMessage})});
      const data = await response.json();
      if (!response.ok || !/^w_[a-f0-9]{16}$/.test(data.work_id || '')) throw new Error(data.detail || '这次没打开，请再试一次');
      const url = '/works?work=' + encodeURIComponent(data.work_id);
      status.textContent = '已收进爱的印记';
      if (window.parent !== window && typeof window.parent.openSubPage === 'function') window.parent.openSubPage(url);
      else if (typeof window.openSubPage === 'function') window.openSubPage(url);
      else location.href = url;
    } catch (error) { status.textContent = error.name === 'AbortError' ? '打开超时了，请再点一次。' : error.message || '这次没打开，请再试一次'; }
    finally { clearTimeout(timer); button.disabled = false; }
  });
  window.ChatWorks = {prepare};
})();
