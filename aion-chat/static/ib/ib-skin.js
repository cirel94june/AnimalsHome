/*
 * IB 皮肤加载器：在页面 <head> 里同步执行，先用本机缓存立即上色（不闪烁），
 * 再从 /api/skin 拉取最新设置（手机 App 与浏览器共用一份）。
 * 美化工作台保存后，通过 localStorage 事件让已打开的页面实时刷新。
 */
(function () {
  'use strict';
  var CACHE_KEY = 'ib_skin_cache_v1';
  var THEME_KEY = 'aion_chat_theme';
  var root = document.documentElement;
  var VERSION = (document.currentScript && document.currentScript.src.split('?v=')[1]) || '';

  // 可调变量：设置里的键 → CSS 变量
  var VARS = {
    tx: '--ib-tx', tx2: '--ib-tx2', tx3: '--ib-tx3', acc: '--ib-acc', acc2: '--ib-acc2',
    base: '--ib-base', scrim: '--ib-scrim',
    glass_a: '--ib-glass-a', blur: '--ib-blur', line: '--ib-line',
    topbar_a: '--ib-topbar-a', topbar_tx: '--ib-topbar-tx',
    card_a: '--ib-card-a', card_tx: '--ib-card-tx',
    input_a: '--ib-input-a', dock_a: '--ib-dock-a',
    user_from: '--ib-user-from', user_to: '--ib-user-to', user_tx: '--ib-user-tx',
    ai_bg: '--ib-ai-bg', ai_tx: '--ib-ai-tx',
    bubble_r: '--ib-bubble-r', bubble_tail: '--ib-bubble-tail', bubble_shadow: '--ib-bubble-shadow',
    font_scale: '--ib-font-scale', text_shadow: '--ib-text-shadow'
  };
  var PX = { blur: 1, bubble_r: 1, bubble_tail: 1 };

  // 全站色调整套更换（亮色系）
  var PRESETS = {
    internal: { name: '雾蓝 Internal', vars: {} },
    sakura: { name: '樱雾', vars: {
      tx: '#3d2433', tx2: '#7a5468', tx3: '#b08da0', acc: '#c45f8a', acc2: '#e08aac', base: '#f6e6ee',
      user_from: '#f2a7c3', user_to: '#d9779f', ai_tx: '#4a2c3c', line: 'rgba(214,150,182,0.32)',
      scrim: 'linear-gradient(180deg,rgba(255,240,247,0.42),rgba(252,232,242,0.62))' } },
    mint: { name: '薄荷', vars: {
      tx: '#173a33', tx2: '#3f6b61', tx3: '#7fa59b', acc: '#2f8f7a', acc2: '#5bb39c', base: '#e3f3ee',
      user_from: '#8fd1bd', user_to: '#4fa58d', ai_tx: '#1f3d36', line: 'rgba(120,190,170,0.32)',
      scrim: 'linear-gradient(180deg,rgba(240,252,248,0.4),rgba(228,246,240,0.6))' } },
    latte: { name: '奶茶', vars: {
      tx: '#3b2d22', tx2: '#76604d', tx3: '#ad9886', acc: '#a8714a', acc2: '#c99a73', base: '#f3ebe2',
      user_from: '#d8b08c', user_to: '#b98763', ai_tx: '#3f3026', line: 'rgba(190,160,130,0.32)',
      scrim: 'linear-gradient(180deg,rgba(252,246,238,0.42),rgba(246,236,224,0.62))' } },
    lavender: { name: '薰衣草', vars: {
      tx: '#26203f', tx2: '#5a5180', tx3: '#9a91bd', acc: '#6c5bc4', acc2: '#9584dc', base: '#ece8f8',
      user_from: '#b3a6ec', user_to: '#8473d3', ai_tx: '#2d2648', line: 'rgba(160,145,220,0.32)',
      scrim: 'linear-gradient(180deg,rgba(246,243,255,0.4),rgba(236,231,252,0.6))' } }
  };

  // IB 式页头：衬线英文大标题 + 中文小字
  var TITLE_EN = {
    '/moments': 'Circle', '/memory': 'Memory', '/settings': 'Settings', '/worldbook': 'World',
    '/schedule': 'Schedule', '/diary': 'Diary', '/album': 'Album', '/music-station': 'Music',
    '/wishes': 'Wishes', '/theater': 'Theater', '/date-theater': 'Date', '/reading': 'Reading',
    '/health': 'Health', '/location': 'Location', '/camera': 'Camera', '/monitor-logs': 'Logs',
    '/activity-logs': 'Activity', '/family-dynamics': 'Family', '/gift': 'Gifts',
    '/capabilities': 'Tools', '/app-supervision': 'Guard', '/memory-compression': 'Archive',
    '/heart-whispers': 'Whispers', '/english-corner': 'English', '/lounge-friends': 'Friends',
    '/taobao': 'Shop', '/xhs-lite': 'Notes', '/playground': 'Playground', '/mcp-tools': 'MCP', '/repair': 'Repair',
    '/fund': 'Fund', '/ai-cards': 'Cards', '/wallpaper': 'Wallpaper', '/hug': 'Hug', '/toys': 'Whisper', '/tts-test': 'Voice'
  };
  var LEADING_EMOJI = /^[\s\u200d\ufe0f\u2190-\u21ff\u2300-\u27bf\u2b00-\u2bff\ud83c-\udbff\udc00-\udfff]+/;
  var CHEVRON = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 5.5L8 12l6.5 6.5"/></svg>';

  function decorateHeaders(skin) {
    if (skin.enabled === false || skin.title_style === 'plain') return;
    var en = TITLE_EN[location.pathname.replace(/\/+$/, '') || '/'];
    Array.prototype.forEach.call(document.querySelectorAll('.top-bar > h2:not([data-ib-title])'), function (h2) {
      if (h2.children.length) return;  // 页面自己有结构的标题不动
      var cn = (h2.textContent || '').replace(LEADING_EMOJI, '').trim();
      if (!cn) return;
      h2.setAttribute('data-ib-title', '1');
      h2.textContent = '';
      if (en) {
        var e = document.createElement('span'); e.className = 'ib-ph-en'; e.textContent = en; h2.appendChild(e);
      }
      var c = document.createElement('span'); c.className = en ? 'ib-ph-cn' : 'ib-ph-only'; c.textContent = cn; h2.appendChild(c);
    });
    Array.prototype.forEach.call(document.querySelectorAll('.top-bar > .back-btn:not([data-ib-back])'), function (btn) {
      if (!/^\s*[⬅←‹<]\s*$/.test(btn.textContent || '')) return;
      btn.setAttribute('data-ib-back', '1');
      if (!btn.getAttribute('aria-label')) btn.setAttribute('aria-label', '返回');
      btn.innerHTML = CHEVRON;
    });
  }

  // IB 桌面挂件只挂在桌面（/）上
  function loadDesk() {
    if (location.pathname !== '/' || document.getElementById('ib-desk-js')) return;
    var v = VERSION;
    var css = document.createElement('link');
    css.rel = 'stylesheet'; css.href = '/static/ib/desk.css?v=' + v;
    document.head.appendChild(css);
    var js = document.createElement('script');
    js.id = 'ib-desk-js'; js.src = '/static/ib/desk.js?v=' + v;
    document.head.appendChild(js);
  }

  function clean(value) {
    // 防止设置里混入能跳出声明的字符
    return String(value == null ? '' : value).replace(/[;{}<>\\]/g, '').trim();
  }

  function cssUrl(url) {
    url = String(url || '');
    if (!/^(\/[\w\-./]*|https:\/\/[\w\-./%?=&#:]+)$/.test(url)) return '';
    return 'url("' + url.replace(/"/g, '') + '")';
  }

  function buildCss(skin) {
    var vars = Object.assign({}, (PRESETS[skin.preset] || PRESETS.internal).vars, skin.vars || {});
    var decl = [];
    Object.keys(VARS).forEach(function (key) {
      if (vars[key] === undefined || vars[key] === '') return;
      var value = clean(vars[key]);
      if (PX[key] && /^\d+(\.\d+)?$/.test(value)) value += 'px';
      decl.push(VARS[key] + ':' + value);
    });
    if (vars.title_font === 'serif') decl.push('--ib-title-font:var(--ib-serif)');
    var bg = cssUrl(skin.bg);
    if (bg) decl.push('--ib-bg-url:' + bg);
    var chatBg = cssUrl(skin.chat_bg);
    if (chatBg) decl.push('--ib-chat-bg-url:' + chatBg);
    var css = decl.length ? 'html[data-skin="ib"]{' + decl.join(';') + '}' : '';
    if (skin.custom_css) css += '\n/* 美化码 · 自定义 CSS */\n' + String(skin.custom_css).replace(/<\/?style/gi, '');
    return css;
  }

  function styleEl() {
    var el = document.getElementById('ib-skin-vars');
    if (!el) {
      el = document.createElement('style');
      el.id = 'ib-skin-vars';
      (document.head || root).appendChild(el);
    }
    return el;
  }

  function setBodyTheme() {
    if (document.body && root.dataset.skin === 'ib') document.body.dataset.theme = 'light';
  }

  function apply(skin) {
    skin = skin || {};
    var link = document.getElementById('ib-skin-css');
    if (skin.enabled === false) {
      delete root.dataset.skin;
      delete root.dataset.ibReduce;
      if (link) link.disabled = true;
      styleEl().textContent = '';
      return;
    }
    if (link) link.disabled = false;
    root.dataset.skin = 'ib';
    loadDesk();
    root.dataset.theme = 'light';
    try { localStorage.setItem(THEME_KEY, 'light'); } catch (e) {}
    if (skin.reduce_motion) root.dataset.ibReduce = '1'; else delete root.dataset.ibReduce;
    if (skin.icon_style === 'original') root.dataset.ibIcons = 'original'; else delete root.dataset.ibIcons;
    styleEl().textContent = buildCss(skin);
    setBodyTheme();
    if (document.readyState !== 'loading') decorateHeaders(skin);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', (skin.vars && skin.vars.base) || '#dfe9f6');
  }

  function readCache() {
    try { return JSON.parse(localStorage.getItem(CACHE_KEY) || 'null'); } catch (e) { return null; }
  }

  function writeCache(skin) {
    try { localStorage.setItem(CACHE_KEY, JSON.stringify(skin)); } catch (e) {}
  }

  var current = readCache() || { enabled: true };
  apply(current);
  document.addEventListener('DOMContentLoaded', function () { setBodyTheme(); decorateHeaders(current); }, { once: true });
  // 其他脚本（如 common.js）改了主题时拉回亮色
  window.addEventListener('aion-theme-applied', function (event) {
    if (root.dataset.skin === 'ib' && event.detail && event.detail.theme !== 'light' && window.applyAionTheme) {
      window.applyAionTheme('light');
    }
  });

  function refresh() {
    if (!window.fetch) return;
    fetch('/api/skin', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (skin) {
        if (!skin) return;
        if (JSON.stringify(skin) !== JSON.stringify(current)) {
          current = skin;
          writeCache(skin);
          apply(skin);
        }
      })
      .catch(function () {});
  }
  refresh();

  window.addEventListener('storage', function (event) {
    if (event.key === CACHE_KEY) {
      current = readCache() || current;
      apply(current);
    }
  });

  window.IBSkin = {
    presets: PRESETS,
    vars: VARS,
    current: function () { return JSON.parse(JSON.stringify(current)); },
    preview: function (skin) { apply(skin); },
    commit: function (skin) { current = skin; writeCache(skin); apply(skin); },
    buildCss: buildCss
  };
})();
