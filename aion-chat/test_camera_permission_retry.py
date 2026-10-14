"""监控页：相机权限被拒绝后不再自动重试（不会一直弹授权框），用户点按钮才再问一次（Codex 复查 C1）。

用 Node 跑 camera.html 里真实的页面脚本，给一个最小的假页面和一个一直拒绝的假相机；没有 Node 就跳过。
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parent / "static" / "camera.html"

HARNESS = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n;\n');
const els = {};
function el(id) {
  if (!els[id]) els[id] = { id, value: '', textContent: '', className: '', style: {}, checked: false, disabled: false,
    innerHTML: '', dataset: {}, classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {}, appendChild() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    getContext() { return null; }, setAttribute() {}, removeAttribute() {} };
  return els[id];
}
const timers = [];
const sandbox = {
  console, Date, JSON, Math, Promise, Number, String, Array, Object, parseFloat, parseInt, isNaN, Error,
  document: { getElementById: el, addEventListener() {}, querySelector: () => el('_q'), querySelectorAll: () => [],
              documentElement: { dataset: {} }, hidden: false, createElement: () => el('_c' + Math.random()) },
  setTimeout: (fn) => { timers.push(fn); return timers.length; }, clearTimeout() {}, setInterval() { return 0; },
  clearInterval() {}, requestAnimationFrame() { return 0; }, cancelAnimationFrame() {},
  fetch: () => Promise.reject(new Error('offline')), localStorage: { getItem() { return null; }, setItem() {} },
  navigator: { userAgent: 'test' }, location: { href: '' }, alert() {}, confirm() { return true; },
  PhoneCameraPreviewPolicy: { isStale: () => false },
};
sandbox.window = sandbox;
// common.js 里的公共函数（页面用 <script src> 引入）
sandbox.$ = el; sandbox.api = () => Promise.reject(new Error('offline')); sandbox.showToast = () => {};
sandbox.connectCommonWS = () => {}; sandbox.onCommonWS = () => {};
sandbox.addEventListener = () => {}; sandbox.removeEventListener = () => {};
sandbox.Image = function () { return el('_img' + Math.random()); };
const calls = { start: 0, ask: 0 };
let granted = false;
sandbox.AionPhoneCamera = { getCapabilities: () => '{}', requestPreview: () => true };
sandbox.AionCamera = {
  setZoom() {}, isRunning: () => false, getFacing: () => 'environment', stop() {}, getLastFrameAt: () => 0,
  start() { calls.start++; return granted; },
  hasPermission: () => granted, isPermissionDenied: () => !granted,
  requestPermissionAgain() { calls.ask++; },
};
const vm = require('vm');
vm.createContext(sandbox);
try { vm.runInContext(scripts, sandbox); } catch (e) { console.error('PAGE INIT:', e.message); }
(async () => {
  await new Promise(r => setImmediate(r));
  vm.runInContext('_activeSource = "phone"; _phonePreviewRestartAt = 0; phonePreviewDemanded = () => true;', sandbox);
  vm.runInContext('phonePreviewNow()', sandbox);
  // 把页面安排的所有重试都跑掉（每轮都清零退避时间）
  for (let round = 0; round < 5; round++) {
    const pending = timers.splice(0);
    for (const fn of pending) { vm.runInContext('_phonePreviewRestartAt = 0', sandbox); try { fn(); } catch (e) {} }
  }
  const afterRetries = calls.start;
  const buttonShown = el('phonePermBtn').style.display === '';
  vm.runInContext('askCameraPermissionAgain()', sandbox);
  granted = true;
  vm.runInContext('onAionCameraPermissionResult(true)', sandbox);
  console.log(JSON.stringify({ afterRetries, buttonShown, asked: calls.ask, startsAfterGrant: calls.start - afterRetries,
                                message: el('camNoSignal').textContent }));
  process.exit(0);
})();
"""


def test_denied_camera_is_not_retried_until_the_user_asks(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("没有 Node")
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    out = subprocess.run([node, str(harness), str(PAGE)], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert result["afterRetries"] == 1          # 只试了一次，没有一轮轮自动重试
    assert result["buttonShown"] is True        # 给出「重新允许相机」按钮
    assert result["asked"] == 1                 # 用户点了才再问
    assert result["startsAfterGrant"] >= 1      # 允许后自动打开
