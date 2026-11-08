"""桌面像真手机一样拉（11-08 小猫）：小组件长按拖动，文件夹下拉关闭。"""
from pathlib import Path

ROOT = Path(__file__).parent / "static"


def test_widgets_can_be_dragged_and_order_is_saved():
    js = (ROOT / "ib" / "desk.js").read_text(encoding="utf-8")
    assert "function pickUp(" in js and "desk.widgets = widgetsInOrder()" in js and "if (drag) { renderWaiting = true; return; }" in js


def test_folder_can_be_pulled_down():
    html = (ROOT / "home.html").read_text(encoding="utf-8")
    assert "bindFolderPull();" in html and "translateY(70vh)" in html
