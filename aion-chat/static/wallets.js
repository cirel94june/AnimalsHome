(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const money = value => Number(value).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  let owners = [], selected = '', userName = '你', generation = 0, ledgerGeneration = 0, offset = 0;
  const api = actor => actor === 'aion' ? '/api/wallet' : actor === 'connor' ? '/api/connor-wallet' : `/api/seat-wallet/${encodeURIComponent(actor)}`;
  async function read(url) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(url, {cache: 'no-store', signal: controller.signal});
      if (!response.ok) throw new Error('读取失败');
      return await response.json();
    } finally { clearTimeout(timer); }
  }
  function node(tag, cls, text) {
    const element = document.createElement(tag);
    if (cls) element.className = cls;
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function renderOwners() {
    $('wallets').replaceChildren(...owners.map(owner => {
      const card = node('button', 'wallet'); card.type = 'button';
      card.setAttribute('aria-pressed', String(owner.id === selected));
      card.style.setProperty('--accent', /^#[0-9a-f]{6}$/i.test(owner.color || '') ? owner.color : '#7598c6');
      const head = node('div', 'owner');
      if (/^\/(?!\/)|^https:\/\//.test(owner.avatar || '')) {
        const img = node('img'); img.src = owner.avatar; img.alt = ''; head.append(img);
      }
      head.append(node('span', '', `${owner.name}的口袋`));
      card.append(head, node('span', 'balance', owner.balance === undefined ? '—' : `¥${money(owner.balance)}`),
        node('small', '', owner.error ? '暂时读不到，点刷新重试' : '各自一本账 · 点开看明细'));
      card.onclick = () => { selected = owner.id; renderOwners(); loadLedger(); };
      return card;
    }));
  }
  async function loadLedger(append = false) {
    const owner = owners.find(item => item.id === selected);
    if (!owner) return;
    const current = ++ledgerGeneration;
    const nextOffset = append ? offset : 0;
    $('ledger').hidden = false;
    $('ledger-title').textContent = `${owner.name}的小钱包`;
    $('more').hidden = true;
    if (!append) { $('transactions').replaceChildren(node('div', 'empty', '正在翻账本……')); $('stamp').textContent = ''; }
    try {
      const rows = await read(`${api(owner.id)}/transactions?limit=31&offset=${nextOffset}`);
      if (current !== ledgerGeneration) return;
      if (!Array.isArray(rows)) throw new Error('账本格式错误');
      if (!append) $('transactions').replaceChildren();
      const visible = rows.slice(0, 30);
      for (const tx of visible) {
        const amount = Number(tx.amount);
        if (!Number.isFinite(amount)) throw new Error('金额格式错误');
        const isAi = tx.record_type === 'wallet_ai' || tx.record_type === 'connor_wallet_ai' || tx.record_type === `seat_wallet_ai:${owner.id}`;
        const description = String(tx.description || '');
        let direction = amount < 0 ? `从${owner.name}的口袋支出` : `${owner.name}的钱包入账`;
        if (description.startsWith('系统纠错：')) direction = `误扣退回 → ${owner.name}`;
        else if (description.startsWith('斗地主阵营结算')) direction = amount < 0 ? '牌局结算 · 支出' : '牌局结算 · 收入';
        else if (!isAi && amount > 0) direction = `${userName} → ${owner.name}`;
        else if (isAi && amount < 0 && /转(?:账)?给/.test(description)) direction = `${owner.name} → ${userName}`;
        const row = node('div', 'tx'), info = node('div', 'tx-info');
        info.append(node('div', 'direction', direction));
        if (tx.description) info.append(node('div', 'description', tx.description));
        info.append(node('div', 'time', new Date(tx.created_at * 1000).toLocaleString('zh-CN', {hour12: false})));
        row.append(info, node('div', amount < 0 ? 'amount out' : 'amount', `${amount >= 0 ? '+' : '−'}${money(Math.abs(amount))}`));
        $('transactions').append(row);
      }
      if (!append && !visible.length) $('transactions').append(node('div', 'empty', '还没有收支记录。第一份小心意，会记在这里。'));
      offset = nextOffset + visible.length;
      $('more').hidden = rows.length <= 30;
      $('stamp').textContent = `刚刚更新 · 只看${owner.name}的账`;
    } catch (_) {
      if (current !== ledgerGeneration) return;
      if (!append) $('transactions').replaceChildren(node('div', 'empty', '账本暂时没打开，点上面的“刷新”再试。'));
      else { $('stamp').textContent = '后面的记录没读到，可以再试一次。'; $('more').hidden = false; }
    }
  }
  async function refresh() {
    const current = ++generation;
    ++ledgerGeneration;
    $('status').textContent = '正在更新小钱包……';
    try {
      const data = await read('/api/actors');
      const active = data.actors.filter(owner => owner.enabled);
      const balances = await Promise.allSettled(active.map(owner => read(`${api(owner.id)}/balance`)));
      if (current !== generation) return;
      userName = data.user?.name || '你';
      owners = active.map((owner, index) => {
        const result = balances[index];
        const balance = result.status === 'fulfilled' ? result.value.balance : undefined;
        return {...owner, balance: typeof balance === 'number' && Number.isFinite(balance) ? balance : undefined,
          error: typeof balance !== 'number' || !Number.isFinite(balance)};
      });
      if (!owners.some(owner => owner.id === selected)) selected = owners[0]?.id || '';
      renderOwners();
      $('status').textContent = owners.some(owner => owner.error) ? '有的钱包暂时没读到；其他人的仍可查看。' : owners.length ? '' : '还没有入住的家人。';
      $('ledger').hidden = !selected;
      if (selected) await loadLedger();
    } catch (_) {
      if (current === generation) $('status').textContent = '这次没连上，点“刷新”再试。已有内容是上次读到的记录。';
    }
  }
  $('refresh').onclick = refresh;
  $('more').onclick = () => loadLedger(true);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  setInterval(() => { if (!document.hidden) refresh(); }, 60000);
  refresh();
})();
