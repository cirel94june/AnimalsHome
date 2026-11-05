"""朋友圈看大图：本页盖一层，不开新窗口（11-05 小猫：App 里点开大图黑屏）。"""
from pathlib import Path

PAGE = (Path(__file__).parent / "static" / "moments.html").read_text(encoding="utf-8")


def _func(name):
    start = PAGE.index(f"function {name}(")
    return PAGE[start:PAGE.index("\n}\n", start)]


def test_opening_an_image_never_opens_a_new_window():
    body = _func("openMomentImage")
    assert "window.open" not in body
    assert "moment-viewer" in body
    assert "closeMomentImage" in body


def test_image_url_is_not_pasted_into_html():
    # 用 DOM 赋值，不把地址拼进 innerHTML
    body = _func("openMomentImage")
    assert "innerHTML" not in body
    assert "img.src = url" in body


def test_viewer_can_be_closed_and_saving_never_opens_a_window():
    assert "key === 'Escape'" in PAGE
    assert "window.open" not in _func("saveMomentImage")
    assert ".moment-viewer {" in PAGE and "position:fixed" in PAGE
