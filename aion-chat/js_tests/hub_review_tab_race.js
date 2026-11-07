const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const app = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(app, 'static/ib/hub-review.html'), 'utf8');
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)][0][1];

class Element {
  constructor() { this.children = []; this.dataset = {}; this.attrs = {}; this.textContent = ''; this._html = ''; }
  set innerHTML(value) { this._html = value; this.children = []; }
  get innerHTML() { return this._html; }
  appendChild(value) { this.children.push(value); }
  addEventListener() {}
  setAttribute(key, value) { this.attrs[key] = value; }
}
const list = new Element();
const pendingTab = new Element(); pendingTab.dataset.status = 'pending';
const reviewTab = new Element(); reviewTab.dataset.status = 'review_after';
const elements = {list, 'n-pending': new Element(), 'n-review': new Element()};
const requests = [];
const document = {
  getElementById: id => elements[id],
  querySelectorAll: () => [pendingTab, reviewTab],
  createElement: () => new Element(), body: new Element(),
};
function fetch(url) {
  if (url.endsWith('/summary')) return Promise.resolve({status: 200, json: async () => ({waiting: 1, review_after: 1})});
  return new Promise(resolve => requests.push({url, resolve}));
}
const context = vm.createContext({document, fetch, console, setTimeout, confirm: () => true});
vm.runInContext(script, context, {filename: 'hub-review.html'});
async function flush() { for (let i = 0; i < 5; i++) await Promise.resolve(); }
function reply(index, item) {
  requests[index].resolve({status: 200, json: async () => ({items: [item]})});
}

(async () => {
  assert.match(requests[0].url, /status=pending/);
  const inOrder = process.argv.includes('--in-order');
  if (inOrder) {
    reply(0, {id: 'pending-item', status: 'pending', content: 'PENDING_CONTENT'});
    await flush();
  }
  reviewTab.onclick();
  assert.match(requests[1].url, /status=review_after/);
  reply(1, {id: 'review-item', status: 'review_after', content: 'REVIEW_AFTER_CONTENT'});
  await flush();
  assert.match(list.children[0].innerHTML, /REVIEW_AFTER_CONTENT/);
  if (!inOrder) {
    reply(0, {id: 'pending-item', status: 'pending', content: 'PENDING_CONTENT'});
    await flush();
  }
  assert.equal(reviewTab.attrs['aria-selected'], 'true');
  console.log('selected tab:', reviewTab.dataset.status);
  console.log('visible card:', list.children[0].innerHTML);
  assert.match(list.children[0].innerHTML, /REVIEW_AFTER_CONTENT/,
    'late pending response overwrote the selected review_after list');
})().catch(error => { console.error(error); process.exitCode = 1; });
