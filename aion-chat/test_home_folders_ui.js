// 11-01 主页改版：图标收进分组文件夹，Dock 放常用四个（小猫：翻都翻不到、日记藏得深）
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const html = fs.readFileSync(path.join(__dirname, 'static', 'home.html'), 'utf8');
const pick = (start, end) => {
  const from = html.indexOf(start);
  assert.ok(from >= 0, start);
  return html.slice(from, html.indexOf(end, from) + end.length);
};

const context = {};
vm.createContext(context);
vm.runInContext(pick('const APPS = [', '];') + '\n' + pick('const FOLDERS = [', '];')
  + '\nconst FOLDERED_APP_IDS = new Set(FOLDERS.flatMap(folder => folder.apps));\n'
  + pick('function folderApps(folder) {', '\n}') + '\n'
  + pick('function getGridApps() {', '\n}') + '\n'
  + pick('function getGridAppIds() {', '\n}') + '\n'
  + pick('function getAppById(id) {', '\n}') + '\n'
  + pick('function hasLayoutPositions(positions) {', '\n}')
  + '\nthis.APPS = APPS; this.FOLDERS = FOLDERS;'
  + ' this.api = { getGridApps, getAppById, hasLayoutPositions, folderApps };', context);
const { APPS, FOLDERS, api } = context;
const plain = value => JSON.parse(JSON.stringify(value));  // vm 里的数组和这边不是同一个原型

// 常用四个：聊天、群聊、日记本、记忆库
assert.deepStrictEqual(plain(APPS.filter(app => app.dock).map(app => app.id)), ['chat', 'chatroom', 'diary', 'memory']);
assert.strictEqual(APPS.find(app => app.id === 'diary').url, '/diary');

// 每个不在 Dock 的应用正好进一个文件夹，文件夹里没有写错的 id
const seen = new Map();
FOLDERS.forEach(folder => folder.apps.forEach(id => {
  assert.ok(APPS.some(app => app.id === id), `文件夹 ${folder.name} 里的 ${id} 不存在`);
  assert.ok(!seen.has(id), `${id} 同时在两个文件夹里`);
  seen.set(id, folder.id);
}));
APPS.filter(app => !app.dock).forEach(app => assert.ok(seen.has(app.id), `${app.name} 没分进文件夹`));
assert.deepStrictEqual(plain(FOLDERS.find(f => f.id === 'folder-later').apps), ['whisper', 'dateTheater', 'hug']);

// 桌面上只剩文件夹（所有应用都分好了）
assert.deepStrictEqual(plain(api.getGridApps().map(item => item.id)), plain(FOLDERS.map(f => f.id)));
assert.ok(api.getAppById('folder-play').folder);
assert.strictEqual(api.getAppById('theater').url, '/theater');

// 旧排法（一个文件夹都没有）不再用；新排法认
assert.strictEqual(api.hasLayoutPositions({ theater: 0, album: 1 }), false);
assert.strictEqual(api.hasLayoutPositions({ 'folder-play': 0 }), true);
assert.strictEqual(api.hasLayoutPositions(null), false);

// 文件夹打开用 textContent 写标题；应用格子仍由 appHTML 生成（名字过 escapeAttr）
assert.ok(html.includes("document.getElementById('folderTitle').textContent = folder.name;"));
assert.ok(/id="folderOverlay" hidden/.test(html));
// 朋友圈红点挂在「家里的事」文件夹上，文件夹里那份不重复用这个 id
assert.ok(html.includes("app.id === 'moments' && !options.inFolder"));

// IB 皮肤：日记本有线条图标，文件夹里的小图标也换成线条图
const skin = fs.readFileSync(path.join(__dirname, 'static', 'ib', 'ib-skin.css'), 'utf8');
assert.ok(skin.includes('.app-icon[data-app-id="diary"] .icon-img img { content: url("/static/ib/icons/diary.svg"); }'));
assert.ok(skin.includes('.mini-icon[data-app-id="theater"] img'));
assert.ok(fs.existsSync(path.join(__dirname, 'static', 'ib', 'icons', 'diary.svg')));

// 点歌台：每首歌上直接有「删除」（原来藏在「管理」里）
const station = fs.readFileSync(path.join(__dirname, 'static', 'music-station.js'), 'utf8');
assert.ok(station.includes("remove.className = 'track-delete';"));
assert.ok(station.includes('deleteTracks([track.id])'));
assert.ok(station.includes('event.stopPropagation();'));

console.log('home folders: ok');
