(function(root) {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function songId(song) {
    const value = String(song?.external_id || song?.id || '');
    return /^\d+$/.test(value) && Number.isSafeInteger(Number(value)) && Number(value)>0 ? String(Number(value)) : '';
  }
  const localCover = value => /^\/(?!\/)[^\x00-\x1f\x7f\\]*$/.test(value) ? value : '';
  function coverUrl(song) {
    const id = song?.source_type === 'local' ? '' : songId(song);
    if (id) return '/api/music/cover/' + id;
    const url = String(song?.cover_url || song?.cover || '');
    return localCover(url);
  }
  function knownCover(song) {
    const raw = String(song?.cover_url || song?.cover || '');
    if (raw.startsWith('/')) return localCover(raw);
    try {
      const url = new URL(raw);
      if (!['http:','https:'].includes(url.protocol) || !url.hostname.endsWith('.music.126.net') || url.username || url.password || (url.port && !['80','443'].includes(url.port))) return '';
      url.protocol = 'https:'; url.port = ''; return url.href;
    } catch (_) { return ''; }
  }
  function coverMarkup(song, className = 'music-cover') {
    const css = className.replace(/[^a-zA-Z0-9 _-]/g, '');
    const fallback = coverUrl(song), src = knownCover(song) || fallback;
    return `<span class="${css} music-cover-frame"><span class="music-cover-fallback" aria-hidden="true">♫</span>${src ? `<img data-music-cover data-music-fallback="${escape(fallback)}" src="${escape(src)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ''}</span>`;
  }
  function caption(song) {
    const clean = value => String(value || '').replace(/[\x00-\x1f\[\]［］【】<>`]/g, '').slice(0,160);
    return `🎵 分享音乐：《${clean(song.name)}》 — ${clean(song.artist)}`;
  }
  function composeText(text, attachments) {
    const captions = attachments.filter(a => a?.type === 'music' && songId(a)).map(caption);
    return [String(text || '').trim(), ...captions].filter(Boolean).join('\n');
  }
  function serialize(attachment) {
    if (attachment?.type !== 'music') return attachment.url;
    if (!songId(attachment)) throw new Error('歌曲编号无效，请重新选歌。');
    return {type:'music', id:Number(songId(attachment)), name:String(attachment.name || '').slice(0,160),
      artist:String(attachment.artist || '').slice(0,160), album:String(attachment.album || '').slice(0,160),
      cover:coverUrl(attachment), shared_by:'user'};
  }
  const api = {songId, coverUrl, knownCover, coverMarkup, caption, composeText, serialize};
  root.MusicShare = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;
  document.addEventListener('error', event => {
    const img = event.target;
    if (!img?.matches?.('img[data-music-cover]')) return;
    const fallback = img.dataset.musicFallback;
    if (fallback && img.getAttribute('src') !== fallback && !img.dataset.musicRetried) {
      img.src = fallback; return;
    }
    img.hidden = true;
    if (fallback && !img.dataset.musicRetried) {
      img.dataset.musicRetried = '1';
      setTimeout(() => {
        if (!img.isConnected) return;
        img.loading = 'eager';
        img.src = fallback + '?retry=1';
      }, 1500);
    }
  }, true);
  document.addEventListener('load', event => {
    if (event.target?.matches?.('img[data-music-cover]')) event.target.hidden = false;
  }, true);
  api.setCover = (node, song) => {
    node.classList.add('music-cover-frame'); node.style.backgroundImage = '';
    node.innerHTML = coverMarkup(song, 'music-cover-inner');
  };
  api.preview = song => `<span class="music-share-preview-content">${coverMarkup(song,'music-share-mini')}<span><strong>${escape(song.name)}</strong><small>${escape(song.artist)}</small></span></span>`;
  let dialog = null, sequence = 0, request = null;
  function close() { ++sequence; request?.abort(); dialog?.close(); }
  api.openPicker = options => {
    if (!dialog) {
      dialog = document.createElement('dialog'); dialog.className = 'music-share-dialog';
      dialog.innerHTML = `<header><h2>分享歌曲，一起听</h2><button type="button" class="music-share-close" aria-label="关闭选歌">×</button></header>
        <p>选好后会放进输入区，点“发送”才分享。原曲由播放器播放，无需开启语音朗读。</p>
        <form><input type="search" aria-label="搜索要分享的歌曲" placeholder="歌名、歌手" required maxlength="100"><button type="submit" class="music-share-search">搜索</button></form>
        <p class="music-share-status" role="status" aria-live="polite"></p><div class="music-share-results"></div>`;
      document.body.append(dialog);
      dialog.querySelector('.music-share-close').addEventListener('click', close);
      dialog.addEventListener('cancel', close);
      dialog.addEventListener('close', () => { ++sequence; request?.abort(); });
    }
    ++sequence; request?.abort();
    const input = dialog.querySelector('input'); const status = dialog.querySelector('.music-share-status');
    const results = dialog.querySelector('.music-share-results'); results.replaceChildren(); status.textContent = ''; input.value = '';
    dialog.querySelector('form').onsubmit = async event => {
      event.preventDefault(); const query = input.value.trim(); if (!query) return;
      const token = ++sequence; request?.abort(); request = new AbortController();
      status.textContent = '正在搜索网易云…'; results.replaceChildren();
      try {
        const response = await fetch('/api/music-station/netease/search?' + new URLSearchParams({q:query,limit:12}), {signal:request.signal,cache:'no-store'});
        const data = await response.json(); if (!response.ok) throw new Error('歌曲搜索暂时没有完成，请稍后重试。');
        if (token !== sequence || !dialog.open) return;
        status.textContent = data.songs?.length ? '挑一首放进输入区，可以附一句想说的话。' : '没有找到，可以换个歌名或歌手。';
        for (const song of (data.songs || []).slice(0,12)) {
          const id = songId(song); if (!id) continue;
          const row = document.createElement('div'); row.className = 'music-share-result';
          row.innerHTML = coverMarkup(song,'music-share-mini');
          const label = document.createElement('span'); label.textContent = `${song.name} · ${song.artist}`;
          const select = document.createElement('button'); select.type = 'button'; select.className = 'music-share-select'; select.textContent = '选这首';
          select.addEventListener('click', async () => {
            select.disabled = true;
            try {
              const selected = await fetch('/api/music/share/' + id, {cache:'no-store'});
              if (!selected.ok) throw new Error('歌曲资料暂时没有读到，请稍后再试。');
              const data = await selected.json();
              if (token !== sequence || !dialog.open) return;
              if (!options.isCurrent()) throw new Error('聊天已切换，请在当前聊天里重新选歌。');
              options.onSelect(data.song); close();
            } catch (error) { status.textContent = error.message; }
            finally { select.disabled = false; }
          });
          row.append(label,select); results.append(row);
        }
      } catch (error) { if (token === sequence && error.name !== 'AbortError') status.textContent = error.message; }
    };
    if (!dialog.open) dialog.showModal(); input.focus();
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
