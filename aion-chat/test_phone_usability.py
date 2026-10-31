"""2026-10-28 小猫手机上真用时发现：入口页滑不下去、窗口删不掉、私聊里看不到思考过程。"""

import re
from pathlib import Path

STATIC = Path(__file__).parent / "static"


def _read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_long_pages_can_scroll_on_phone():
    """common.css 把 body 设成一屏高、不滚动；内容比屏幕长的页面要改回来，否则手机上滑不下去。"""
    for css, cls in (("contacts.css", "contacts-page"), ("hub-memory.css", "hub-memory-page")):
        rule = re.search(r"body\." + cls + r"\s*\{([^}]*)\}", _read(css)).group(1)
        assert "overflow-y: auto" in rule and "height: auto" in rule, css


def test_windows_can_be_deleted_after_asking():
    js = _read("contacts.js")
    remove = js[js.index("async function remove("):js.index("// ── 画页面 ──")]
    assert remove.index("confirm(") < remove.index("api('DELETE'")  # 先问，再删
    assert "/api/conversations/" in remove and "/api/chatroom/rooms/" in remove
    assert "window-remove" in js


def test_thinking_button_is_always_visible():
    """💭 不能放在「点一下消息才出现」的那组按钮（.msg-feedback-actions）里。"""
    chat = _read("chat.js")
    feedback = chat[chat.index("const feedbackHtml = isAssistant"):chat.index("const reasoningHtml")]
    assert "openMsgReasoning" not in feedback
    assert "${escHtml(roleLabel)}</span>${reasoningHtml}" in chat  # 紧跟名字，点消息时不会被挤走
    room = _read("chatroom.js")
    feedback = room[room.index("function crMsgFeedbackHtml"):room.index("function crReasoningChipHtml")]
    assert "openCrMsgReasoning" not in feedback
    assert "${esc(name)}</span>${msg ? crReasoningChipHtml(msg) : ''}" in room


def test_thinking_popover_opens_below_when_no_room_above():
    for name in ("chat.js", "chatroom.js"):
        js = _read(name)
        assert "goBelow" in js and "window.innerHeight * 0.6" in js, name


def test_memory_detail_sheet_hides_the_list_under_glass_skins():
    """10-30 小猫截图：IB 皮肤底色只有 35% 不透明，手机上「原子记忆详情」面板透出底下的列表，字叠在一起。"""
    css = _read("memory-library.css")
    sheet = re.search(r"\.detail\{display:none;position:fixed[^}]*\}", css).group(0)
    assert "backdrop-filter:blur(" in sheet and "background:var(--surface)" in sheet
    dialog = re.search(r"\ndialog\{[^}]*\}", css).group(0)
    assert "backdrop-filter:blur(" in dialog


def test_persona_page_is_solid_on_phone_under_glass_skins():
    """10-31 小猫截图：手机上群聊里的「Lucien 人设」页去掉了毛玻璃，IB 底色又半透明，底下聊天的字透上来。"""
    css = _read("chatroom.css")
    block = re.search(r"@media \(max-width: 760px\) and \(pointer: coarse\) \{\s*\.persona-page-overlay\.active[^}]*\}\s*\.persona-page-panel \{[^}]*\}", css).group(0)
    assert "background: var(--ib-base, var(--bg));" in block
    assert "--ib-base: #" in _read("ib/ib-skin.css")
