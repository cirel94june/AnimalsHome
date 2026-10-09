/*
 * IB 桌面挂件：Hero（日期 + 大时刻 + 头像）、相遇卡、便笺、日程。
 * 由 ib-skin.js 在 IB 外观下挂到 AionsHome 桌面（/）上；原桌面的应用网格、Dock、拖拽排序保持不变。
 * 设置存 /api/skin 的 desk 字段（手机 App 与浏览器共用）；长按挂件区打开桌面设置。
 */
(function () {
  'use strict';
  if (document.getElementById('ib-desk')) return;

  var SMALL = {
    meet: { name: '相遇卡' },
    notes: { name: '便笺' },
    sched: { name: '日程' }
  };
  var DEFAULT_DESK = { widgets: ['meet', 'notes'], meet_actor: '' };
  var WEEK = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
  var MON = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];

  var skin = {};
  var desk = clone(DEFAULT_DESK);
  var people = { user: { name: '你', avatar: '/public/UserIcon.png' }, actors: [] };
  var notes = null;
  var schedules = null;
  var root = null;

  function clone(v) { return JSON.parse(JSON.stringify(v)); }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
  function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }

  function normalizeDesk(d) {
    d = Object.assign(clone(DEFAULT_DESK), d && typeof d === 'object' ? d : {});
    d.widgets = (Array.isArray(d.widgets) ? d.widgets : DEFAULT_DESK.widgets).filter(function (k) { return SMALL[k]; });
    return d;
  }

  function saveDesk() {
    skin.desk = desk;
    if (window.IBSkin) window.IBSkin.commit(skin);
    return fetch('/api/skin', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(skin) })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (saved) { if (saved) skin = saved; })
      .catch(function () {});
  }

  // ── 角色 ──
  function activeActors() { return people.actors.filter(function (a) { return a.enabled; }); }
  function actorById(id) {
    if (id === 'user') return { id: 'user', name: people.user.name, avatar: people.user.avatar };
    return people.actors.filter(function (a) { return a.id === id; })[0] || null;
  }
  function meetActor() {
    var list = activeActors();
    return list.filter(function (a) { return a.id === desk.meet_actor; })[0] || list[0] || null;
  }
  function avatarOf(a) { return (a && a.avatar) || '/public/AIIcon.png'; }

  // ── Hero ──
  function heroHtml() {
    return '<div class="ib-hero-main">' +
      '<div class="ib-hero-date"><span data-k="fdate"></span><i></i><span data-k="week"></span></div>' +
      '<div class="ib-time"><span class="t-shadow" data-k="time2"></span><span class="t-face" data-k="time"></span></div>' +
      '</div>' +
      '<div class="ib-hero-side">' +
      '<div class="ib-duo" data-act="cards"><img class="me" src="' + esc(people.user.avatar) + '" alt=""><img class="ai" src="' + esc(avatarOf(meetActor())) + '" alt=""></div>' +
      '<button class="ib-icon-btn" type="button" data-act="search" aria-label="搜索聊天记录">' +
      '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/></svg></button>' +
      '</div>';
  }
  function tick() {
    if (!root) return;
    var now = new Date();
    var t = pad(now.getHours()) + ':' + pad(now.getMinutes());
    root.querySelectorAll('[data-k="time"],[data-k="time2"]').forEach(function (n) { n.textContent = t; });
    var f = root.querySelector('[data-k="fdate"]'); if (f) f.textContent = MON[now.getMonth()] + ' ' + now.getDate();
    var w = root.querySelector('[data-k="week"]'); if (w) w.textContent = WEEK[now.getDay()];
  }

  // ── 相遇卡（点按切换 AI） ──
  function meetHtml() {
    var a = meetActor();
    var list = activeActors();
    var since = a && a.meet_since ? new Date(a.meet_since + 'T00:00:00') : null;
    var days = null, left = null;
    if (since && !isNaN(since)) {
      var t0 = new Date(); t0.setHours(0, 0, 0, 0);
      days = Math.max(0, Math.round((t0 - since) / 86400000));
      var next = new Date(since); next.setFullYear(t0.getFullYear());
      if (next < t0) next.setFullYear(t0.getFullYear() + 1);
      left = Math.round((next - t0) / 86400000);
    }
    var dots = list.length > 1 ? '<div class="dots">' + list.map(function (x) {
      return '<i' + (a && x.id === a.id ? ' class="on" style="background:' + esc(x.color) + '"' : '') + '></i>';
    }).join('') + '</div>' : '';
    return '<span class="wgt-lab">Together</span>' +
      '<div class="names">' + esc(people.user.name) + '<i>&amp;</i>' + esc(a ? a.name : 'AI') + '</div>' +
      '<div class="tf">together for</div>' +
      '<div class="days"><b' + (a ? ' style="color:' + esc(a.color) + '"' : '') + '>' + (days === null ? '—' : days) + '</b><span>' + (days === 1 ? 'Day' : 'Days') + '</span></div>' +
      '<div class="cd">' + (days === null ? '去 AI 名片里设置相遇的日子' : (left === 0 ? 'anniversary is today' : left + ' days to go')) + '</div>' + dots;
  }

  // ── 便笺（你和每个 AI 都能留） ──
  function fmtAt(ts) {
    var at = new Date(ts * 1000);
    return (at.getMonth() + 1) + '.' + at.getDate() + ' ' + pad(at.getHours()) + ':' + pad(at.getMinutes());
  }
  function notesHtml() {
    var n = notes && notes[0];
    var who = n ? actorById(n.author) : null;
    var by = n ? '<span class="by"><img src="' + esc(n.author === 'user' ? people.user.avatar : avatarOf(who)) + '" alt="">' + esc(who ? who.name : n.author) + '</span>' : '';
    return '<span class="wgt-lab">Notes</span><div class="note">' +
      '<div class="body' + (n ? '' : ' empty') + '">' + (n ? esc(n.text) : (notes === null ? '读取中…' : '点这里写点什么…')) + '</div>' +
      '<div class="date">' + by + (n ? fmtAt(n.at) : '') + '</div></div>';
  }

  // ── 日程 ──
  function parseWhen(s) {
    if (!s) return null;
    var d = new Date(String(s).replace(' ', 'T'));
    return isNaN(d) ? null : d;
  }
  function schedHtml() {
    var now = new Date();
    var list = '';
    if (schedules === null) list = '<div class="none">读取中…</div>';
    else {
      var up = schedules.map(function (s) { return { s: s, at: parseWhen(s.trigger_at) }; })
        .filter(function (x) { return x.at && x.at >= new Date(now.getTime() - 3600000); })
        .sort(function (a, b) { return a.at - b.at; }).slice(0, 3);
      list = up.length ? up.map(function (x) {
        var sameDay = x.at.toDateString() === now.toDateString();
        var when = sameDay ? pad(x.at.getHours()) + ':' + pad(x.at.getMinutes()) : (x.at.getMonth() + 1) + '/' + x.at.getDate();
        return '<div class="ev"><time>' + when + '</time><span>' + esc(x.s.content) + '</span></div>';
      }).join('') : '<div class="none">接下来没有安排</div>';
    }
    return '<span class="wgt-lab">Schedule</span><div class="head"><div class="datebox"><b>' + now.getDate() + '</b><small>' + MON[now.getMonth()] + '</small></div>' +
      '<h4>Schedule<span class="cn">日程</span></h4></div><div class="list">' + list + '</div>';
  }

  function render() {
    if (!root) return;
    root.innerHTML = '';
    var hero = el('div', 'wgt full ib-hero', heroHtml());
    root.appendChild(hero);
    desk.widgets.forEach(function (key) {
      var w;
      if (key === 'meet') { w = el('div', 'wgt ib-meet', meetHtml()); w.dataset.act = 'meet-next'; }
      if (key === 'notes') { w = el('div', 'wgt ib-notes', notesHtml()); w.dataset.act = 'note'; }
      if (key === 'sched') { w = el('div', 'wgt ib-sched', schedHtml()); w.dataset.act = 'schedule'; }
      if (w) root.appendChild(w);
    });
    if (desk.widgets.length % 2 === 1) root.lastChild.classList.add('full');
    tick();
    // 挂件高度变化后让原桌面重新计算每页能放几行
    window.dispatchEvent(new Event('resize'));
  }

  // ── 底部面板 ──
  function sheet(html) {
    var mask = el('div', 'ib-sheet-mask');
    var box = el('div', 'ib-sheet', html);
    mask.appendChild(box);
    mask.addEventListener('click', function (e) { if (e.target === mask) mask.remove(); });
    document.body.appendChild(mask);
    return box;
  }

  function openNote() {
    var list = (notes || []).slice(0, 12).map(function (n) {
      var who = actorById(n.author);
      return '<div class="nrow"><img src="' + esc(n.author === 'user' ? people.user.avatar : avatarOf(who)) + '" alt="">' +
        '<div class="nmain"><div class="nmeta"><b>' + esc(who ? who.name : n.author) + '</b><span>' + fmtAt(n.at) + '</span>' +
        '<button type="button" data-del="' + esc(n.id) + '" aria-label="删除">×</button></div><div class="ntext">' + esc(n.text) + '</div></div></div>';
    }).join('');
    var box = sheet('<h3>Notes<small>桌面便笺</small></h3>' +
      '<div class="hint">你和 AI 都可以在这里留言；AI 聊天时想到你，会自己贴一张。</div>' +
      '<textarea id="ib-note-text" placeholder="写一张新的便笺…" style="min-height:90px"></textarea>' +
      '<div class="acts"><button type="button" data-x="cancel">关闭</button><button type="button" class="primary" data-x="save">贴上去</button></div>' +
      '<div class="sec"><b>最近的便笺</b><div class="nlist">' + (list || '<div class="hint">还没有便笺</div>') + '</div></div>');
    var ta = box.querySelector('textarea');
    box.querySelector('[data-x="cancel"]').onclick = function () { box.parentNode.remove(); };
    box.querySelector('[data-x="save"]').onclick = function () {
      var text = ta.value.trim();
      if (!text) { ta.focus(); return; }
      fetch('/api/desk/notes', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: text }) })
        .then(function () { box.parentNode.remove(); loadNotes(); });
    };
    box.querySelectorAll('[data-del]').forEach(function (b) {
      b.onclick = function () {
        if (!confirm('删掉这张便笺？')) return;
        fetch('/api/desk/notes/' + encodeURIComponent(b.dataset.del), { method: 'DELETE' })
          .then(function () { box.parentNode.remove(); loadNotes(); });
      };
    });
  }

  function openSettings() {
    var order = desk.widgets.slice();
    Object.keys(SMALL).forEach(function (k) { if (order.indexOf(k) === -1) order.push(k); });
    var box = sheet('<h3>Desk<small>桌面设置</small></h3>' +
      '<div class="sec"><b>挂件</b><div class="hint">勾选要显示的挂件，用 ↑↓ 调顺序；两个一行。</div><div id="ib-wlist"></div></div>' +
      '<div class="sec"><b>AI</b><div class="hint">头像、签名、相遇的日子都在 AI 名片里设置；相遇卡点一下就能切换到下一位。</div>' +
      '<div class="acts" style="margin-top:6px"><a class="btn" href="/ai-cards">打开 AI 名片</a></div></div>' +
      '<div class="sec"><b>外观</b><div class="acts" style="margin-top:6px"><a class="btn" href="/skin">打开美化工作台</a></div></div>' +
      '<div class="acts"><button type="button" data-x="cancel">取消</button><button type="button" class="primary" data-x="save">保存</button></div>');
    var on = {};
    desk.widgets.forEach(function (k) { on[k] = true; });
    function drawList() {
      var wrap = box.querySelector('#ib-wlist'); wrap.innerHTML = '';
      order.forEach(function (k, i) {
        var row = el('div', 'wrow', '<input type="checkbox"' + (on[k] ? ' checked' : '') + '><span>' + SMALL[k].name + '</span>' +
          '<button type="button" data-d="-1"' + (i === 0 ? ' disabled' : '') + '>↑</button><button type="button" data-d="1"' + (i === order.length - 1 ? ' disabled' : '') + '>↓</button>');
        row.querySelector('input').onchange = function () { on[k] = this.checked; };
        row.querySelectorAll('button').forEach(function (b) {
          b.onclick = function () { var j = i + (+b.dataset.d); order[i] = order[j]; order[j] = k; drawList(); };
        });
        wrap.appendChild(row);
      });
    }
    drawList();
    box.querySelector('[data-x="cancel"]').onclick = function () { box.parentNode.remove(); };
    box.querySelector('[data-x="save"]').onclick = function () {
      desk.widgets = order.filter(function (k) { return on[k]; });
      saveDesk(); render(); box.parentNode.remove();
      if (desk.widgets.indexOf('sched') !== -1 && schedules === null) loadSchedules();
    };
  }

  // ── 交互：点按 / 长按 ──
  function bind() {
    var timer = null, longPressed = false, startX = 0, startY = 0;
    root.addEventListener('pointerdown', function (e) {
      longPressed = false; startX = e.clientX; startY = e.clientY;
      clearTimeout(timer);
      timer = setTimeout(function () {
        longPressed = true;
        root.classList.add('editing');
        if (navigator.vibrate) navigator.vibrate(12);
        setTimeout(function () { root.classList.remove('editing'); openSettings(); }, 260);
      }, 600);
    });
    root.addEventListener('pointermove', function (e) {
      if (Math.abs(e.clientX - startX) > 8 || Math.abs(e.clientY - startY) > 8) clearTimeout(timer);
    });
    ['pointerup', 'pointercancel', 'pointerleave'].forEach(function (t) { root.addEventListener(t, function () { clearTimeout(timer); }); });
    root.addEventListener('contextmenu', function (e) { e.preventDefault(); });
    root.addEventListener('click', function (e) {
      if (longPressed) { e.preventDefault(); return; }
      var target = e.target.closest('[data-act]');
      if (!target) return;
      var act = target.dataset.act;
      if (act === 'search' && typeof window.openGlobalSearch === 'function') window.openGlobalSearch();
      else if (act === 'note') openNote();
      else if (act === 'schedule') location.href = '/schedule';
      else if (act === 'settings') openSettings();
      else if (act === 'cards') location.href = '/ai-cards';
      else if (act === 'meet-next') {
        var list = activeActors();
        if (list.length < 2) { location.href = '/ai-cards'; return; }
        var cur = meetActor();
        var i = list.indexOf(cur);
        desk.meet_actor = list[(i + 1) % list.length].id;
        saveDesk(); render();
      }
    });
  }

  function loadNotes() {
    fetch('/api/desk/notes', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (rows) { notes = Array.isArray(rows) ? rows : []; render(); })
      .catch(function () { notes = []; render(); });
  }

  function loadSchedules() {
    fetch('/api/schedules?status=active', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (rows) { schedules = Array.isArray(rows) ? rows : []; render(); })
      .catch(function () { schedules = []; render(); });
  }

  function mount() {
    var screen = document.getElementById('screen');
    var pages = document.getElementById('appPages');
    if (!screen || !pages) return;
    var anchor = document.querySelector('.home-quick-row') || pages;
    root = el('div');
    root.id = 'ib-desk';
    screen.insertBefore(root, anchor);
    skin = window.IBSkin ? window.IBSkin.current() : {};
    desk = normalizeDesk(skin.desk);
    render();
    bind();
    setInterval(tick, 10000);

    fetch('/api/actors', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) { people = data; render(); } })
      .catch(function () {});
    loadNotes();
    setInterval(loadNotes, 60000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) loadNotes(); });
    fetch('/api/skin', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) { if (s) { skin = s; desk = normalizeDesk(s.desk); render(); } })
      .catch(function () {});
    if (desk.widgets.indexOf('sched') !== -1) loadSchedules();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true });
  else mount();
})();
