"""模型下拉框只显示这台服务器用得了的线路（中转站总显示；内置线路要有 Key；命令行线路要装了程序）。"""

import config


def test_model_usable_rules(monkeypatch):
    monkeypatch.setitem(config.SETTINGS, "siliconflow_key", "")
    monkeypatch.setitem(config.SETTINGS, "gemini_key", "g")
    assert config.model_usable({"provider": "custom_openai"})
    assert not config.model_usable({"provider": "siliconflow"})
    assert config.model_usable({"provider": "gemini"})
    import ai_providers
    monkeypatch.setattr(ai_providers, "_CODEX_SCRIPT", None)
    assert not config.model_usable({"provider": "codex_cli"})
    monkeypatch.setattr(ai_providers, "_CODEX_SCRIPT", "/x/codex.js")
    assert config.model_usable({"provider": "codex_cli"})


def test_selectable_list_hides_unusable_but_visible_list_does_not(monkeypatch):
    monkeypatch.setitem(config.MODELS, "没Key的内置", {"provider": "aipro", "model": "x"})
    monkeypatch.setitem(config.SETTINGS, "aipro_key", "")
    assert "没Key的内置" in dict(config.iter_visible_models())        # 兜底选模型的逻辑不受影响
    assert "没Key的内置" not in dict(config.iter_selectable_models())  # 下拉框里不出现
