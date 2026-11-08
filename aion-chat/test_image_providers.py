"""画图三种方式（11-08 小猫）：OpenAI 兼容、Gemini Banana、代码画图。"""
import asyncio
import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image

import config
import image_code
import image_gen
from test_album import sample_image

KEY = "KEY_CANARY"


@pytest.fixture
def world(monkeypatch):
    calls, answers, saved = [], [], []

    class Response:
        def __init__(self, data):
            self._data, self.content, self.status_code = data, b"", 200

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response(answers.pop(0))

    async def save(data, **kwargs):
        saved.append((data, kwargs))
        return "album/x.png"
    monkeypatch.setattr(image_gen.httpx, "AsyncClient", Client)
    monkeypatch.setattr(image_gen, "save_generated_image", save)
    for key in ("image_gen_provider", "image_gen_base_url", "image_gen_api_key", "image_gen_model"):
        monkeypatch.setitem(config.SETTINGS, key, "")
    return calls, answers, saved


def _set(monkeypatch, provider, base="", key="", model=""):
    for name, value in (("provider", provider), ("base_url", base), ("api_key", key), ("model", model)):
        monkeypatch.setitem(config.SETTINGS, f"image_gen_{name}", value)


B64 = base64.b64encode(sample_image()).decode()


def test_gpt_image_uses_the_images_endpoint(world, monkeypatch):
    calls, answers, saved = world
    _set(monkeypatch, "openai", "https://relay.example", KEY, "gpt-image-2")
    answers.append({"data": [{"b64_json": B64}]})
    assert asyncio.run(image_gen.generate_image("一只猫")) == "album/x.png"
    url, kwargs = calls[0]
    assert url == "https://relay.example/v1/images/generations"
    assert kwargs["json"]["model"] == "gpt-image-2" and kwargs["headers"]["Authorization"] == f"Bearer {KEY}"
    assert saved[0][0] == sample_image()


def test_gpt_image_with_reference_uses_edits(world, monkeypatch, tmp_path):
    calls, answers, _ = world
    _set(monkeypatch, "openai", "https://relay.example/v1", KEY, "gpt-image-2")
    ref = tmp_path / "a.jpg"
    ref.write_bytes(sample_image())
    answers.append({"data": [{"b64_json": B64}]})
    asyncio.run(image_gen.generate_image("合照", references=[{"name": "小猫", "path": str(ref)}]))
    assert calls[0][0] == "https://relay.example/v1/images/edits" and calls[0][1]["files"]


def test_other_models_still_use_chat(world, monkeypatch):
    calls, answers, _ = world
    _set(monkeypatch, "openai", "https://relay.example/v1", KEY, "nano-banana")
    answers.append({"choices": [{"message": {"content": f"![x](data:image/png;base64,{B64})"}}]})
    assert asyncio.run(image_gen.generate_image("猫")) == "album/x.png"
    assert calls[0][0] == "https://relay.example/v1/chat/completions"


def test_gemini_official_with_its_own_key_and_model(world, monkeypatch):
    calls, answers, saved = world
    _set(monkeypatch, "gemini", "", KEY, "gemini-banana-pro")
    answers.append({"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": B64}}]}}]})
    assert asyncio.run(image_gen.generate_image("猫")) == "album/x.png"
    assert calls[0][0] == ("https://generativelanguage.googleapis.com/v1beta/models/gemini-banana-pro"
                           f":generateContent?key={KEY}")
    assert saved[0][1]["model"] == "gemini-banana-pro"


def test_gemini_through_a_relay(world, monkeypatch):
    calls, answers, _ = world
    _set(monkeypatch, "gemini", "https://relay.example/", KEY, "")
    answers.append({"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": B64}}]}}]})
    asyncio.run(image_gen.generate_image("猫"))
    assert calls[0][0].startswith(f"https://relay.example/v1beta/models/{image_gen.IMAGE_GEN_MODEL}:generateContent")


def test_auto_keeps_the_old_behaviour(world, monkeypatch):
    calls, answers, _ = world
    _set(monkeypatch, "", "https://relay.example/v1", KEY, "some-model")
    answers.append({"choices": [{"message": {"content": f"![x](data:image/png;base64,{B64})"}}]})
    asyncio.run(image_gen.generate_image("猫"))
    assert calls[0][0].endswith("/chat/completions")


# ── 代码画图 ──

PLAN = {"width": 300, "height": 260, "background": ["#ffeecc", "#88aaff"], "shapes": [
    {"type": "rect", "x": 10, "y": 10, "w": 100, "h": 50, "r": 8, "fill": "#ff0000", "stroke": "#000", "width": 2},
    {"type": "ellipse", "cx": 150, "cy": 100, "rx": 30, "ry": 20, "fill": "#00aaff80"},
    {"type": "polygon", "points": [[200, 10], [280, 10], [240, 80]], "fill": "#00aa00"},
    {"type": "line", "points": [[0, 190], [300, 150]], "color": "#333", "width": 3},
    {"type": "text", "x": 20, "y": 150, "text": "Hi", "size": 24, "color": "#000"},
]}


def test_render_makes_a_png_of_the_right_size():
    image = Image.open(io.BytesIO(image_code.render(PLAN)))
    assert image.format == "PNG" and image.size == (300, 260)
    assert image.getpixel((50, 30))[0] > 200 and image.getpixel((50, 30))[1] < 60  # 红色方块画上了


def test_size_is_clamped():
    plan = {"width": 99999, "height": 1, "shapes": [{"type": "rect", "x": 0, "y": 0, "w": 5, "h": 5, "fill": "#000"}]}
    assert Image.open(io.BytesIO(image_code.render(plan))).size == (image_code.MAX_SIDE, image_code.MIN_SIDE)


@pytest.mark.parametrize("plan", [None, [], {}, {"shapes": "x"}, {"shapes": []}, {"shapes": [{"type": "script", "src": "x"}]},
                                  {"shapes": [{"type": "rect", "x": "a", "y": 0, "w": 1, "h": 1}]}])
def test_nothing_drawable_returns_none(plan):
    assert image_code.render(plan) is None


def test_bad_shapes_are_skipped_not_fatal():
    plan = {"shapes": [{"type": "rect", "x": float("inf"), "y": 0, "w": 1, "h": 1},
                       {"type": "eval", "code": "import os"},
                       {"type": "ellipse", "cx": 50, "cy": 50, "rx": 10, "ry": 10, "fill": "not-a-color"},
                       {"type": "line", "points": [[0, 0], [9, 9]], "color": "#000", "width": 2}]}
    assert image_code.render(plan)


def test_code_mode_uses_the_ais_own_model_and_saves(world, monkeypatch):
    import ai_providers
    calls, _, saved = world
    _set(monkeypatch, "code")
    monkeypatch.setattr(image_gen, "_code_model", lambda who: f"model-of-{who}")
    seen = {}

    async def stream(messages, model_key, meta):
        seen["model"], seen["ask"] = model_key, messages[0]["content"]
        yield f"{ai_providers.CLI_STATUS_PREFIX}想一想"
        yield json.dumps(PLAN)
    monkeypatch.setattr(ai_providers, "stream_ai", stream)
    assert asyncio.run(image_gen.generate_image("一座小房子", source_identity="ai3")) == "album/x.png"
    assert seen["model"] == "model-of-ai3" and "一座小房子" in seen["ask"] and calls == []
    assert saved[0][1]["kind"] == "draw" and saved[0][0].startswith(b"\x89PNG")


def test_code_mode_failure_is_none(world, monkeypatch):
    import ai_providers
    _, _, saved = world
    _set(monkeypatch, "code")
    monkeypatch.setattr(image_gen, "_code_model", lambda who: "m")

    async def stream(messages, model_key, meta):
        yield "我不会画"
    monkeypatch.setattr(ai_providers, "stream_ai", stream)
    assert asyncio.run(image_gen.generate_image("猫")) is None and saved == []


def test_settings_page_wired():
    root = Path(__file__).parent
    html = (root / "static" / "settings.html").read_text(encoding="utf-8")
    assert 'id="imageGenProviderInput"' in html and "image_gen_provider: $(" in html
    api = (root / "routes" / "settings.py").read_text(encoding="utf-8")
    assert 'SETTINGS["image_gen_provider"]' in api
