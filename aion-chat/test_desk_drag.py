"""桌面像真手机一样拉（11-08、11-09 小猫）：小组件和 App 混排，文件夹下拉关闭。"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
STATIC = ROOT / "static"


def test_widgets_live_in_the_home_grid():
    js = (STATIC / "ib" / "desk.js").read_text(encoding="utf-8")
    html = (STATIC / "home.html").read_text(encoding="utf-8")
    assert "window.HomeGrid.setWidgets(list)" in js and "function pickUp(" not in js
    assert "window.HomeGrid = {" in html and '.widget-slot[data-widget]' in html and "/static/home-grid.js" in html


def test_folder_can_be_pulled_down():
    html = (STATIC / "home.html").read_text(encoding="utf-8")
    assert "bindFolderPull();" in html and "translateY(70vh)" in html


def test_styles_follow_widgets_out_of_the_top_area():
    css = (STATIC / "ib" / "desk.css").read_text(encoding="utf-8")
    assert ":is(#ib-desk, .ib-desk-scope) .wgt" in css


@pytest.mark.skipif(not shutil.which("node"), reason="没有 node")
def test_solver():
    result = subprocess.run(["node", str(ROOT / "test_home_grid.js")], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_mixed_pages_start_at_the_top_row():
    html = (STATIC / "home.html").read_text(encoding="utf-8")
    assert ".app-pages.mixed .app-page { align-content: start; }" in html
    assert "homeWidgets.size ? rect.top +" in html
