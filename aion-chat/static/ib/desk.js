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
  var DEFAULT_DESK = { widgets: ['meet', 'notes'], meet_since: '', meet_with: '', note: { text: '', at: 0 } };
  var WEEK = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
  var MON = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];

  var skin = {};
  var desk = clone(DEFAULT_DESK);
  var names = { user: '你', ai: 'AI' };
  var schedules = null;
  var root = null;

  function clone(v) { return JSON.parse(JSON.stringify(v)); }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
  function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }

  function normalizeDesk(d) {
    d = Object.assign(clone(DEFAULT_DESK), d && typeof d === 'object' ? d : {});
    d.widgets = (Array.isArray(d.widgets) ? d.widgets : DEFAULT_DESK.widgets).filter(function (k) { return SMALL[k]; });
    if (!d.note || typeof d.note !== 'object') d.note = { text: '', at: 0 };
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

  // ── Hero ──
  function heroHtml() {
    return '<div class="ib-hero-main">' +
      '<div class="ib-hero-date"><span data-k="fdate"></span><i></i><span data-k="week"></span></div>' +
      '<div class="ib-time"><span class="t-shadow" data-k="time2"></span><span class="t-face" data-k="time"></span></div>' +
      '</div>' +
      '<div class="ib-hero-side">' +
      '<div class="ib-duo"><img class="me" src="/public/UserIcon.png" alt=""><img class="ai" src="/public/AIIcon.png" alt=""></div>' +
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

  // ── 相遇卡 ──
  function meetHtml() {
    var partner = desk.meet_with || names.ai;
    var since = desk.meet_since ? new Date(desk.meet_since + 'T00:00:00') : null;
    var days = null, left = null;
    if (since && !isNaN(since)) {
      var t0 = new Date(); t0.setHours(0, 0, 0, 0);
      days = Math.max(0, Math.round((t0 - since) / 86400000));
      var next = new Date(since); next.setFullYear(t0.getFullYear());
      if (next < t0) next.setFullYear(t0.getFullYear() + 1);
      left = Math.round((next - t0) / 86400000);
    }
    return '<span class="wgt-lab">Together</span>' +
      '<div class="names">' + esc(names.user) + '<i>&amp;</i>' + esc(partner) + '</div>' +
      '<div class="tf">together for</div>' +
      '<div class="days"><b>' + (days === null ? '—' : days) + '</b><span>' + (days === 1 ? 'Day' : 'Days') + '</span></div>' +
      '<div class="cd">' + (days === null ? '长按设置相遇的日子' : (left === 0 ? 'anniversary is today' : left + ' days to go')) + '</div>';
  }

  // ── 便笺 ──
  function notesHtml() {
    var text = (desk.note && desk.note.text) || '';
    var at = desk.note && desk.note.at ? new Date(desk.note.at) : null;
    return '<span class="wgt-lab">Notes</span><div class="note">' +
      '<div class="body' + (text ? '' : ' empty') + '">' + (text ? esc(text) : '点这里写点什么…') + '</div>' +
      '<div class="date">' + (at ? (at.getMonth() + 1) + '.' + at.getDate() + ' ' + pad(at.getHours()) + ':' + pad(at.getMinutes()) : '') + '</div></div>';
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
      if (key === 'meet') { w = el('div', 'wgt ib-meet', meetHtml()); w.dataset.act = 'settings'; }
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
    var box = sheet('<h3>Notes<small>便笺</small></h3><textarea id="ib-note-text" placeholder="写点什么…"></textarea>' +
      '<div class="acts"><button type="button" data-x="cancel">取消</button><button type="button" class="primary" data-x="save">保存</button></div>');
    var ta = box.querySelector('textarea');
    ta.value = (desk.note && desk.note.text) || '';
    setTimeout(function () { ta.focus(); }, 50);
    box.querySelector('[data-x="cancel"]').onclick = function () { box.parentNode.remove(); };
    box.querySelector('[data-x="save"]').onclick = function () {
      desk.note = { text: ta.value.slice(0, 2000), at: Date.now() };
      saveDesk(); render(); box.parentNode.remove();
    };
  }

  function openSettings() {
    var order = desk.widgets.slice();
    Object.keys(SMALL).forEach(function (k) { if (order.indexOf(k) === -1) order.push(k); });
    var box = sheet('<h3>Desk<small>桌面设置</small></h3>' +
      '<div class="sec"><b>挂件</b><div class="hint">勾选要显示的挂件，用 ↑↓ 调顺序；两个一行。</div><div id="ib-wlist"></div></div>' +
      '<div class="sec"><b>相遇卡</b>' +
      '<label class="field">相遇的日子<input type="date" id="ib-meet-since"></label>' +
      '<label class="field">和谁（留空则用 AI 的名字）<input type="text" id="ib-meet-with" maxlength="24"></label></div>' +
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
    box.querySelector('#ib-meet-since').value = desk.meet_since || '';
    box.querySelector('#ib-meet-with').value = desk.meet_with || '';
    box.querySelector('[data-x="cancel"]').onclick = function () { box.parentNode.remove(); };
    box.querySelector('[data-x="save"]').onclick = function () {
      desk.widgets = order.filter(function (k) { return on[k]; });
      desk.meet_since = box.querySelector('#ib-meet-since').value;
      desk.meet_with = box.querySelector('#ib-meet-with').value.trim();
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
    });
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

    fetch('/api/worldbook', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (wb) {
        names.user = (wb && wb.user_name) || names.user;
        names.ai = (wb && wb.ai_name) || names.ai;
        render();
      }).catch(function () {});
    fetch('/api/skin', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) { if (s) { skin = s; desk = normalizeDesk(s.desk); render(); } })
      .catch(function () {});
    if (desk.widgets.indexOf('sched') !== -1) loadSchedules();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true });
  else mount();
})();
