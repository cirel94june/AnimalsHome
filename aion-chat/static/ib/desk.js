/*
 * IB 桌面挂件：Hero（日期 + 大时刻 + 头像）、相遇卡、便笺、日程、月历、音乐、相片卡、装饰。
 * 每个挂件可设宽窄，整体可换材质；设置里按住 ≡ 拖动排序。
 * 由 ib-skin.js 在 IB 外观下挂到 AionsHome 桌面（/）上；原桌面的应用网格、Dock、拖拽排序保持不变。
 * 设置存 /api/skin 的 desk 字段（手机 App 与浏览器共用）；长按挂件区打开桌面设置。
 */
(function () {
  'use strict';
  if (document.getElementById('ib-desk')) return;

  // size：默认宽度（half 两个一行 / full 占一整行），可在桌面设置里逐个改
  var SMALL = {
    meet: { name: '相遇卡', size: 'half' },
    notes: { name: '便笺', size: 'half' },
    sched: { name: '日程', size: 'half' },
    cal: { name: '月历', size: 'full' },
    music: { name: '音乐', size: 'half' },
    photo: { name: '相片卡', size: 'half' },
    deco: { name: '装饰', size: 'half' }
  };
  var MATERIALS = { glass: '毛玻璃', solid: '实底', clear: '透明' };
  var DEFAULT_DESK = {
    widgets: ['meet', 'notes'], meet_actor: '', sizes: {}, material: 'glass',
    birthday: '', dates: [], deco: { text: '', image: '' }, photo_mode: 'latest'
  };
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

  var MD = /^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/;
  function normalizeDesk(d) {
    d = Object.assign(clone(DEFAULT_DESK), d && typeof d === 'object' ? d : {});
    d.widgets = (Array.isArray(d.widgets) ? d.widgets : DEFAULT_DESK.widgets).filter(function (k, i, a) { return SMALL[k] && a.indexOf(k) === i; });
    d.sizes = d.sizes && typeof d.sizes === 'object' ? d.sizes : {};
    if (!MATERIALS[d.material]) d.material = 'glass';
    if (!MD.test(d.birthday || '')) d.birthday = '';
    d.dates = (Array.isArray(d.dates) ? d.dates : []).filter(function (x) { return x && MD.test(x.md || '') && x.name; }).slice(0, 20);
    d.deco = Object.assign({ text: '', image: '' }, d.deco && typeof d.deco === 'object' ? d.deco : {});
    if (d.photo_mode !== 'random') d.photo_mode = 'latest';
    return d;
  }
  function sizeOf(key) { return desk.sizes[key] === 'full' || desk.sizes[key] === 'half' ? desk.sizes[key] : SMALL[key].size; }

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

  // ── 月历：你的生日、每位 AI 的相遇纪念日、你自己加的日子 ──
  var tracks = null;
  var photos = null;
  function marks() {
    var list = [];
    if (desk.birthday) list.push({ md: desk.birthday, name: '你的生日', color: 'var(--ib-acc)' });
    activeActors().forEach(function (a) {
      if (a.meet_since && /^\d{4}-\d{2}-\d{2}$/.test(a.meet_since)) list.push({ md: a.meet_since.slice(5), name: '和' + a.name + '相遇', color: a.color });
    });
    desk.dates.forEach(function (x) { list.push({ md: x.md, name: x.name, color: 'var(--ib-acc2, var(--ib-acc))' }); });
    return list;
  }
  function daysUntil(md, today) {
    var d = new Date(today.getFullYear(), +md.slice(0, 2) - 1, +md.slice(3));
    if (d < today) d.setFullYear(today.getFullYear() + 1);
    return Math.round((d - today) / 86400000);
  }
  function calHtml() {
    var now = new Date(); now.setHours(0, 0, 0, 0);
    var y = now.getFullYear(), m = now.getMonth();
    var first = new Date(y, m, 1).getDay(), days = new Date(y, m + 1, 0).getDate();
    var all = marks(), byDay = {};
    all.forEach(function (x) { if (+x.md.slice(0, 2) === m + 1) (byDay[+x.md.slice(3)] = byDay[+x.md.slice(3)] || []).push(x); });
    var cells = '';
    ['日', '一', '二', '三', '四', '五', '六'].forEach(function (w) { cells += '<i class="wk">' + w + '</i>'; });
    for (var i = 0; i < first; i++) cells += '<i></i>';
    for (var d = 1; d <= days; d++) {
      var mk = byDay[d];
      cells += '<i class="' + (d === now.getDate() ? 'today' : '') + (mk ? ' mk' : '') + '"' +
        (mk ? ' title="' + esc(mk.map(function (x) { return x.name; }).join('、')) + '" style="--mk:' + esc(mk[0].color) + '"' : '') + '>' + d + '</i>';
    }
    var next = all.map(function (x) { return { x: x, n: daysUntil(x.md, now) }; }).sort(function (a, b) { return a.n - b.n; })[0];
    var foot = next ? (next.n === 0 ? '今天是' + esc(next.x.name) : '还有 ' + next.n + ' 天 · ' + esc(next.x.name)) : '在桌面设置里加上生日和纪念日';
    return '<span class="wgt-lab">Calendar</span><div class="cal-head"><b>' + MON[m] + '</b><small>' + y + '</small></div>' +
      '<div class="cal-grid">' + cells + '</div><div class="cal-foot">' + foot + '</div>';
  }

  // ── 音乐：点歌台最近点过的歌 ──
  function musicHtml() {
    var t = tracks && tracks[0];
    if (!t) return '<span class="wgt-lab">Music</span><div class="mu-empty">' + (tracks === null ? '读取中…' : '去点歌台点一首') + '</div>';
    return '<span class="wgt-lab">Music</span><div class="mu">' +
      (t.cover_url ? '<img class="mu-cover" src="' + esc(t.cover_url) + '" alt="" loading="lazy">' : '<div class="mu-cover none">♪</div>') +
      '<div class="mu-text"><b>' + esc(t.title) + '</b><span>' + esc(t.artist || '') + '</span></div></div>';
  }

  // ── 相片卡：相册里最新的一张，或随机一张 ──
  var photoPick = null;
  function photoHtml() {
    var list = photos || [];
    if (!list.length) return '<span class="wgt-lab">Photo</span><div class="ph-empty">' + (photos === null ? '读取中…' : '相册里还没有照片') + '</div>';
    if (!photoPick || list.indexOf(photoPick) === -1) photoPick = desk.photo_mode === 'random' ? list[Math.floor(Math.random() * list.length)] : list[0];
    return '<div class="ph" style="background-image:url(&quot;' + esc(photoPick.thumbnail_url || photoPick.url) + '&quot;)"></div>' +
      '<span class="ph-cap">' + esc(photoPick.taken_on || '') + '</span>';
  }

  // ── 装饰：一句话 / 一张贴纸 ──
  function decoHtml() {
    var t = desk.deco.text, img = desk.deco.image;
    if (!t && !img) return '<div class="deco-empty">长按桌面，在设置里写一句话或放一张贴纸</div>';
    return (img ? '<img class="deco-img" src="' + esc(img) + '" alt="">' : '') + (t ? '<div class="deco-text">' + esc(t) + '</div>' : '');
  }

  var BUILD = {
    meet: function () { var w = el('div', 'wgt ib-meet', meetHtml()); w.dataset.act = 'meet-next'; return w; },
    notes: function () { var w = el('div', 'wgt ib-notes', notesHtml()); w.dataset.act = 'note'; return w; },
    sched: function () { var w = el('div', 'wgt ib-sched', schedHtml()); w.dataset.act = 'schedule'; return w; },
    cal: function () { return el('div', 'wgt ib-cal', calHtml()); },
    music: function () { var w = el('div', 'wgt ib-music', musicHtml()); w.dataset.act = 'music'; return w; },
    photo: function () { var w = el('div', 'wgt ib-photo', photoHtml()); w.dataset.act = 'album'; return w; },
    deco: function () { return el('div', 'wgt ib-deco', decoHtml()); }
  };

  function render() {
    if (!root) return;
    root.innerHTML = '';
    root.dataset.material = desk.material;
    var hero = el('div', 'wgt full ib-hero', heroHtml());
    root.appendChild(hero);
    var pendingHalf = null;  // 落单的半宽挂件拉成整行，不留空位
    desk.widgets.forEach(function (key) {
      var w = BUILD[key] && BUILD[key]();
      if (!w) return;
      if (sizeOf(key) === 'full') {
        if (pendingHalf) { pendingHalf.classList.add('full'); pendingHalf = null; }
        w.classList.add('full');
      } else {
        pendingHalf = pendingHalf ? null : w;
      }
      root.appendChild(w);
    });
    if (pendingHalf) pendingHalf.classList.add('full');
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
    var draft = clone(desk);
    var order = draft.widgets.slice();
    Object.keys(SMALL).forEach(function (k) { if (order.indexOf(k) === -1) order.push(k); });
    var on = {};
    draft.widgets.forEach(function (k) { on[k] = true; });
    var box = sheet('<h3>Desk<small>桌面设置</small></h3>' +
      '<div class="sec"><b>挂件</b><div class="hint">勾选要显示的；按住 ≡ 拖动调顺序；「宽 / 窄」决定占一整行还是半行。</div><div id="ib-wlist"></div></div>' +
      '<div class="sec"><b>挂件材质</b><div class="seg" id="ib-mat">' + Object.keys(MATERIALS).map(function (k) {
        return '<button type="button" data-v="' + k + '">' + MATERIALS[k] + '</button>'; }).join('') + '</div></div>' +
      '<div class="sec"><b>月历上的日子</b><div class="hint">AI 的相遇纪念日会自动标上（在 AI 名片里设置）。</div>' +
      '<label class="field">你的生日<input type="text" id="ib-bday" maxlength="5" placeholder="月-日，例如 03-14"></label>' +
      '<div id="ib-dates"></div><button type="button" class="btn small" id="ib-add-date">＋ 加一个纪念日</button></div>' +
      '<div class="sec"><b>相片卡</b><div class="seg" id="ib-pmode"><button type="button" data-v="latest">最新一张</button><button type="button" data-v="random">每次随机</button></div></div>' +
      '<div class="sec"><b>装饰</b><label class="field">一句话<input type="text" id="ib-deco-text" maxlength="60" placeholder="想贴在桌面上的话"></label>' +
      '<div class="acts" style="margin-top:6px"><button type="button" class="btn" id="ib-deco-up">换贴纸</button><button type="button" class="btn" id="ib-deco-clear">去掉贴纸</button>' +
      '<input type="file" id="ib-deco-file" accept="image/*" hidden></div></div>' +
      '<div class="sec"><b>AI</b><div class="hint">头像、签名、相遇的日子都在 AI 名片里设置；相遇卡点一下就能切换到下一位。</div>' +
      '<div class="acts" style="margin-top:6px"><a class="btn" href="/ai-cards">打开 AI 名片</a></div></div>' +
      '<div class="sec"><b>外观</b><div class="acts" style="margin-top:6px"><a class="btn" href="/skin">打开美化工作台</a></div></div>' +
      '<div class="acts"><button type="button" data-x="cancel">取消</button><button type="button" class="primary" data-x="save">保存</button></div>');
    var $ = function (s) { return box.querySelector(s); };

    function drawList() {
      var wrap = $('#ib-wlist'); wrap.innerHTML = '';
      order.forEach(function (k) {
        var full = (draft.sizes[k] || SMALL[k].size) === 'full';
        var row = el('div', 'wrow ib-wrow', '<span class="grip" aria-label="拖动排序">≡</span><input type="checkbox"' + (on[k] ? ' checked' : '') + '>' +
          '<span class="wname">' + SMALL[k].name + '</span><button type="button" class="size">' + (full ? '宽' : '窄') + '</button>');
        row.dataset.k = k;
        row.querySelector('input').onchange = function () { on[k] = this.checked; };
        row.querySelector('.size').onclick = function () { draft.sizes[k] = full ? 'half' : 'full'; drawList(); };
        wrap.appendChild(row);
      });
    }
    // 按住 ≡ 上下拖动（手机和电脑都用 pointer 事件，不依赖 HTML5 拖放）
    $('#ib-wlist').addEventListener('pointerdown', function (e) {
      var grip = e.target.closest('.grip'); if (!grip) return;
      e.preventDefault();
      var row = grip.parentNode, wrap = row.parentNode;
      row.classList.add('dragging');
      // 监听挂在 window 上：行被挪动时浏览器会放掉指针捕获，挂在 grip 上会收不到后续事件
      function move(ev) {
        // 放到「中线在手指下方」的第一行前面；拖得再快也能一次到位
        var before = null;
        Array.prototype.some.call(wrap.children, function (x) {
          if (x === row) return false;
          var r = x.getBoundingClientRect();
          if (ev.clientY < r.top + r.height / 2) { before = x; return true; }
          return false;
        });
        if (before !== row.nextSibling) wrap.insertBefore(row, before);
      }
      function up(ev) {
        if (ev && ev.type === 'pointerup') move(ev);  // 松手的位置为准
        row.classList.remove('dragging');
        window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up);
        order = Array.prototype.map.call(wrap.children, function (x) { return x.dataset.k; });
      }
      window.addEventListener('pointermove', move); window.addEventListener('pointerup', up); window.addEventListener('pointercancel', up);
    });

    function drawDates() {
      $('#ib-dates').innerHTML = draft.dates.map(function (x, i) {
        return '<div class="wrow"><span class="wname">' + esc(x.name) + ' · ' + esc(x.md) + '</span><button type="button" data-del-date="' + i + '">删除</button></div>';
      }).join('');
      box.querySelectorAll('[data-del-date]').forEach(function (b) { b.onclick = function () { draft.dates.splice(+b.dataset.delDate, 1); drawDates(); }; });
    }
    function seg(id, key) {
      var s = $(id);
      s.querySelectorAll('button').forEach(function (b) {
        b.classList.toggle('on', b.dataset.v === draft[key]);
        b.onclick = function () { draft[key] = b.dataset.v; seg(id, key); };
      });
    }
    drawList(); drawDates(); seg('#ib-mat', 'material'); seg('#ib-pmode', 'photo_mode');
    $('#ib-bday').value = draft.birthday;
    $('#ib-deco-text').value = draft.deco.text;
    $('#ib-add-date').onclick = function () {
      var name = prompt('纪念日叫什么？'); if (!name || !name.trim()) return;
      var md = prompt('哪一天？写成 月-日，例如 05-20'); if (!md) return;
      md = md.trim().replace(/^(\d)-/, '0$1-').replace(/-(\d)$/, '-0$1');
      if (!MD.test(md)) { alert('日期格式是 月-日，例如 05-20'); return; }
      draft.dates.push({ name: name.trim().slice(0, 20), md: md }); drawDates();
    };
    $('#ib-deco-up').onclick = function () { $('#ib-deco-file').click(); };
    $('#ib-deco-clear').onclick = function () { draft.deco.image = ''; alert('保存后贴纸会去掉'); };
    $('#ib-deco-file').onchange = function () {
      var f = this.files && this.files[0]; if (!f) return;
      var fd = new FormData(); fd.append('file', f);
      fetch('/api/skin/background', { method: 'POST', body: fd })
        .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail || r.status); return j; }); })
        .then(function (j) { draft.deco.image = j.url; alert('贴纸已上传，保存后生效'); })
        .catch(function (err) { alert('上传失败：' + err.message); });
    };
    $('[data-x="cancel"]').onclick = function () { box.parentNode.remove(); };
    $('[data-x="save"]').onclick = function () {
      var bday = $('#ib-bday').value.trim().replace(/^(\d)-/, '0$1-').replace(/-(\d)$/, '-0$1');
      if (bday && !MD.test(bday)) { alert('生日格式是 月-日，例如 03-14'); return; }
      draft.birthday = bday;
      draft.deco.text = $('#ib-deco-text').value.trim();
      draft.widgets = order.filter(function (k) { return on[k]; });
      desk = normalizeDesk(draft);
      photoPick = null;
      saveDesk(); render(); box.parentNode.remove();
      loadForWidgets();
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
      else if (act === 'music') location.href = '/music-station';
      else if (act === 'album') location.href = '/album';
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

  function loadTracks() {
    fetch('/api/music-station/tracks', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : { tracks: [] }; })
      .then(function (d) { tracks = Array.isArray(d.tracks) ? d.tracks : []; render(); })
      .catch(function () { tracks = []; render(); });
  }

  function loadPhotos() {
    fetch('/api/album/photos?limit=30', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : { photos: [] }; })
      .then(function (d) { photos = Array.isArray(d.photos) ? d.photos : []; render(); })
      .catch(function () { photos = []; render(); });
  }

  // 只为显示中的挂件取数据
  function loadForWidgets() {
    var on = desk.widgets;
    if (on.indexOf('sched') !== -1 && schedules === null) loadSchedules();
    if (on.indexOf('music') !== -1 && tracks === null) loadTracks();
    if (on.indexOf('photo') !== -1 && photos === null) loadPhotos();
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
      .then(function (s) { if (s) { skin = s; desk = normalizeDesk(s.desk); render(); loadForWidgets(); } })
      .catch(function () {});
    loadForWidgets();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true });
  else mount();
})();
