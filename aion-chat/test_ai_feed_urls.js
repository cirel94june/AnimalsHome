// Codex §111 L1：头像 / 附图只认本站目录，带 .. 之类绕出去的不用
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

for (const page of ['ai-feed.html', 'switches.html']) {
  const html = fs.readFileSync(path.join(__dirname, 'static', page), 'utf8').split('\r\n').join('\n');
  const start = html.indexOf('function safeLocalUrl');
  assert.ok(start >= 0, page);
  const source = html.slice(start, html.indexOf('\n  }\n', start) + 4);
  const ctx = {};
  vm.runInNewContext(source + '\nthis.safe = safeLocalUrl;', ctx);
  const ok = ['/uploads/a.jpg', '/skin-assets/x.webp', '/public/AIIcon.png'];
  const bad = ['/uploads/../../cdn-cgi/access/logout', '/uploads/%2e%2e/x', '/uploads/..%2fx', '/uploads\\..\\x',
    '//evil.example/a.png', 'https://evil.example/a.png', '/uploads/./a.jpg', '/api/x', '/uploads/a b.jpg', '/uploads/a").jpg'];
  ok.forEach(url => assert.strictEqual(ctx.safe(url, ['/public/', '/skin-assets/', '/static/', '/uploads/']), url, `${page} ${url}`));
  bad.forEach(url => assert.strictEqual(ctx.safe(url, ['/public/', '/skin-assets/', '/static/', '/uploads/']), '', `${page} ${url}`));
}
console.log('ai feed urls: ok');
