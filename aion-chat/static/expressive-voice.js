/* Cached speech playback and an opt-in view of the exact synthesis request. */
(function(root) {
  const escape = value => String(value || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const validURL = url => /^\/api\/tts\/audio\/[A-Za-z0-9_-]+_expressive$/.test(url || '');
  const durations = new Map();
  const icon = '<svg viewBox="0 0 24 24" width="21" height="21" aria-hidden="true"><circle cx="5" cy="12" r="1.5" fill="currentColor"/><path d="M9 8a6 6 0 0 1 0 8m4-12a11 11 0 0 1 0 16m4-19a15 15 0 0 1 0 22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
  let enqueue = null;
  let playback = {};
  function render(item) {
    const ready = item.status === 'ready' && validURL(item.url);
    const duration = durations.get(item.url);
    const label = ready ? (duration || '…') : ['pending','working'].includes(item.status) ? '生成中…' : '未生成';
    const source = typeof item.synthesis_text === 'string' ? item.synthesis_text : '这条历史语音未保存合成原文，无法还原声音标签。';
    return `<div class="expressive-voice"><div class="expressive-voice-controls"><button type="button" class="expressive-voice-button" aria-label="播放语音" ${ready ? '' : 'disabled'} data-voice-url="${ready ? escape(item.url) : ''}" data-voice-text="${escape(item.text)}" onclick="event.stopPropagation();ExpressiveVoice.play(this)">${icon}<span class="expressive-voice-duration">${label}</span></button><button type="button" class="expressive-voice-source-toggle" aria-label="显示合成原文" aria-expanded="false" onclick="event.stopPropagation();ExpressiveVoice.toggle(this)">原文</button></div><pre class="expressive-voice-source" hidden>${escape(source)}</pre>${item.error ? `<small class="expressive-voice-error">${escape(item.error)}</small>` : ''}${ready && !duration ? `<audio hidden preload="metadata" src="${escape(item.url)}" onloadedmetadata="ExpressiveVoice.duration(this)" onerror="ExpressiveVoice.duration(this)"></audio>` : ''}</div>`;
  }
  root.ExpressiveVoice = {render, configure(fn, controls = {}) { enqueue = fn; playback = controls; }, play(button) {
    const url = button.dataset.voiceUrl;
    if (!enqueue || !validURL(url)) return;
    const same = playback.isPlaying?.() && String(playback.audio?.src || '').endsWith(url);
    playback.stop?.();
    if (!same) enqueue(url, button.dataset.voiceText || '');
  }, toggle(button) {
    const source = button.closest('.expressive-voice').querySelector('.expressive-voice-source');
    source.hidden = !source.hidden;
    button.setAttribute('aria-expanded', String(!source.hidden));
    button.setAttribute('aria-label', source.hidden ? '显示合成原文' : '收起合成原文');
    button.textContent = source.hidden ? '原文' : '收起';
  }, duration(audio) {
    const seconds = audio.duration;
    const label = Number.isFinite(seconds) && seconds > 0 ? `${Math.ceil(seconds)}″` : '—';
    if (label !== '—') durations.set(audio.getAttribute('src'), label);
    const parent = audio.closest('.expressive-voice');
    parent.querySelector('.expressive-voice-duration').textContent = label;
    parent.querySelector('.expressive-voice-button').setAttribute('aria-label', label === '—' ? '播放语音，时长暂不可用' : `播放语音，${Math.ceil(seconds)}秒`);
    audio.remove();
  }};
})(window);
