const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// §63 复审 M3：一次巡游没结束时，保存配置重建出来的座位按钮也不能点，runOnce 也不会重入。
const html = fs.readFileSync('static/xhs-lite.html', 'utf8');
const pick = (start, end) => html.slice(html.indexOf(start), html.indexOf(end, html.indexOf(start)));
const code = pick('let xhsRunning = false;', 'function collectPayload()') + '\n' +
  pick('async function runOnce(actor)', 'loadConfig().catch');

function element(tag) {
  return {tag, children: [], dataset: {}, className: '', textContent: '', disabled: false, checked: false, type: '',
    listeners: {}, append(...xs) { this.children.push(...xs); }, replaceChildren(...xs) { this.children = xs; },
    addEventListener(name, fn) { this.listeners[name] = fn; }};
}
const nodes = {seatActorCards: element('span'), seatRunBtns: element('span'),
  runPrimaryBtn: element('button'), runSecondaryBtn: element('button')};
const posts = [];
let release;
const context = {
  currentConfig: {actor_labels: {aion: '小克', connor: 'Lucien', ai3: '<img src=x onerror=alert(1)>'}, actors: {}},
  $: id => nodes[id],
  actorName: id => context.currentConfig.actor_labels[id],
  document: {
    createElement: element,
    createTextNode: text => ({text}),
    querySelectorAll: sel => sel === '[data-seat-run]' ? nodes.seatRunBtns.children : [],
  },
  saveConfig: async () => { context.renderSeatActors(); return true; },  // 保存会重建座位按钮
  setStatus() {},
  api: (method, url, body) => { posts.push(body.actor); return new Promise(resolve => { release = resolve; }); },
};
vm.createContext(context);
vm.runInContext(code + '\nthis.renderSeatActors = renderSeatActors; this.runOnce = runOnce;', context);

(async () => {
  context.renderSeatActors();
  const seatBtn = nodes.seatRunBtns.children[0];
  assert.equal(seatBtn.textContent, '让<img src=x onerror=alert(1)>跑一次');  // 名字是纯文本
  const first = context.runOnce('ai3');
  await new Promise(r => setImmediate(r));
  assert.equal(posts.length, 1);
  const rebuilt = nodes.seatRunBtns.children[0];
  assert.notEqual(rebuilt, seatBtn, 'saveConfig rebuilds the seat buttons');
  assert.equal(rebuilt.disabled, true, 'rebuilt seat button stays disabled while a run is pending');
  await context.runOnce('ai3');  // 直接再点也不会重入
  assert.equal(posts.length, 1);
  release({ok: true});
  await first;
  assert.equal(nodes.seatRunBtns.children[0].disabled, false);
  console.log('xhs seat run buttons: no re-entry while pending, names stay text OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
