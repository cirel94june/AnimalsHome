"""小剧场（11-08 小猫）：看不懂怎么玩，模型藏得太深。"""
from pathlib import Path

JS = (Path(__file__).parent / "static" / "theater-studio.js").read_text(encoding="utf-8")


def test_new_story_lets_you_pick_model_and_persona():
    assert 'id="newStoryModel"' in JS and 'id="newStoryPersona"' in JS
    assert "model:$('newStoryModel').value" in JS


def test_there_is_a_how_to_play():
    assert "howto()" in JS and JS.count("Studio.howto()") == 2
