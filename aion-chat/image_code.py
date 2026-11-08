"""代码画图（11-08 小猫）：AI 用平时聊天的模型写一份「画画清单」（JSON），家里用 Pillow 画成 PNG。

不用图片模型，也不执行模型写的任何代码：清单里只认下面这几种形状，别的都跳过。
画不出照片感，适合插画、图标、小漫画。
"""

from __future__ import annotations

import io
import math
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageColor, ImageDraw, ImageFont

MIN_SIDE, MAX_SIDE = 256, 1024
MAX_SHAPES = 400
MAX_POINTS = 200
MAX_TEXT = 120
SCALE = 2  # 先画两倍大再缩小，边缘才不锯齿

FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
)
LATIN_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_CJK = re.compile(r"[\u2e80-\u9fff\uff00-\uffef]")


def _cjk_font() -> str:
    return next((p for p in FONT_CANDIDATES if Path(p).exists()), "")


def instructions() -> str:
    text_rule = ("文字可以写中文。" if _cjk_font() else "服务器没有中文字体：画面上的文字只能用英文或数字，不要写中文。")
    return (
        "用「画画清单」画一张图。只输出一个 JSON 对象，不要解释，不要代码块。格式：\n"
        '{"width":768,"height":768,"background":"#fff4e0","shapes":[…]}\n'
        "width、height 在 256 到 1024 之间。background 是一个颜色，或两个颜色的数组（从上到下渐变）。\n"
        "shapes 按顺序一层层往上画，最多 400 个，尽量多用形状把细节画足。可用的形状：\n"
        '{"type":"rect","x":0,"y":0,"w":100,"h":50,"r":8,"fill":"#f00","stroke":"#000","width":2}\n'
        '{"type":"ellipse","cx":50,"cy":50,"rx":30,"ry":20,"fill":"#0af","stroke":"#000","width":2}\n'
        '{"type":"polygon","points":[[0,0],[10,0],[5,9]],"fill":"#0a0","stroke":"#000","width":1}\n'
        '{"type":"line","points":[[0,0],[10,10],[20,5]],"color":"#333","width":3}\n'
        '{"type":"text","x":10,"y":10,"text":"Hi","size":32,"color":"#000","align":"left"}\n'
        "颜色写 #RRGGBB，要半透明写 #RRGGBBAA。fill、stroke 不要的就不写。" + text_rule
    )


def _num(value: Any, low: float = -5000, high: float = 5000) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return max(low, min(high, float(value)))


def _color(value: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(value, str) or len(value) > 32:
        return None
    try:
        rgb = ImageColor.getcolor(value.strip(), "RGBA")
    except ValueError:
        return None
    return rgb if isinstance(rgb, tuple) and len(rgb) == 4 else None


def _points(value: Any) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for item in value[:MAX_POINTS] if isinstance(value, list) else []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            x, y = _num(item[0]), _num(item[1])
            if x is not None and y is not None:
                out.append((x * SCALE, y * SCALE))
    return out


def _background(size: tuple[int, int], value: Any) -> Image.Image:
    colors = value[:2] if isinstance(value, list) else [value]
    parsed = [c for c in (_color(v) for v in colors) if c]
    top = parsed[0] if parsed else (255, 255, 255, 255)
    bottom = parsed[1] if len(parsed) > 1 else top
    strip = Image.new("RGBA", (1, 256))
    for y in range(256):
        t = y / 255
        strip.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return strip.resize(size, Image.Resampling.BICUBIC)


def _font(size: float, text: str):
    pixels = max(8, min(400, int(size * SCALE)))
    path = _cjk_font() if _CJK.search(text) else (_cjk_font() or LATIN_FONT)
    if path:
        try:
            return ImageFont.truetype(path, pixels)
        except OSError:
            pass
    return ImageFont.load_default(pixels)


def _draw_one(canvas: Image.Image, shape: dict[str, Any]) -> bool:
    kind = shape.get("type")
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    pen = ImageDraw.Draw(layer)
    fill, stroke = _color(shape.get("fill")), _color(shape.get("stroke"))
    width = int(max(0, min(60, _num(shape.get("width")) or 0)) * SCALE)
    outline = stroke if width else None
    if kind == "rect":
        box = [_num(shape.get(k)) for k in ("x", "y", "w", "h")]
        if None in box or box[2] <= 0 or box[3] <= 0:
            return False
        x, y, w, h = (v * SCALE for v in box)
        pen.rounded_rectangle((x, y, x + w, y + h), radius=max(0, (_num(shape.get("r")) or 0) * SCALE),
                              fill=fill, outline=outline, width=width)
    elif kind == "ellipse":
        box = [_num(shape.get(k)) for k in ("cx", "cy", "rx", "ry")]
        if None in box or box[2] <= 0 or box[3] <= 0:
            return False
        cx, cy, rx, ry = (v * SCALE for v in box)
        pen.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=fill, outline=outline, width=width)
    elif kind == "polygon":
        points = _points(shape.get("points"))
        if len(points) < 3:
            return False
        pen.polygon(points, fill=fill, outline=outline, width=width)
    elif kind == "line":
        points = _points(shape.get("points"))
        color = _color(shape.get("color")) or stroke
        if len(points) < 2 or not color:
            return False
        pen.line(points, fill=color, width=max(1, width), joint="curve")
    elif kind == "text":
        text = str(shape.get("text") or "")[:MAX_TEXT]
        x, y = _num(shape.get("x")), _num(shape.get("y"))
        color = _color(shape.get("color")) or (0, 0, 0, 255)
        if not text or x is None or y is None or (_CJK.search(text) and not _cjk_font()):
            return False
        anchor = {"left": "la", "center": "ma", "right": "ra"}.get(shape.get("align"), "la")
        pen.text((x * SCALE, y * SCALE), text, font=_font(_num(shape.get("size"), 8, 200) or 24, text),
                 fill=color, anchor=anchor)
    else:
        return False
    canvas.alpha_composite(layer)
    return True


def render(plan: Any) -> bytes | None:
    """把清单画成 PNG。一个形状都画不出来就返回 None。"""
    if not isinstance(plan, dict) or not isinstance(plan.get("shapes"), list):
        return None
    width = int(_num(plan.get("width"), MIN_SIDE, MAX_SIDE) or 768)
    height = int(_num(plan.get("height"), MIN_SIDE, MAX_SIDE) or 768)
    canvas = _background((width * SCALE, height * SCALE), plan.get("background"))
    drawn = sum(1 for shape in plan["shapes"][:MAX_SHAPES] if isinstance(shape, dict) and _draw_one(canvas, shape))
    if not drawn:
        return None
    out = io.BytesIO()
    canvas.resize((width, height), Image.Resampling.LANCZOS).convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()
