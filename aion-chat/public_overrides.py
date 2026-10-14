"""
原作者随仓库带的几张图（他自己 AI 的脸、他本人的头像、视频来电图），页面里很多地方按文件名直接引用。
服务器 data/public_overrides/ 里放了同名文件时，就用那份代替——私人图片只放在服务器上，不进公开仓库。
自拍参考照另放 data/selfie/<AI 的 id>.jpg，每位 AI 各用自己的。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import DATA_DIR

OVERRIDE_DIR = DATA_DIR / "public_overrides"
SELFIE_DIR = DATA_DIR / "selfie"

# 只允许替换这些文件，其余 /public 照常
OVERRIDABLE = {
    "AIIcon.png", "codexicon.png", "UserIcon.png", "gropicon1.png",
    "视频来电头像.jpg", "视频通话背景.jpg", "视频背景照片.jpg",
}


def override_for(name: str) -> Path | None:
    if name not in OVERRIDABLE:
        return None
    path = OVERRIDE_DIR / name
    return path if path.is_file() else None


def selfie_reference(actor: str) -> Path | None:
    actor = str(actor or "aion").strip().lower() or "aion"
    if not actor.replace("_", "").isalnum():
        return None
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        path = SELFIE_DIR / f"{actor}{ext}"
        if path.is_file():
            return path
    return None


class OverridableStaticFiles(StaticFiles):
    """/public 的静态文件：可替换的那几张先看 data/public_overrides/ 里有没有。"""

    async def get_response(self, path: str, scope):
        override = override_for(path)
        if override is not None:
            return FileResponse(override, headers={"Cache-Control": "no-cache"})
        return await super().get_response(path, scope)
