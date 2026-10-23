"""Codex 登录态的模型目录：账号里有什么模型就列什么（新模型自动出现，下架的自动消失）。

数据来源是 Codex 自己缓存的 models_cache.json（Codex 运行时会更新它）；「刷新」会启动一次
`codex app-server` 只问模型列表（不调模型、不扣额度）。模型在下拉框里的名字沿用原来的写法：
gpt-6.1-sol → Codex-6.1-Sol，所以以前选过的 Codex-6.1-Sol 等照样认得。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

# 由本模块按目录加进 MODELS 的条目（写死在 config 里的不算）
_DYNAMIC_KEYS: set[str] = set()


def _cache_paths() -> list[Path]:
    import ai_providers
    return [Path(ai_providers._CODEX_HOME) / "models_cache.json",
            Path(ai_providers._CODEX_CHAT_HOME) / "models_cache.json"]


def list_codex_models() -> list[dict]:
    """账号当前能用的模型（隐藏的不列），带能否看图。没有缓存或缓存坏了返回空列表。"""
    files = [path for path in _cache_paths() if path.is_file()]
    if not files:
        return []
    newest = max(files, key=lambda path: path.stat().st_mtime_ns)
    try:
        data = json.loads(newest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    models = data.get("models") if isinstance(data, dict) else data
    result = []
    for item in models if isinstance(models, list) else []:
        if not isinstance(item, dict) or item.get("visibility") == "hide":
            continue
        slug = str(item.get("slug") or item.get("id") or "").strip()
        if not slug:
            continue
        result.append({
            "id": slug,
            "name": str(item.get("display_name") or slug),
            "vision": "image" in (item.get("input_modalities") or []),
        })
    return result


def available_slugs() -> set[str] | None:
    """账号里现在有的模型；还没有缓存（没登录过 / 没刷新过）时返回 None，表示「不知道」。"""
    models = list_codex_models()
    return {m["id"] for m in models} if models else None


def codex_model_key(slug: str) -> str:
    """gpt-6.1-sol → Codex-6.1-Sol（和原来写死的名字一致）。"""
    body = slug[4:] if slug.startswith("gpt-") else slug
    return "Codex-" + "-".join(part.capitalize() if part.isalpha() else part for part in body.split("-"))


def sync_models() -> list[str]:
    """把账号里的模型补进 MODELS（已有同名条目的不动），去掉以前补进来、现在账号里没有了的。"""
    from config import MODELS
    models = list_codex_models()
    if not models:
        return []
    wanted = {}
    for item in models:
        key = codex_model_key(item["id"])
        if key in MODELS and key not in _DYNAMIC_KEYS:
            continue  # config 里写死的，照旧
        wanted[key] = {"provider": "codex_cli", "model": item["id"], "vision": item["vision"],
                       "transport_mode": "safe_live"}
    for key in list(_DYNAMIC_KEYS):
        if key not in wanted:
            MODELS.pop(key, None)
            _DYNAMIC_KEYS.discard(key)
    for key, cfg in wanted.items():
        MODELS[key] = cfg
        _DYNAMIC_KEYS.add(key)
    return sorted(wanted)


async def refresh(timeout: float = 40.0) -> list[dict]:
    """启动一次 codex app-server，只问模型列表，让 Codex 更新它的缓存，再同步进 MODELS。"""
    import shutil
    import ai_providers
    if not ai_providers._CODEX_SCRIPT:
        raise RuntimeError("服务器上没有装 Codex")
    env = ai_providers._build_codex_chat_environment()
    process = await asyncio.create_subprocess_exec(
        shutil.which("node") or "node", ai_providers._CODEX_SCRIPT, "app-server", "--stdio",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env,
    )

    async def send(payload: dict) -> None:
        process.stdin.write((json.dumps(payload) + "\n").encode())
        await process.stdin.drain()

    async def ask() -> None:
        await send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"clientInfo": {"name": "aionshome-models", "version": "1"}}})
        while True:
            line = await process.stdout.readline()
            if not line:
                raise RuntimeError("Codex 没有回应")
            message = json.loads(line)
            if message.get("id") == 1:
                await send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
                await send({"jsonrpc": "2.0", "id": 2, "method": "model/list", "params": {}})
            elif message.get("id") == 2:
                if "error" in message:
                    raise RuntimeError("Codex 没能列出模型（可能需要重新登录）")
                return

    try:
        await asyncio.wait_for(ask(), timeout=timeout)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    sync_models()
    return list_codex_models()
