"""设置页整理（11-08 小猫）：用不上的收起来，不删；每把钥匙写清楚干什么用。"""
from pathlib import Path

HTML = (Path(__file__).parent / "static" / "settings.html").read_text(encoding="utf-8")


def test_rarely_used_cards_are_marked_not_removed():
    assert HTML.count("<details data-rarely") == 3
    assert HTML.count('<div class="field" data-rarely>') == 2  # 11-08 小猫：两个 Gemini Key 暂时用不上
    for field in ("minimaxTtsKeyInput", "elevenKey", "luckin"):
        assert field in HTML


def test_every_key_is_explained():
    for words in ("用官方 Gemini 模型", "只给后台小任务用", "硅基流动的语音", "AI 联网搜索用的", "填了能放 VIP 歌"):
        assert words in HTML


def test_image_settings_do_not_require_all_three_for_gemini():
    api = (Path(__file__).parent / "routes" / "settings.py").read_text(encoding="utf-8")
    assert 'image_provider in ("", "openai") and any(image_config.values())' in api


def test_fold_runs_before_the_page_reads_fields():
    assert HTML.index("rarelyUsedSettings") < HTML.index("connectCommonWS();")
