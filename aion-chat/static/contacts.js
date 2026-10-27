// 统一聊天入口：每位 AI 一张卡（头像、名字、TA 名下的窗口），点开选窗口或开新窗口；下面是群聊。
// 小克的窗口在小克私聊页打开（应用外壳本身），Lucien、座位 3～6 和群聊在聊天室里打开。
(function () {
  const $ = id => document.getElementById(id);
  const OPEN_KEY = 'aion_contacts_open';
  let openPeople = new Set();
  try { openPeople = new Set(JSON.parse(localStorage.getItem(OPEN_KEY) || '[]')); } catch (e) { /* 记不住展开状态也能用 */ }

  async function api(method, url, body) {
    const res = await fetch(url, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw new Error(`${res.status}`);
    return res.json();
  }

  function toast(text) {
    const el = $('toast');
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { el.hidden = true; }, 2600);
  }

  function when(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    if (d.toDateString() === now.toDateString()) return `今天 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    return `${d.getMonth() + 1}月${d.getDate()}日`;
  }

  function today() {
    const d = new Date();
    return `${d.getMonth() + 1}月${d.getDate()}日`;
  }

  function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    Object.assign(node, props);
    for (const child of children) node.append(child);
    return node;
  }

  // ── 打开窗口 ──
  function openUrl(url) {
    if (window.parent !== window && typeof window.parent.openSubPage === 'function') window.parent.openSubPage(url);
    else location.href = url;
  }

  function openAion(convId) {
    // 小克的私聊就是应用外壳本身：关掉这一页，在外壳里切到这个窗口（或开新窗口）
    if (window.parent !== window && typeof window.parent.openAionConversation === 'function') {
      window.parent.openAionConversation(convId || null);
      return;
    }
    location.href = convId ? `/chat?conv=${encodeURIComponent(convId)}` : '/chat?new=1';
  }

  function openWindow(person, win) {
    if (person && person.id === 'aion') openAion(win.id);
    else openUrl(`/chatroom?room=${encodeURIComponent(win.id)}`);
  }

  async function newWindow(person) {
    if (person.id === 'aion') { openAion(null); return; }
    try {
      const body = person.id === 'connor'
        ? { type: 'connor_1v1', title: `和 ${person.name} · ${today()}` }
        : { type: 'seat_1v1', actor: person.id, title: `和 ${person.name} · ${today()}`, new_window: true };
      const room = await api('POST', '/api/chatroom/rooms', body);
      openUrl(`/chatroom?room=${encodeURIComponent(room.id)}`);
    } catch (e) {
      toast('没能开新窗口，稍后再试');
    }
  }

  async function newGroup() {
    try {
      const room = await api('POST', '/api/chatroom/rooms', { type: 'group', title: `群聊 · ${today()}` });
      openUrl(`/chatroom?room=${encodeURIComponent(room.id)}`);
    } catch (e) {
      toast('没能建新群聊，稍后再试');
    }
  }

  async function rename(person, win) {
    const title = (prompt('给这个窗口起个名字', win.title || '') || '').trim();
    if (!title || title === win.title) return;
    try {
      if (person && person.id === 'aion') await api('PUT', `/api/conversations/${encodeURIComponent(win.id)}`, { title });
      else await api('PUT', `/api/chatroom/rooms/${encodeURIComponent(win.id)}`, { title });
      await load();
    } catch (e) {
      toast('改名没成功，稍后再试');
    }
  }

  // ── 画页面 ──
  function windowItem(person, win) {
    const meta = [when(win.updated_at), win.message_count ? `${win.message_count} 条` : ''].filter(Boolean).join(' · ');
    const openBtn = el('button', { type: 'button', className: 'window-open', onclick: () => openWindow(person, win) }, [
      el('span', { className: 'window-title', textContent: win.title || '未命名窗口' }),
      el('span', { className: 'window-meta', textContent: meta }),
    ]);
    const renameBtn = el('button', {
      type: 'button', className: 'window-rename', textContent: '✎', title: '改名',
      onclick: () => rename(person, win),
    });
    renameBtn.setAttribute('aria-label', `给「${win.title || '未命名窗口'}」改名`);
    return el('li', { className: 'window' }, [openBtn, renameBtn]);
  }

  function personCard(person, windows) {
    const card = el('li', { className: 'person' + (openPeople.has(person.id) ? ' open' : '') });
    card.style.setProperty('--person-color', person.color || 'var(--accent)');
    const latest = windows[0];
    const sub = windows.length
      ? `${windows.length} 个窗口 · 最近：${latest.title || '未命名'}${latest.updated_at ? ' · ' + when(latest.updated_at) : ''}`
      : '还没有聊过，点开开一个窗口';
    const head = el('button', { type: 'button', className: 'person-head' }, [
      el('img', { src: person.avatar || '/public/AIIcon.png', alt: '' }),
      el('span', { className: 'person-info' }, [
        el('span', { className: 'person-name', textContent: person.name }),
        el('span', { className: 'person-sub', textContent: sub }),
      ]),
      el('span', { className: 'chevron', textContent: '›' }),
    ]);
    head.setAttribute('aria-expanded', String(openPeople.has(person.id)));
    head.onclick = () => {
      const open = card.classList.toggle('open');
      head.setAttribute('aria-expanded', String(open));
      if (open) openPeople.add(person.id); else openPeople.delete(person.id);
      try { localStorage.setItem(OPEN_KEY, JSON.stringify([...openPeople])); } catch (e) { /* 忽略 */ }
    };
    const list = el('ul', { className: 'windows' }, windows.map(win => windowItem(person, win)));
    const add = el('button', { type: 'button', className: 'add-btn', textContent: `＋ 和 ${person.name} 开新窗口`,
      onclick: () => newWindow(person) });
    card.append(head, el('div', { className: 'person-body' }, [list, add]));
    return card;
  }

  async function load() {
    const [actorData, conversations, rooms] = await Promise.all([
      api('GET', '/api/actors'),
      api('GET', '/api/conversations').catch(() => []),
      api('GET', '/api/chatroom/rooms').catch(() => []),
    ]);
    const byRecent = (a, b) => (b.updated_at || 0) - (a.updated_at || 0);
    const homeRooms = (rooms || []).filter(r => !String(r.id).startsWith('billiards_'));
    const all = actorData.actors || [];
    const people = all.filter(a => a.enabled || a.builtin);
    const emptySeats = all.filter(a => !(a.enabled || a.builtin));
    const windowsOf = person => {
      if (person.id === 'aion') return [...(conversations || [])].sort(byRecent);
      if (person.id === 'connor') return homeRooms.filter(r => r.type === 'connor_1v1').sort(byRecent);
      return homeRooms.filter(r => r.type === 'seat_1v1' && r.actor === person.id).sort(byRecent);
    };
    $('people').replaceChildren(...people.map(p => personCard(p, windowsOf(p))),
      ...emptySeats.map(seat => {
        // 还没入住的座位：灰色空位，点不了
        const card = el('li', { className: 'person placeholder' });
        card.setAttribute('aria-disabled', 'true');
        card.append(el('div', { className: 'person-head' }, [
          el('span', { className: 'empty-avatar', textContent: '＋' }),
          el('span', { className: 'person-info' }, [
            el('span', { className: 'person-name', textContent: seat.name || '空位' }),
            el('span', { className: 'person-sub', textContent: '还空着' }),
          ]),
        ]));
        return card;
      }));
    const groups = homeRooms.filter(r => r.type === 'group').sort(byRecent);
    $('groups').replaceChildren(...(groups.length
      ? groups.map(win => windowItem(null, win))
      : [el('li', { className: 'empty', textContent: '还没有群聊' })]));
  }

  $('backBtn').onclick = () => {
    if (window.parent !== window && typeof window.parent.openSubPage === 'function') window.parent.openSubPage('/');
    else location.href = '/';
  };
  $('newGroup').onclick = newGroup;
  load().catch(() => {
    $('people').replaceChildren(el('li', { className: 'empty', textContent: '加载失败，下拉或稍后重新打开试试' }));
  });
  // 从别的页面回来时刷新（新消息、新窗口）
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load().catch(() => {}); });
})();
