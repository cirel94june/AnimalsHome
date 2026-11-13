'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');

test('wallets show each owner, transfer direction, empty/error states and ignore stale responses', async () => {
  const actors = ['aion', 'connor', 'ai3'].map((id, i) => ({id, enabled: true, name: ['小克', 'Lucien', '狗蛋'][i], avatar: '', color: ['#7598c6', '#bb967b', '#7caa9d'][i]}));
  const browser = await chromium.launch({headless: true, ...(process.env.BROWSER_PATH ? {executablePath: process.env.BROWSER_PATH} : {})});
  try {
    const page = await browser.newPage({viewport: {width: 375, height: 812}});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    let failBalance = false, held, delayAion = false;
    const outgoing = {id: 'out', record_type: 'seat_wallet_ai:ai3', amount: -20, description: '狗蛋转给小猫 20元', created_at: 1794528000};
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      assert.equal(route.request().method(), 'GET', 'viewing wallets must never write money');
      if (url.origin !== 'http://wallets.test') return route.abort();
      if (url.pathname === '/api/skin') return route.fulfill({json: {enabled: true}});
      if (url.pathname === '/api/actors') return route.fulfill({json: {user: {name: '小猫'}, actors: [...actors, {id: 'ai4', name: '空位', enabled: false}]}});
      if (url.pathname.endsWith('/balance')) {
        if (failBalance && url.pathname.includes('connor')) return route.fulfill({status: 503, body: ''});
        return route.fulfill({json: {balance: url.pathname.includes('ai3') ? 80 : 100}});
      }
      if (url.pathname.endsWith('/transactions')) {
        if (delayAion && url.pathname === '/api/wallet/transactions') { held = route; return; }
        const rows = url.pathname.includes('ai3') ? [outgoing, {id: 'in', record_type: 'seat_wallet_user:ai3', amount: 100, description: '<img src=x onerror="window.pwned=true">', created_at: 1794527000}] : [];
        return route.fulfill({json: rows});
      }
      if (url.pathname === '/wallets') {
        const html = fs.readFileSync(path.join(__dirname, 'static/wallets.html'), 'utf8').replace('</head>', '<link id="ib-skin-css" rel="stylesheet" href="/static/ib/ib-skin.css"><script src="/static/ib/ib-skin.js"></script></head>');
        return route.fulfill({contentType: 'text/html; charset=utf-8', body: html});
      }
      if (url.pathname.startsWith('/static/')) {
        const file = path.join(__dirname, url.pathname);
        if (fs.existsSync(file)) return route.fulfill({path: file});
      }
      return route.fulfill({status: 404, body: ''});
    });
    await page.goto('http://wallets.test/wallets');
    await page.waitForFunction(() => document.querySelectorAll('.wallet').length === 3);
    assert.match(await page.locator('#transactions').innerText(), /还没有收支记录/);
    await page.getByRole('button', {name: /狗蛋的口袋/}).click();
    await page.waitForFunction(() => document.querySelectorAll('.tx').length === 2);
    assert.match(await page.locator('#transactions').innerText(), /狗蛋 → 小猫/);
    assert.match(await page.locator('#transactions').innerText(), /小猫 → 狗蛋/);
    assert.match(await page.locator('#transactions').innerText(), /−20.00/);
    assert.equal(await page.evaluate(() => !!window.pwned), false);
    assert.equal(await page.locator('#transactions img').count(), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    if (process.env.SCREENSHOT_PATH) {
      await page.locator('.description').last().evaluate(el => { el.textContent = '测试入账 · 存一份小心意'; });
      await page.screenshot({path: process.env.SCREENSHOT_PATH, fullPage: true});
    }
    delayAion = true;
    await page.getByRole('button', {name: /小克的口袋/}).click();
    await page.waitForFunction(() => document.querySelector('#ledger-title').textContent === '小克的小钱包');
    for (let i = 0; !held && i < 30; i++) await page.waitForTimeout(50);
    assert.ok(held);
    await page.getByRole('button', {name: /狗蛋的口袋/}).click();
    await page.waitForFunction(() => document.querySelectorAll('.tx').length === 2);
    await held.fulfill({json: [{...outgoing, description: 'STALE_AION'}]});
    await page.waitForTimeout(200);
    assert.doesNotMatch(await page.locator('#transactions').innerText(), /STALE_AION/);
    failBalance = true;
    await page.getByRole('button', {name: '刷新', exact: true}).click();
    await page.waitForFunction(() => document.querySelector('#status').textContent.includes('有的钱包'));
    const failed = page.getByRole('button', {name: /Lucien的口袋/});
    assert.match(await failed.innerText(), /暂时读不到/);
    assert.doesNotMatch(await failed.innerText(), /0\.00/);
    assert.match(await page.getByRole('button', {name: /狗蛋的口袋/}).innerText(), /80\.00/);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
