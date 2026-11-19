(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const base = '/api/music-station/account';
  let account = null, handle = '', timer = 0, serial = 0, viewSerial = 0;
  let view = null, busy = false;
  const actions = new Map();
  let accountKey = '';
  let personalProfiles = [], ownedPlaylists = [];
  async function api(path = '', method = 'GET', data) {
    const response = await fetch(base + path, { method, cache: 'no-store', headers: {
      'X-Music-Station': '1', ...(data === undefined ? {} : {'Content-Type': 'application/json'})
    }, ...(data === undefined ? {} : {body: JSON.stringify(data)}) });
    const value = await response.json();
    if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : '网易云请求未完成');
    return value;
  }
  const message = text => {
    $('cloudStatus').textContent = text || '';
    $('accountActionStatus').textContent = text || '';
  };
  const updated = () => window.dispatchEvent(new Event('music-station-updated'));
  function showAccount(value) {
    const nextKey = `${value.account.user_id}:${value.generation}:${value.logged_in}`;
    if (nextKey !== accountKey) {
      accountKey = nextKey; ++viewSerial; view = null; actions.clear();
      personalProfiles = []; ownedPlaylists = [];
      $('cloudResults').replaceChildren(); $('cloudWriteTarget').replaceChildren();
      $('importCloudButton').hidden = true; $('cloudMoreButton').hidden = true;
      $('cloudPreviewAudio').pause(); message('');
    }
    account = value;
    $('cloudAccountSummary').textContent = value.logged_in ? `网易云 · ${value.account.nickname}` : '未登录网易云。可点“账号与权限”扫码，登录后浏览自己的云歌单。';
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
    if (!account?.logged_in) { await refreshPersonal(owner); return; }
    const data = await api('/library?kind=playlists');
    if (owner !== accountKey) return;
    ownedPlaylists = data.playlists.filter(p => p.owned && !p.liked);
    for (const item of data.playlists.filter(p => p.owned && !p.liked)) {
      $('cloudWriteTarget').add(new Option(item.name, item.id));
    }
    if ([...$('cloudWriteTarget').options].some(option => option.value === selected)) $('cloudWriteTarget').value = selected;
    await refreshPersonal(owner);
  }
  async function refreshPersonal(owner = accountKey) {
    try {
      const data = await api('/personal');
      if (owner !== accountKey) return;
      personalProfiles = data.profiles || [];
      $('personalPlaylistBindings').replaceChildren();
      for (const profile of personalProfiles) {
        const label = document.createElement('label'); const title = document.createElement('span');
        title.textContent = profile.name;
        const select = document.createElement('select'); select.setAttribute('aria-label', `${profile.name}的网易云歌单`);
        select.add(new Option('自动找同名歌单，没有则新建', ''));
        for (const p of ownedPlaylists) select.add(new Option(p.name, p.id));
        select.value = profile.cloud_playlist_id || '';
        select.disabled = !account?.logged_in;
        select.addEventListener('change', async () => {
          select.disabled = true;
          try {
            const result = await api(`/personal/${encodeURIComponent(profile.actor)}`, 'PUT', {playlist_id: select.value, account_generation: account.generation});
            showAccount(result.account); await refreshTargets(); message('这位 AI 的网易云歌单已设置。');
          } catch (error) { message(error.message); select.value = profile.cloud_playlist_id || ''; }
          finally { select.disabled = !account?.logged_in; }
        });
        label.append(title, select); $('personalPlaylistBindings').append(label);
        if (profile.status?.message) { const status = document.createElement('p'); status.textContent = profile.status.message; $('personalPlaylistBindings').append(status); }
      }
    } catch (_) { $('personalPlaylistBindings').textContent = '暂时无法读取个人歌单设置，稍后重开点歌台可再试。'; }
  }
  async function showCloud() {
    $('localMusicLibrary').hidden = true; $('cloudMusicLibrary').hidden = false;
    $('localLibraryButton').setAttribute('aria-pressed', 'false'); $('cloudLibraryButton').setAttribute('aria-pressed', 'true');
    if (window.matchMedia('(max-width: 850px), (max-height: 700px)').matches) {
      $('dropZone').scrollTop = Math.max(0, $('cloudLibraryButton').parentElement.offsetTop - 6);
    }
    try { showAccount(await api()); await refreshTargets(); await load('personal'); }
    catch (error) { message(error.message); }
  }
  $('localLibraryButton').addEventListener('click', () => {
    ++viewSerial; $('cloudMusicLibrary').hidden = true; $('localMusicLibrary').hidden = false;
    $('localLibraryButton').setAttribute('aria-pressed', 'true'); $('cloudLibraryButton').setAttribute('aria-pressed', 'false');
    $('cloudPreviewAudio').pause();
  });
  $('cloudLibraryButton').addEventListener('click', showCloud);
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
      const listKind = view.kind === 'charts' ? 'chart' : 'playlist';
      const node = button(`${item.name}${item.track_count === undefined ? '' : ` · ${item.track_count}首`}`,
        () => load(listKind, item.id, 0, item.name));
      node.className = 'cloud-playlist-row'; $('cloudResults').append(node);
    }
    for (const song of data.songs || []) {
      const row = document.createElement('div'); row.className = 'cloud-song-row';
      const label = document.createElement('span'); label.textContent = `${song.name} · ${song.artist}`;
      const spec = {id: song.id, name: song.name, artist: song.artist, album: song.album};
      const cover = document.createElement('span'); cover.className = 'netease-cover';
      MusicShare.setCover(cover, song); row.append(cover);
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
        if (!id) { $('cloudPlaylistTools').open = true; $('cloudWriteTarget').focus(); throw new Error('先选择一张你自己创建的网易云歌单，再点一次“加入云歌单”。'); }
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
      const data = await api(`/library?${new URLSearchParams({kind: kind === 'personal' ? 'playlists' : kind, ref, offset})}`);
      if (token !== viewSerial) return;
      if (kind === 'personal') {
        data.playlists = (data.playlists || []).filter(p => personalProfiles.some(profile => profile.cloud_playlist_id ? profile.cloud_playlist_id === p.id : profile.playlist_name === p.name));
        if (!data.playlists.length) message('还没有个人云歌单。允许 AI 点歌并开启网易云歌单写入后，他们第一次收歌时会自动整理。');
      }
      document.querySelectorAll('#cloudTabs [data-kind]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.kind === kind)));
      render(data, offset > 0); message(data.name || name || (data.songs?.length === 0 ? '这份列表暂时没有歌曲。' : ''));
      if (kind === 'personal' && !data.playlists?.length) message('还没有个人云歌单。允许 AI 点歌并开启网易云歌单写入后，他们第一次收歌时会自动整理。');
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
    try { showAccount(await api(`/qr/${encodeURIComponent(handle)}/confirm`, 'POST')); await cancelQr(); $('neteaseAccountDialog').close(); await showCloud(); }
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
        if ($('cloudMusicLibrary').hidden) break;
      } while (offset != null && offset <= 10000);
      updated();
    } catch (error) { message(error.message + ' 已保存的歌曲会保留，再次导入会去重。'); updated(); }
    finally { busy = false; $('importCloudButton').disabled = false; }
  });
  $('cloudPreviewAudio').addEventListener('error', () => message('这首歌暂时不能播放，可能需要会员或官方客户端授权。'));
  $('stationAudio').addEventListener('play', () => $('cloudPreviewAudio').pause());
  $('cloudSearchForm').addEventListener('submit', async event => {
    event.preventDefault(); const query = $('cloudSearchInput').value.trim(); if (!query) return;
    const token = ++viewSerial; const owner = accountKey; view = {kind: 'search', ref: '', next: null}; message('正在搜索网易云…');
    try {
      const response = await fetch(`/api/music-station/netease/search?${new URLSearchParams({q: query, limit: 20})}`, {cache: 'no-store'});
      const data = await response.json(); if (!response.ok) throw new Error('网易云搜索暂时没有完成，请稍后重试。');
      if (token !== viewSerial || owner !== accountKey) return;
      render(data, false); $('importCloudButton').hidden = true; message(data.songs?.length ? '选择一首试听，或加入歌单。' : '没有找到这首歌，可以换个歌名或歌手试试。');
    } catch (error) { if (token === viewSerial) message(error.message); }
  });
  api().then(async data => { showAccount(data); await refreshTargets(); if (!data.logged_in) await refreshPersonal(); }).catch(() => {});
  window.addEventListener('music-station-visible', async () => {
    try {
      showAccount(await api()); await refreshTargets();
      if (!$('cloudMusicLibrary').hidden) await load('personal');
    } catch (error) { message(error.message); }
  });
})();
