(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const base = '/api/music-station/account';
  let account = null, handle = '', timer = 0, serial = 0, viewSerial = 0;
  let view = null, busy = false;
  const actions = new Map();
  let accountKey = '';
  async function api(path = '', method = 'GET', data) {
    const response = await fetch(base + path, { method, cache: 'no-store', headers: {
      'X-Music-Station': '1', ...(data === undefined ? {} : {'Content-Type': 'application/json'})
    }, ...(data === undefined ? {} : {body: JSON.stringify(data)}) });
    const value = await response.json();
    if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : '网易云请求未完成');
    return value;
  }
  const message = text => { $('cloudStatus').textContent = text || ''; };
  const updated = () => window.dispatchEvent(new Event('music-station-updated'));
  function showAccount(value) {
    const nextKey = `${value.account.user_id}:${value.generation}:${value.logged_in}`;
    if (nextKey !== accountKey) {
      accountKey = nextKey; ++viewSerial; view = null; actions.clear();
      $('cloudResults').replaceChildren(); $('cloudWriteTarget').replaceChildren();
      $('importCloudButton').hidden = true; $('cloudMoreButton').hidden = true;
      $('cloudPreviewAudio').pause(); message('');
    }
    account = value;
    $('accountStatus').textContent = value.logged_in ? `已登录：${value.account.nickname}（登录是否有效可点“检查登录”）` :
      value.cookie_configured ? '已有登录凭据，请点“检查登录”确认账号。' : '尚未登录，扫码后可读取你的网易云歌单。';
    document.querySelectorAll('[data-permission]').forEach(input => {
      input.checked = Boolean(value.permissions[input.dataset.permission]);
      input.disabled = input.dataset.permission !== 'local_playlists' && !value.logged_in;
    });
    $('logoutAccountButton').disabled = !value.cookie_configured;
  }
  async function refreshTargets() {
    const owner = accountKey;
    const selected = $('cloudWriteTarget').value;
    $('cloudWriteTarget').replaceChildren(new Option('选择自建网易云歌单', ''));
    if (!account?.logged_in) return;
    const data = await api('/library?kind=playlists');
    if (owner !== accountKey) return;
    for (const item of data.playlists.filter(p => p.owned && !p.liked)) {
      $('cloudWriteTarget').add(new Option(item.name, item.id));
    }
    if ([...$('cloudWriteTarget').options].some(option => option.value === selected)) $('cloudWriteTarget').value = selected;
  }
  async function cancelQr() {
    ++serial; clearTimeout(timer);
    const old = handle; handle = '';
    $('qrLoginPanel').hidden = true; $('confirmAccountButton').hidden = true;
    if (old) { try { await api(`/qr/${encodeURIComponent(old)}`, 'DELETE'); } catch (_) {} }
  }
  async function startQr() {
    await cancelQr(); const token = ++serial;
    $('qrLoginButton').disabled = true;
    try {
      const data = await api('/qr', 'POST');
      if (token !== serial || !$('neteaseAccountDialog').open) {
        await api(`/qr/${encodeURIComponent(data.handle)}`, 'DELETE'); return;
      }
      handle = data.handle; $('neteaseQr').replaceChildren();
      new QRCode($('neteaseQr'), {text: data.url, width: 220, height: 220, correctLevel: QRCode.CorrectLevel.M});
      $('qrLoginPanel').hidden = false; $('qrLoginStatus').textContent = '等待扫码…';
      const poll = async () => {
        if (token !== serial || !handle) return;
        try {
          const value = await api(`/qr/${encodeURIComponent(handle)}`);
          if (token !== serial) return;
          if (value.state === 'confirm') {
            $('qrLoginStatus').textContent = `手机已确认：${value.account.nickname}。请确认是否使用这个账号。`;
            $('confirmAccountButton').hidden = false; return;
          }
          if (value.state === 'expired') { $('qrLoginStatus').textContent = '二维码已到期，请重新扫码登录。'; return; }
          $('qrLoginStatus').textContent = value.state === 'scanned' ? '已扫码，请在网易云 App 确认。' : '等待扫码…';
          timer = setTimeout(poll, 2800);
        } catch (error) { if (token === serial) $('qrLoginStatus').textContent = error.message; }
      };
      timer = setTimeout(poll, 2800);
    } catch (error) { message(error.message); }
    finally { $('qrLoginButton').disabled = false; }
  }
  async function write(data) {
    const owner = accountKey;
    data = {...data, account_generation: account?.generation};
    // Keep the same operation id after transport failure; the server never replays it.
    const key = JSON.stringify(data);
    if (!actions.has(key)) actions.set(key, crypto.randomUUID());
    const result = await api('/action', 'POST', {...data, action_id: actions.get(key)});
    if (owner !== accountKey) return result;
    message(result.message);
    if (result.ok) { await refreshTargets(); updated(); }
    return result;
  }
  function button(text, fn) {
    const node = document.createElement('button'); node.type = 'button'; node.textContent = text;
    node.addEventListener('click', async () => {
      node.disabled = true;
      try { await fn(); } catch (error) { message(error.message); }
      finally { node.disabled = false; }
    });
    return node;
  }
  function render(data, append) {
    if (!append) $('cloudResults').replaceChildren();
    for (const item of data.playlists || []) {
      const node = button(`${item.name}${item.track_count === undefined ? '' : ` · ${item.track_count}首`}`,
        () => load(view.kind === 'charts' ? 'chart' : 'playlist', item.id, 0, item.name));
      node.className = 'cloud-playlist-row'; $('cloudResults').append(node);
    }
    for (const song of data.songs || []) {
      const row = document.createElement('div'); row.className = 'cloud-song-row';
      const label = document.createElement('span'); label.textContent = `${song.name} · ${song.artist}`;
      const spec = {id: song.id, name: song.name, artist: song.artist, album: song.album};
      row.append(label, button('试听', async () => {
        const audio = $('cloudPreviewAudio');
        $('stationAudio').pause(); $('neteasePreview').pause();
        audio.src = `/api/music/stream/${encodeURIComponent(song.id)}`;
        await audio.play();
      }), button('存到点歌台', async () => {
        await write({target: 'local', op: 'create', name: '网易云收藏', songs: [spec]});
      }), button('红心', () => write({target: 'netease', op: 'like', songs: [spec]})),
      button('加入云歌单', async () => {
        const id = $('cloudWriteTarget').value;
        if (!id) throw new Error('先选择一张你自己创建的网易云歌单。');
        await write({target: 'netease', op: 'add', playlist_id: id, songs: [spec]});
      }));
      $('cloudResults').append(row);
    }
    $('cloudMoreButton').hidden = data.next_offset == null;
    view.next = data.next_offset;
    $('importCloudButton').hidden = !data.songs?.length;
  }
  async function load(kind, ref = '', offset = 0, name = '') {
    if (busy) return;
    const token = ++viewSerial;
    view = {kind, ref, name, next: null}; message('正在读取网易云…');
    try {
      const data = await api(`/library?${new URLSearchParams({kind, ref, offset})}`);
      if (token !== viewSerial) return;
      render(data, offset > 0); message(data.name || name || (data.songs?.length === 0 ? '这份列表暂时没有歌曲。' : ''));
    } catch (error) { if (token === viewSerial) message(error.message); }
  }
  $('accountButton').addEventListener('click', async () => {
    $('neteaseAccountDialog').showModal();
    try { showAccount(await api()); await refreshTargets(); } catch (error) { message(error.message); }
  });
  $('closeAccountButton').addEventListener('click', () => $('neteaseAccountDialog').close());
  $('neteaseAccountDialog').addEventListener('close', () => { cancelQr(); $('cloudPreviewAudio').pause(); ++viewSerial; });
  $('qrLoginButton').addEventListener('click', startQr);
  $('cancelQrButton').addEventListener('click', cancelQr);
  $('confirmAccountButton').addEventListener('click', async () => {
    const node = $('confirmAccountButton'); node.disabled = true;
    try { showAccount(await api(`/qr/${encodeURIComponent(handle)}/confirm`, 'POST')); await cancelQr(); await refreshTargets(); await load('playlists'); }
    catch (error) { message(error.message); } finally { node.disabled = false; }
  });
  for (const [id, path] of [['checkAccountButton', '/check'], ['logoutAccountButton', '/logout']]) {
    $(id).addEventListener('click', async () => {
      $(id).disabled = true;
      try { await cancelQr(); showAccount(await api(path, 'POST')); $('cloudResults').replaceChildren(); await refreshTargets(); }
      catch (error) { message(error.message); } finally { $(id).disabled = false; }
    });
  }
  $('cookieLoginForm').addEventListener('submit', async event => {
    event.preventDefault(); const input = $('neteaseCookieInput'); const secret = input.value; input.value = '';
    try { await cancelQr(); showAccount(await api('/cookie', 'POST', {cookie: secret})); await refreshTargets(); }
    catch (error) { message(error.message); }
  });
  document.querySelectorAll('[data-permission]').forEach(input => input.addEventListener('change', async () => {
    input.disabled = true;
    try { showAccount(await api('/permissions', 'PUT', {[input.dataset.permission]: input.checked})); }
    catch (error) { input.checked = !input.checked; message(error.message); }
    finally { input.disabled = input.dataset.permission !== 'local_playlists' && !account?.logged_in; }
  }));
  $('cloudTabs').addEventListener('click', event => { if (event.target.dataset.kind) load(event.target.dataset.kind); });
  $('cloudMoreButton').addEventListener('click', () => { if (view?.next != null) load(view.kind, view.ref, view.next, view.name); });
  $('createCloudPlaylistButton').addEventListener('click', async () => {
    const name = $('cloudPlaylistName').value.trim();
    if (!name) { message('请先填写歌单名称。'); return; }
    $('createCloudPlaylistButton').disabled = true;
    try { await write({target: 'netease', op: 'create', name, songs: []}); await load('playlists'); }
    catch (error) { message(error.message); } finally { $('createCloudPlaylistButton').disabled = false; }
  });
  $('importCloudButton').addEventListener('click', async () => {
    if (busy || !view) return;
    const selected = {...view}, owner = accountKey, generation = account?.generation;
    busy = true; $('importCloudButton').disabled = true;
    try {
      let offset = 0, count = 0;
      do {
        if (owner !== accountKey) break;
        const value = await api('/import', 'POST', {kind: selected.kind, ref: selected.ref, offset, name: selected.name, account_generation: generation});
        if (owner !== accountKey) break;
        count += value.added; offset = value.next_offset;
        message(`已存入点歌台：新增 ${count} 首。`);
        if (!$('neteaseAccountDialog').open) break;
      } while (offset != null && offset <= 10000);
      updated();
    } catch (error) { message(error.message + ' 已保存的歌曲会保留，再次导入会去重。'); updated(); }
    finally { busy = false; $('importCloudButton').disabled = false; }
  });
  $('cloudPreviewAudio').addEventListener('error', () => message('这首歌暂时不能播放，可能需要会员或官方客户端授权。'));
  $('stationAudio').addEventListener('play', () => $('cloudPreviewAudio').pause());
})();
