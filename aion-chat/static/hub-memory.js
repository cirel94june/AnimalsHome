// Hub 记忆：选一位 AI，看 TA 在 Memory Hub 里的记忆；可以改、归档，已归档的可以彻底删除（要确认两次）。
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

  function meta(m) {
    return [
      m.category, m.subject_name ? `关于 ${m.subject_name}` : '',
      m.layer === 'private' ? '私人' : m.layer === 'shared' ? '共享' : '',
      m.importance ? `重要度 ${m.importance}` : '', when(m.event_date || m.created_at),
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
    $('list').replaceChildren(...state.items.map(item));
  }

  function item(m) {
    const li = el('li', { className: 'hm-item' });
    if (state.editing === m.id) {
      const text = el('textarea', { value: m.content || '' });
      text.setAttribute('aria-label', '记忆内容');
      const imp = el('select');
      for (let i = 1; i <= 10; i++) imp.append(el('option', { value: String(i), textContent: String(i) }));
      imp.value = String(m.importance || 5);
      const save = el('button', { type: 'button', textContent: '保存' });
      save.onclick = async () => {
        save.disabled = true;
        try {
          await api('PUT', `/api/hub-memory/${encodeURIComponent(m.id)}`, { content: text.value, importance: Number(imp.value) });
          state.editing = null; toast('改好了'); load();
        } catch (e) { save.disabled = false; toast(e.message); }
      };
      const cancel = el('button', { type: 'button', textContent: '取消', onclick: () => { state.editing = null; render(); } });
      li.append(el('div', { className: 'hm-edit' }, [text, el('label', {}, ['重要度', imp])]),
        el('div', { className: 'hm-actions' }, [save, cancel]));
      return li;
    }
    li.append(el('p', { className: 'hm-content', textContent: m.content || '（空）' }), el('div', { className: 'hm-meta', textContent: meta(m) }));
    const actions = [];
    if (state.status === 'active') {
      actions.push(el('button', { type: 'button', textContent: '改', onclick: () => { state.editing = m.id; render(); } }));
      actions.push(el('button', { type: 'button', textContent: '归档', onclick: async () => {
        try { await api('POST', `/api/hub-memory/${encodeURIComponent(m.id)}/archive`); toast('已归档，可以在「已归档」里找到'); load(); }
        catch (e) { toast(e.message); }
      } }));
    } else {
      actions.push(el('button', { type: 'button', className: 'danger', textContent: '彻底删除', onclick: async () => {
        if (!confirm('彻底删除后找不回来了，确定吗？')) return;
        if (!confirm('再确认一次：真的彻底删除这条记忆？')) return;
        try { await api('DELETE', `/api/hub-memory/${encodeURIComponent(m.id)}?confirm=delete`); toast('已彻底删除'); load(); }
        catch (e) { toast(e.message); }
      } }));
    }
    li.append(el('div', { className: 'hm-actions' }, actions));
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
