(function () {
  'use strict';
  const input = document.getElementById('aiMusicEnabled');
  const status = document.getElementById('aiMusicStatus');
  let saving = false, version = 0;
  function show(item) {
    if (!item || item.key !== 'music' || typeof item.enabled !== 'boolean') throw new Error('点歌开关暂时无法读取');
    input.checked = item.enabled;
    status.textContent = item.enabled ? '已允许：他们想分享音乐时可以自行点歌。' : '已关闭：AI 不会再点新歌，你仍可手动听歌。';
  }
  async function request(url, options) {
    const response = await fetch(url, {cache: 'no-store', ...options});
    if (!response.ok) throw new Error('点歌开关未保存，请稍后重试');
    return response.json();
  }
  async function refresh() {
    if (saving) return;
    const token = ++version;
    try {
      const data = await request('/api/capabilities');
      if (token !== version || saving) return;
      show((data.groups || []).flatMap(group => group.items || []).find(item => item.key === 'music'));
      input.disabled = false;
    } catch (_) { if (token === version) { input.disabled = true; status.textContent = '暂时无法读取开关，重新打开点歌台可再试。'; } }
  }
  input.addEventListener('change', async () => {
    const previous = !input.checked;
    saving = true; ++version; input.disabled = true; status.textContent = '正在保存…';
    try {
      const data = await request('/api/capabilities/music', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled: input.checked})});
      show(data.capability);
    } catch (_) {
      input.checked = previous;
      status.textContent = '保存结果尚未确认，正在重新读取…';
    } finally { saving = false; await refresh(); }
  });
  window.addEventListener('focus', refresh);
  window.addEventListener('pageshow', refresh);
  window.addEventListener('music-station-visible', refresh);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  refresh();
})();
