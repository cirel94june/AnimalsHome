"""11-05 云端窗口修的三处（审查简报 §155–§157）。行为已在真实页面里重放过，这里守住关键改动不被改回去。"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"


def test_stream_bubble_is_not_created_for_an_already_shown_message():
    js = (STATIC / "chatroom.js").read_text(encoding="utf-8")
    body = js[js.index("function startStreamingBubble(sender, id) {"):js.index("function crStripScheduleCommands")]
    guard = body.index('messagesEl.querySelector(`[data-msg-id="${id}"]`)')
    assert guard < body.index("const row = document.createElement('div');")


def test_phone_camera_toggle_says_who_can_see_and_whether_it_is_linked():
    html = (STATIC / "camera.html").read_text(encoding="utf-8")
    assert "开始外出监控</button>" not in html and "停止外出监控" not in html
    assert 'id="phoneLinkStatus"' in html
    assert re.search(r"PHONE_LINK_GRACE_MS\s*=\s*10000", html)


def test_stale_phone_status_cannot_undo_her_last_tap():
    """狐狸 §157 R1：点击之前发出的状态查询晚回来要丢掉；她点的关不能被服务器旧状态改回开。"""
    html = (STATIC / "camera.html").read_text(encoding="utf-8")
    sync = html[html.index("async function syncCamModalState() {"):]
    assert sync.index("const phoneGen = _phoneOpGen;") < sync.index('await api("GET", "/api/cam/status")')
    assert "applyPhoneServerState(status.phone_camera || {}, phoneGen);" in sync
    apply = html[html.index("function applyPhoneServerState(status, gen) {"):]
    assert "if (gen !== _phoneOpGen) return;" in apply[:200]
    assert "_phoneIntent === null ? _phoneServerArmed === true : _phoneIntent" in html
    assert "if (_phoneArmed && _phoneIntent !== false && bridge) {" in html
    assert "updatePhoneArmState" not in html
