/* 旅行地球和地图（11-08 小猫）：手指转地球，放大看地图，点标记看去过几次和当时的记录；世界窗目录。 */
(function () {
  'use strict';
  var COLORS = { me: '#e8628a' };
  var PALETTE = ['#3d6fd8', '#e0913a', '#3aa76d', '#9a5bd6', '#d6455b', '#2aa9b8'];
  var state = { pins: [], actors: [], rotate: [-105, -25], leaflet: null, moved: false, spinning: true, land: null, tile: 'world', tileLayer: null };

  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function api(path, opts) {
    return fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts || {})).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.detail || '没成功'); return j; });
    });
  }
  function toast(text) { var d = document.createElement('div'); d.className = 'tm-toast'; d.textContent = text; document.body.appendChild(d); setTimeout(function () { d.remove(); }, 2800); }
  function colorOf(pin) {
    if (pin.kind === 'me') return COLORS.me;
    var id = pin.people && pin.people[0] && pin.people[0].id;
    return COLORS[id] || '#3d6fd8';
  }
  function photo(path) { return /^\/uploads\/[\w./-]+$/.test(String(path || '')) && String(path).indexOf('..') < 0 ? path : ''; }
  function when(v) {
    if (v.date) return v.date;
    return new Date((v.at || 0) * 1000).toLocaleDateString('zh-CN', { year: 'numeric', month: 'numeric', day: 'numeric' });
  }

  // ── 标记详情 ──
  function openPin(pin) {
    var who = pin.kind === 'me' ? '你' : pin.people.map(function (p) { return p.name; }).join('、');
    var list = pin.visits.map(function (v) {
      if (pin.kind === 'me') {
        return '<div class="tm-visit"><div class="tm-when">' + esc(when(v)) + '<button class="tm-del" data-del="' + esc(v.id) + '">删除</button></div>' +
          (v.note ? '<p>' + esc(v.note) + '</p>' : '') + '</div>';
      }
      var photos = (v.photos || []).map(photo).filter(Boolean);
      return '<div class="tm-visit"><div class="tm-when">' + esc(when(v)) + ' · ' + esc(v.people.map(function (p) { return p.name; }).join('、')) +
        (v.weather ? ' · 当时 ' + esc(v.weather) : '') + '</div>' +
        (photos.length ? '<div class="tm-photos">' + photos.map(function (p) { return '<img src="' + esc(p) + '" alt="" loading="lazy">'; }).join('') + '</div>' : '') +
        v.parts.map(function (part) {
          return '<h5>' + esc(part.title || part.who) + (v.parts.length > 1 ? ' · ' + esc(part.who) : '') + '</h5><p>' + esc(part.text) + '</p>';
        }).join('') + '</div>';
    }).join('');
    $('tm-sheet-body').innerHTML = '<h3>' + esc(pin.name) + '</h3><div class="tm-sub">' + esc(pin.label) + '</div>' +
      '<div class="tm-count">' + esc(who) + ' 来过 <b>' + pin.count + '</b> 次</div>' + list;
    $('tm-sheet').hidden = false;
  }

  // ── 地球 ──
  var canvas, ctx, projection, path, size = 0;
  function resize() {
    var ratio = window.devicePixelRatio || 1;
    size = Math.min(canvas.parentNode.clientWidth, 420);
    canvas.style.width = canvas.style.height = size + 'px';
    canvas.width = canvas.height = Math.round(size * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    projection.translate([size / 2, size / 2]).scale(size / 2 - 6);
    draw();
  }
  function visible(pin) {
    var center = projection.invert([size / 2, size / 2]);
    return window.d3.geoDistance([pin.lng, pin.lat], center) < Math.PI / 2 - 0.02;
  }
  function draw() {
    if (!ctx || !size) return;
    projection.rotate(state.rotate);
    ctx.clearRect(0, 0, size, size);
    ctx.beginPath(); path({ type: 'Sphere' }); ctx.fillStyle = '#cfe2f7'; ctx.fill();
    ctx.beginPath(); path(window.d3.geoGraticule10()); ctx.strokeStyle = 'rgba(90,130,190,.18)'; ctx.lineWidth = 0.6; ctx.stroke();
    if (state.land) { ctx.beginPath(); path(state.land); ctx.fillStyle = '#f4f1e6'; ctx.fill(); ctx.strokeStyle = 'rgba(120,140,170,.55)'; ctx.lineWidth = 0.6; ctx.stroke(); }
    ctx.beginPath(); path({ type: 'Sphere' }); ctx.strokeStyle = 'rgba(90,130,190,.5)'; ctx.lineWidth = 1; ctx.stroke();
    state.pins.forEach(function (pin) {
      if (!visible(pin)) return;
      var p = projection([pin.lng, pin.lat]);
      ctx.beginPath(); ctx.arc(p[0], p[1], 4 + Math.min(pin.count, 5), 0, Math.PI * 2);
      ctx.fillStyle = colorOf(pin); ctx.fill(); ctx.lineWidth = 1.5; ctx.strokeStyle = '#fff'; ctx.stroke();
    });
  }
  function spin() {
    if (!state.spinning) return;
    state.rotate = [state.rotate[0] + 0.12, state.rotate[1]];
    draw();
    requestAnimationFrame(spin);
  }
  function pinAt(x, y) {
    var best = null, bestD = 22;
    state.pins.forEach(function (pin) {
      if (!visible(pin)) return;
      var p = projection([pin.lng, pin.lat]), d = Math.hypot(p[0] - x, p[1] - y);
      if (d < bestD) { bestD = d; best = pin; }
    });
    return best;
  }
  function bindGlobe() {
    var last = null, start = null;
    canvas.addEventListener('pointerdown', function (e) {
      state.spinning = false; last = [e.clientX, e.clientY]; start = last; state.moved = false; canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener('pointermove', function (e) {
      if (!last) return;
      var k = 180 / (Math.PI * (size / 2));
      state.rotate = [state.rotate[0] + (e.clientX - last[0]) * k, Math.max(-85, Math.min(85, state.rotate[1] - (e.clientY - last[1]) * k))];
      last = [e.clientX, e.clientY];
      if (Math.hypot(e.clientX - start[0], e.clientY - start[1]) > 6) state.moved = true;
      draw();
    });
    function end(e) {
      if (last && !state.moved) {
        var box = canvas.getBoundingClientRect(), pin = pinAt(e.clientX - box.left, e.clientY - box.top);
        if (pin) openPin(pin);
      }
      last = null;
    }
    canvas.addEventListener('pointerup', end);
    canvas.addEventListener('pointercancel', function () { last = null; });
  }

  // ── 放大地图 ──
  function loadLeaflet() {
    if (window.L) return Promise.resolve();
    return new Promise(function (resolve, reject) {
      var css = document.createElement('link'); css.rel = 'stylesheet'; css.href = '/static/vendor/globe/leaflet.css'; document.head.appendChild(css);
      var js = document.createElement('script'); js.src = '/static/vendor/globe/leaflet.js'; js.onload = resolve; js.onerror = reject; document.head.appendChild(js);
    });
  }
  var TILES = {
    cn: { url: 'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}', sub: '1234', credit: '© 高德地图' },
    world: { url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', sub: 'abc', credit: '© OpenStreetMap' }
  };
  function setTiles() {
    var t = TILES[state.tile];
    if (state.tileLayer) state.tileLayer.remove();
    state.tileLayer = window.L.tileLayer(t.url, { subdomains: t.sub, attribution: t.credit, maxZoom: 18 }).addTo(state.leaflet);
    $('tm-tiles').textContent = state.tile === 'cn' ? '换国外地图' : '换国内地图';
  }
  function drawLeafletPins() {
    if (!state.leaflet) return;
    if (state.markers) state.markers.remove();
    state.markers = window.L.layerGroup(state.pins.map(function (pin) {
      return window.L.circleMarker([pin.lat, pin.lng], { radius: 8 + Math.min(pin.count, 6), color: '#fff', weight: 2, fillColor: colorOf(pin), fillOpacity: 0.95 })
        .bindTooltip(pin.name + ' · ' + pin.count + ' 次').on('click', function () { openPin(pin); });
    })).addTo(state.leaflet);
  }
  function openMap() {
    $('tm-map-wrap').hidden = false;
    loadLeaflet().then(function () {
      if (!state.leaflet) {
        var center = projection.invert([size / 2, size / 2]) || [105, 30];
        state.leaflet = window.L.map('tm-map', { worldCopyJump: true }).setView([center[1], center[0]], 3);
        setTiles();
      }
      state.leaflet.invalidateSize();
      drawLeafletPins();
    }).catch(function () { toast('地图没加载出来'); });
  }

  // ── 她自己去过的地方 ──
  function addPlace(near) {
    var name = $('tm-add-name').value.trim();
    if (!near && !name) { toast('写一下地名'); return; }
    api('/api/travel/my-places', { method: 'POST', body: JSON.stringify({ name: name, near: !!near, date: $('tm-add-date').value, note: $('tm-add-note').value.trim() }) })
      .then(function (r) { $('tm-add-name').value = ''; $('tm-add-note').value = ''; toast('标上了：' + r.name); return load(); })
      .catch(function (e) { toast(e.message); });
  }

  // ── 世界窗目录 ──
  function loadDirectory() {
    api('/api/travel/cameras').then(function (j) {
      $('tm-country').innerHTML = '<option value="">选一个国家</option>' + j.countries.map(function (c) { return '<option value="' + esc(c.slug) + '">' + esc(c.name) + '</option>'; }).join('');
    }).catch(function () { $('tm-cities').textContent = '目录这会儿读不到'; });
  }
  function loadCities() {
    var country = $('tm-country').value, box = $('tm-cities');
    if (!country) { box.innerHTML = ''; return; }
    box.textContent = '读取中…';
    api('/api/travel/cameras?country=' + encodeURIComponent(country)).then(function (j) {
      box.innerHTML = j.cities.length ? j.cities.map(function (c) { return '<button data-city="' + esc(c.name) + '">' + esc(c.name) + '</button>'; }).join('') : '这里还没有摄像头';
    }).catch(function (e) { box.textContent = e.message; });
  }

  function load() {
    return api('/api/travel/map').then(function (j) {
      state.pins = j.pins;
      $('tm-empty').textContent = j.pins.length ? '' : '还没有标记。AI 旅行回来，或者你加一个去过的地方，就会出现在这里。';
      $('tm-pending').textContent = j.pending ? '还有 ' + j.pending + ' 个旧地方在补坐标，过一会儿再来看' : '';
      draw(); drawLeafletPins();
    });
  }

  function init(actors) {
    state.actors = actors || [];
    state.actors.forEach(function (a, i) { COLORS[a.id] = a.color && /^#[0-9a-f]{3,8}$/i.test(a.color) ? a.color : PALETTE[i % PALETTE.length]; });
    $('tm-legend').innerHTML = state.actors.map(function (a) { return '<span><i style="background:' + esc(COLORS[a.id]) + '"></i>' + esc(a.name) + '</span>'; }).join('') +
      '<span><i style="background:' + COLORS.me + '"></i>你</span>';
    $('tm-look-actor').innerHTML = state.actors.map(function (a) { return '<option value="' + esc(a.id) + '">' + esc(a.name) + '</option>'; }).join('');
    if (!canvas) {
      canvas = $('tm-globe'); ctx = canvas.getContext('2d');
      projection = window.d3.geoOrthographic().clipAngle(90);
      path = window.d3.geoPath(projection, ctx);
      bindGlobe();
      window.addEventListener('resize', resize);
      if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) state.spinning = false;
      $('tm-open-map').onclick = openMap;
      $('tm-close-map').onclick = function () { $('tm-map-wrap').hidden = true; };
      $('tm-tiles').onclick = function () { state.tile = state.tile === 'cn' ? 'world' : 'cn'; setTiles(); };
      $('tm-sheet').addEventListener('click', function (e) {
        if (e.target === this || e.target.closest('[data-close]')) { this.hidden = true; return; }
        var del = e.target.closest('[data-del]');
        if (del && confirm('删除这条记录？')) {
          api('/api/travel/my-places/' + encodeURIComponent(del.dataset.del), { method: 'DELETE' }).then(function () { $('tm-sheet').hidden = true; return load(); }).catch(function (err) { toast(err.message); });
        }
      });
      $('tm-add').onclick = function () { addPlace(false); };
      $('tm-add-near').onclick = function () { addPlace(true); };
      $('tm-country').onchange = loadCities;
      $('tm-cities').addEventListener('click', function (e) {
        var btn = e.target.closest('[data-city]');
        if (!btn) return;
        var who = $('tm-look-actor');
        api('/api/travel/look', { method: 'POST', body: JSON.stringify({ actor: who.value, place: btn.dataset.city }) })
          .then(function () { toast(who.options[who.selectedIndex].text + ' 去看 ' + btn.dataset.city + ' 了，回来在私聊里说'); })
          .catch(function (err) { toast(err.message); });
      });
    }
    resize();
    fetch('/static/vendor/globe/land-110m.json').then(function (r) { return r.json(); }).then(function (world) {
      state.land = window.topojson.feature(world, world.objects.land); draw();
    }).catch(function () {});
    load().catch(function () {});
    loadDirectory();
    if (state.spinning) spin();
  }
  window.TravelMap = { init: init };
})();
