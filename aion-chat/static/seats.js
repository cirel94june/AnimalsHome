// 家里的六个位置（小克、Lucien、座位 3～6）。还没入住的座位也列出来，标成 placeholder，
// 页面把它们画成灰色的空位、不能点（小猫 2026-10-24：给后面三个 AI 留好位置）。
(function () {
  const ORDER = ['aion', 'connor', 'ai3', 'ai4', 'ai5', 'ai6'];
  let cache = null;

  async function load() {
    if (!cache) {
      cache = fetch('/api/actors').then(res => res.json()).then(data => {
        const byId = Object.fromEntries((data.actors || []).map(a => [a.id, a]));
        return ORDER.map((id, index) => {
          const a = byId[id] || { id, name: `空位 ${index + 1}` };
          const open = !!(a.builtin || a.enabled);
          return {
            id, name: a.name || `空位 ${index + 1}`, avatar: a.avatar || '', color: a.color || '',
            open, placeholder: !open,
          };
        });
      }).catch(error => { cache = null; throw error; });
    }
    return cache;
  }

  async function names() {
    return Object.fromEntries((await load()).map(seat => [seat.id, seat.name]));
  }

  window.AionSeats = { ORDER, load, names };
})();
