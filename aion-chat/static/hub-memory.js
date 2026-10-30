// Hub 记忆：选一位 AI，看 TA 在 Memory Hub 里的记忆；可以改、归档，已归档的可以找回或彻底删除（删除要确认两次）。
// 所有请求都走 AionsHome 服务器（/api/hub-memory），Hub 的密钥不经过浏览器。
(function () {
  const $ = id => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const state = { actor: params.get('ai') || 'aion', status: 'active', page: 1, perPage: 20, total: 0, items: [], editing: null };
  let request = 0;

  async function api(method, url, body) {
    const res = await fetch(url, {
      method, headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败（${res.status}）`);
    return data;
  }

  function toast(text) {
    const el = $('toast');
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { el.hidden = true; }, 2600);
  }

  function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    Object.assign(node, props);
    for (const child of children) node.append(child);
    return node;
  }

  function when(value) {
    if (!value) return '';
    const d = typeof value === 'number' ? new Date(value * (value < 1e12 ? 1000 : 1)) : new Date(value);
    return Number.isNaN(d.getTime()) ? String(value).slice(0, 10) : `${d.getFullYear()}.${d.getMonth() + 1}.${d.getDate()}`;
  }

  // Hub 的重要度是 0～1；页面上显示成 1～10，保存时再除以 10
  const shownImportance = v => (typeof v === 'number' && v > 0) ? Math.min(10, Math.max(1, Math.round(v * 10))) : null;

  function meta(m) {
    const imp = shownImportance(m.importance);
    return [
      m.category, m.subject_name ? `关于 ${m.subject_name}` : '',
      m.layer === 'private' ? '私人' : m.layer === 'shared' ? '共享' : '',
      imp ? `重要度 ${imp}/10` : '', when(m.event_date || m.created_at),
    ].filter(Boolean).join(' · ');
  }

  // ── 人 ──
  async function renderPeople() {
    let seats;
    try { seats = await window.AionSeats.load(); } catch (e) { seats = [{ id: 'aion', name: '小克', open: true }]; }
    if (!seats.some(s => s.id === state.actor && s.open)) state.actor = 'aion';
    $('people').replaceChildren(...seats.map(seat => {
      const btn = el('button', { type: 'button', className: 'hm-person' + (seat.open ? '' : ' placeholder'), textContent: seat.name });
      btn.setAttribute('aria-pressed', String(seat.id === state.actor));
      if (seat.open) btn.onclick = () => { state.actor = seat.id; state.page = 1; renderPeopleState(); load(); };
      else { btn.disabled = true; btn.title = '这个位置还空着'; }
      btn.dataset.actor = seat.id;
      return btn;
    }));
  }

  function renderPeopleState() {
    document.querySelectorAll('.hm-person').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.actor === state.actor)));
    document.querySelectorAll('.hm-tab').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.status === state.status)));
  }

  // ── 列表 ──
  async function load() {
    const id = ++request;
    $('status').textContent = '正在读取…';
    $('list').replaceChildren();
    try {
      const data = await api('GET', `/api/hub-memory/list?actor=${encodeURIComponent(state.actor)}&status=${state.status}&page=${state.page}&per_page=${state.perPage}`);
      if (id !== request) return;
      state.items = data.items || [];
      state.total = data.total || 0;
      $('status').textContent = state.items.length ? '' : (state.status === 'active' ? '这里还没有记忆' : '没有归档的记忆');
    } catch (e) {
      if (id !== request) return;
      state.items = []; state.total = 0;
      $('status').textContent = e.message;
    }
    render();
  }

  function render() {
    const pages = Math.max(1, Math.ceil(state.total / state.perPage));
    $('count').textContent = `${state.total} 条`;
    $('pageLabel').textContent = `${state.page} / ${pages}`;
    $('prev').disabled = state.page <= 1;
    $('next').disabled = state.page >= pages;
    $('list').replaceChildren(...stacked(state.items).map(({ m, extras, orphan }) => item(m, extras, orphan)));
  }

  // Hub 把同一件事的补充说法挂在原来那条名下（fact_ref = 原来那条的 id，最多 4 层）。
  // 顺着往上找到本页里最上面那条，收在它下面；指向的那条不在本页 / 已归档，就单独显示并标「补充」。
  function stacked(items) {
    const byId = new Map(items.map(m => [m.id, m]));
    const rootOf = m => {
      let cur = m;
      for (let i = 0; i < 4 && cur.fact_ref && byId.has(cur.fact_ref) && cur.fact_ref !== m.id; i++) cur = byId.get(cur.fact_ref);
      return cur;
    };
    const tops = new Map();
    for (const m of items) if (rootOf(m) === m) tops.set(m.id, { m, extras: [], orphan: !!m.fact_ref });
    const out = [];
    for (const m of items) {
      const top = tops.get(m.id) || tops.get(rootOf(m).id);
      if (top && top.m === m) out.push(top);
      else if (top) top.extras.push(m);
      else out.push({ m, extras: [], orphan: true });  // 互相指（环）：各自单独显示
    }
    return out;
  }

  function item(m, extras = [], orphan = false, nested = false) {
    const li = el(nested ? 'div' : 'li', { className: nested ? 'hm-item hm-extra' : 'hm-item' });
    if (state.editing === m.id) {
      const text = el('textarea', { value: m.content || '' });
      text.setAttribute('aria-label', '记忆内容');
      const imp = el('select');
      imp.append(el('option', { value: '', textContent: '不改' }));  // 默认不动原来的重要度
      for (let i = 1; i <= 10; i++) imp.append(el('option', { value: String(i), textContent: String(i) }));
      const current = shownImportance(m.importance);
      imp.value = '';
      if (current) imp.options[0].textContent = `不改（现在 ${current}）`;
      const save = el('button', { type: 'button', textContent: '保存' });
      save.onclick = async () => {
        save.disabled = true;
        const body = {};
        if (text.value.trim() !== (m.content || '').trim()) body.content = text.value;
        if (imp.value) body.importance = Number(imp.value) / 10;
        if (!Object.keys(body).length) { state.editing = null; render(); return; }
        try {
          await api('PUT', `/api/hub-memory/${encodeURIComponent(m.id)}`, body);
          state.editing = null; toast('改好了'); load();
        } catch (e) { save.disabled = false; toast(e.message); }
      };
      const cancel = el('button', { type: 'button', textContent: '取消', onclick: () => { state.editing = null; render(); } });
      li.append(el('div', { className: 'hm-edit' }, [text, el('label', {}, ['重要度', imp])]),
        el('div', { className: 'hm-actions' }, [save, cancel]));
      return li;
    }
    const content = el('p', { className: 'hm-content', textContent: m.content || '（空）' });
    if (orphan && !nested) content.prepend(el('span', { className: 'hm-tag', textContent: '补充' }));
    li.append(content, el('div', { className: 'hm-meta', textContent: meta(m) }));
    const actions = [];
    if (state.status === 'active') {
      actions.push(el('button', { type: 'button', textContent: '改', onclick: () => { state.editing = m.id; render(); } }));
      actions.push(el('button', { type: 'button', textContent: '归档', onclick: async () => {
        try { await api('POST', `/api/hub-memory/${encodeURIComponent(m.id)}/archive`); toast('已归档，可以在「已归档」里找到'); load(); }
        catch (e) { toast(e.message); }
      } }));
    } else {
      actions.push(el('button', { type: 'button', textContent: '找回', onclick: async () => {
        try { await api('POST', `/api/hub-memory/${encodeURIComponent(m.id)}/unarchive`); toast('找回来了，在「正在用」里'); load(); }
        catch (e) { toast(e.message); }
      } }));
      actions.push(el('button', { type: 'button', className: 'danger', textContent: '彻底删除', onclick: async () => {
        if (!confirm('彻底删除后找不回来了，确定吗？')) return;
        if (!confirm('再确认一次：真的彻底删除这条记忆？')) return;
        try { await api('DELETE', `/api/hub-memory/${encodeURIComponent(m.id)}?confirm=delete`); toast('已彻底删除'); load(); }
        catch (e) { toast(e.message); }
      } }));
    }
    li.append(el('div', { className: 'hm-actions' }, actions));
    if (extras.length) {
      li.append(el('div', { className: 'hm-same' }, [
        el('div', { className: 'hm-same-label', textContent: '同一件事还记着：' }),
        ...extras.map(x => item(x, [], false, true)),
      ]));
    }
    return li;
  }

  document.querySelectorAll('.hm-tab').forEach(btn => {
    btn.onclick = () => { state.status = btn.dataset.status; state.page = 1; state.editing = null; renderPeopleState(); load(); };
  });
  $('prev').onclick = () => { if (state.page > 1) { state.page--; load(); } };
  $('next').onclick = () => { state.page++; load(); };
  $('backBtn').onclick = () => {
    if (window.parent !== window && typeof window.parent.openSubPage === 'function') window.parent.openSubPage('/memory');
    else location.href = '/memory';
  };
  renderPeople().then(() => { renderPeopleState(); load(); });
})();
