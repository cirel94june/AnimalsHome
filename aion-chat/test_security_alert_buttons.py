"""警报弹窗的两个按钮：服务器不回应、或拒绝信任来源时，弹窗也必须关掉。

用 Node 跑真实的 static/security-alert.js（给它一个最小的假页面），没有 Node 就跳过。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "static" / "security-alert.js"

HARNESS = r"""
const api = require(process.argv[2]);
function el(tag) {
  const node = { tagName: tag, children: [], id: '', textContent: '', hidden: false, style: {},
    _cls: new Set(),
    classList: { add(...c) { c.forEach(x => node._cls.add(x)); }, remove(...c) { c.forEach(x => node._cls.delete(x)); },
                 contains(c) { return node._cls.has(c); } },
    appendChild(c) { node.children.push(c); doc._all.push(c); return c; },
    replaceChildren() { node.children = []; },
    get offsetWidth() { return 1; }, set className(v) { node._cls = new Set(String(v).split(' ').filter(Boolean)); } };
  return node;
}
const doc = { _all: [], head: null, body: null,
  createElement: el,
  getElementById(id) { return doc._all.find(n => n.id === id) || null; },
  addEventListener() {}, removeEventListener() {} };
doc.head = el('head'); doc.body = el('body');
const mode = process.argv[3];
const calls = [];
const fetch = (url, opts) => {
  calls.push(url.split('/').pop());
  if (mode === 'hang') return new Promise(() => {});          // 服务器一直不回
  if (url.endsWith('/trust-source')) return Promise.resolve({ ok: false, status: 409 });
  return Promise.resolve({ ok: true, json: async () => ({}) });
};
class Audio { play() { return Promise.resolve(); } pause() {} addEventListener() {} }
const ui = api.createSecurityAlertUI({ document: doc, fetch, Audio });
(async () => {
  await ui.handleMessage({ type: 'security_alert', data: { alert_id: 'a1', source: 'tailscale', ip: '100.73.232.88' } });
  const overlay = doc.getElementById('securityAlertOverlay');
  const shownBefore = overlay.classList.contains('show');
  const button = process.argv[4];
  if (button === 'trust') ui.trustCurrentDevice(); else ui.dismissCurrentAlert();
  await new Promise(r => setTimeout(r, 50));
  console.log(JSON.stringify({ shownBefore, shownAfter: overlay.classList.contains('show'), calls }));
  process.exit(0);
})();
"""


def _run(tmp_path, mode, button):
    node = shutil.which("node")
    if not node:
        pytest.skip("没有 Node")
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    out = subprocess.run([node, str(harness), str(SCRIPT), mode, button],
                         capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("button", ["dismiss", "trust"])
def test_popup_closes_even_if_the_server_never_answers(tmp_path, button):
    result = _run(tmp_path, "hang", button)
    assert result["shownBefore"] is True and result["shownAfter"] is False


def test_untrustable_source_is_at_least_acknowledged(tmp_path):
    result = _run(tmp_path, "refuse", "trust")
    assert result["shownAfter"] is False
    assert result["calls"] == ["trust-source", "ack"]
