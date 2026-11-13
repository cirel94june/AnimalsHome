'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');

test('reset, undo and reload keep desktop widgets and personal dates', async () => {
  const original = {
    enabled: true, preset: 'sakura', vars: { bubble_r: 24 },
    bg: '/static/ib/bg-internal.jpg',
    slots: [{ name: '我的方案', preset: 'sakura', vars: {} }, null, null, null],
    desk: {
      widgets: ['cal', 'photo', 'notes'], sizes: { cal: 'full' },
      birthday: '05-20', dates: [{ md: '06-01', name: '测试纪念日' }],
      deco: { text: '测试装饰', image: '' }, photo_mode: 'random', material: 'solid'
    },
    desk_look: { bg: '/static/ib/bg-internal.jpg', widget_blur: 9, icons: {} }
  };
  let saved = structuredClone(original);
  const errors = [];
  const browser = await chromium.launch({ headless: true,
    ...(process.env.BROWSER_PATH ? { executablePath: process.env.BROWSER_PATH } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 375, height: 812 } });
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'http://workbench.test') return route.abort();
      if (url.pathname === '/api/skin') {
        if (route.request().method() === 'PUT') saved = route.request().postDataJSON();
        return route.fulfill({ json: saved });
      }
      if (url.pathname === '/skin') {
        const html = fs.readFileSync(path.join(__dirname, 'static/ib/workbench.html'), 'utf8')
          .replace('</head>', '<link id="ib-skin-css" rel="stylesheet" href="/static/ib/ib-skin.css"><script src="/static/ib/ib-skin.js"></script></head>');
        return route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
      }
      if (url.pathname.startsWith('/static/ib/')) {
        const file = path.join(__dirname, url.pathname);
        if (fs.existsSync(file)) return route.fulfill({ path: file });
      }
      return route.fulfill({ status: 404, body: '' });
    });
    const waitSaved = async () => {
      await page.waitForFunction(() => document.querySelector('#state').textContent === '已保存');
      assert.deepEqual(saved.desk, original.desk);
      assert.deepEqual(saved.slots, original.slots);
    };
    await page.goto('http://workbench.test/skin');
    await page.waitForFunction(() => window.IBSkin?.current().preset === 'sakura');
    await page.locator('#reset').click();
    await waitSaved();
    assert.equal(saved.preset, 'internal');
    assert.deepEqual(saved.vars, {});
    assert.deepEqual(saved.desk_look, { widget_bg: {}, icons: {} });
    await page.locator('[data-pane="code"]').click();
    await page.locator('#undo').click();
    await waitSaved();
    assert.equal(saved.preset, original.preset);
    assert.deepEqual(saved.vars, original.vars);
    assert.equal(saved.desk_look.widget_blur, 9);
    await page.locator('#undo').click();
    await waitSaved();
    assert.equal(saved.preset, 'internal');
    await page.reload();
    await page.waitForFunction(() => window.IBSkin?.current().preset === 'internal');
    assert.deepEqual(await page.evaluate(() => window.IBSkin.current().desk), original.desk);
    for (const tab of await page.locator('.tab').all()) {
      await tab.click();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    }
    if (process.env.SCREENSHOT_PATH) await page.screenshot({ path: process.env.SCREENSHOT_PATH, fullPage: true });
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});
