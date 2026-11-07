"""家里的记忆 · 待确认页面：切换标签后晚到的旧请求不能覆盖当前列表（狐狸 §199 复审）。"""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(not shutil.which("node"), reason="没有 node")
def test_late_response_does_not_overwrite_the_selected_tab():
    script = Path(__file__).parent / "js_tests" / "hub_review_tab_race.js"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
