// 全页巡检（docs/验收清单.md）：在 AionsHome 任意页面的浏览器控制台粘贴运行。
// 按手机宽度（375×812）把主页上列出的每个页面放进一个看不见的框里打开，记下：
// 横向溢出多少像素、坏掉的图片、页面上出现的「错误 / 失败 / undefined / NaN」字样、脚本报错。
// 只打开页面、不点任何按钮，不会发消息或改设置。跑完把结果打印成表格，也放在 window.__sweep.out 里。
(async () => {
  const pages = ['/contacts', '/chatroom?view=group', '/memory', '/hub-memory', '/moments', '/music-station',
    '/album', '/taobao', '/lounge-friends', '/xhs-lite', '/wishes', '/worldbook', '/theater', '/english-corner',
    '/date-theater', '/toys', '/schedule', '/settings', '/capabilities', '/app-supervision', '/camera',
    '/monitor-logs', '/location', '/activity-logs', '/family-dynamics', '/health', '/hug', '/reading', '/gift',
    '/fund', '/playground', '/mcp-tools', '/travel', '/usage', '/repair', '/tts-test'];
  window.__sweep = { done: false, out: [] };
  for (const p of pages) {
    const frame = document.createElement('iframe');
    frame.style.cssText = 'position:fixed;left:-2000px;top:0;width:375px;height:812px;border:0';
    document.body.appendChild(frame);
    const errors = [];
    await new Promise(resolve => { frame.onload = resolve; frame.src = p; setTimeout(resolve, 8000); });
    try { frame.contentWindow.addEventListener('error', e => errors.push(String(e.message).slice(0, 80))); } catch (e) {}
    await new Promise(r => setTimeout(r, 2500));
    const row = { page: p };
    try {
      const doc = frame.contentDocument;
      const text = doc.body?.innerText || '';
      row.title = doc.title;
      row.overflowX = Math.max(0, doc.documentElement.scrollWidth - 375);
      row.brokenImages = [...doc.images].filter(i => i.complete && i.naturalWidth === 0 && i.getAttribute('src'))
        .map(i => i.getAttribute('src').split('/').pop().slice(0, 30)).slice(0, 4).join(', ');
      row.errorText = [...new Set(text.match(/[^\n]{0,12}(错误|失败|出错|异常|Error|undefined|NaN|\[object|null)[^\n]{0,24}/g) || [])]
        .slice(0, 3).join(' | ');
    } catch (e) { row.loadError = String(e).slice(0, 80); }
    row.scriptErrors = errors.join(' | ');
    window.__sweep.out.push(row);
    frame.remove();
  }
  window.__sweep.done = true;
  console.table(window.__sweep.out);
})();
