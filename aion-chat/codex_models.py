"""Codex 登录态的模型目录：账号里有什么模型就列什么（新模型自动出现，下架的自动消失）。

数据来源是 Codex 自己缓存的 models_cache.json（Codex 运行时会更新它）；「刷新」会启动一次
`codex app-server` 只问模型列表（不调模型、不扣额度）。模型在下拉框里的名字沿用原来的写法：
gpt-6.1-sol → Codex-6.1-Sol，所以以前选过的 Codex-6.1-Sol 等照样认得。

目录三种状态：读到了且有模型 / 读到了但明确是空的 / 读不到（没有、坏了）。读不到时用上一份
可信目录（内存里的，或存在 data/ 里的副本），不把「暂时读不到」当成「模型都下架了」。
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path

# 本模块补进 MODELS 的条目：名字 → 补进去的那个 dict 对象（只认对象本身，同名的别的线路不动）
_OWNED: dict[str, dict] = {}
_TRUSTED: list[dict] | None = None  # 上一份读到的可信目录（可能是空列表）


def _cache_paths() -> list[Path]:
    import ai_providers
    return [Path(ai_providers._CODEX_HOME) / "models_cache.json",
            Path(ai_providers._CODEX_CHAT_HOME) / "models_cache.json"]


def _trusted_copy_path() -> Path:
    from config import DATA_DIR
    return Path(DATA_DIR) / "codex_models_catalog.json"


def _parse(data) -> list[dict] | None:
    models = data.get("models") if isinstance(data, dict) else data
    if not isinstance(models, list):
        return None
    result = []
    for item in models:
        if not isinstance(item, dict) or item.get("visibility") == "hide":
            continue
        slug = str(item.get("slug") or item.get("id") or "").strip()
        if not slug:
            continue
        result.append({
            "id": slug,
            "name": str(item.get("display_name") or item.get("name") or slug),
            "vision": bool(item.get("vision")) or "image" in (item.get("input_modalities") or []),
        })
    return result


def _read_cache() -> list[dict] | None:
    """读 Codex 的缓存：读到了返回列表（可能是空的），没有或坏了返回 None。"""
    files = [path for path in _cache_paths() if path.is_file()]
    if not files:
        return None
    newest = max(files, key=lambda path: path.stat().st_mtime_ns)
    try:
        return _parse(json.loads(newest.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def current_catalog() -> list[dict] | None:
    """现在可信的目录：缓存读得到就用它（并记下来）；读不到就用上一份可信的；都没有返回 None。"""
    global _TRUSTED
    fresh = _read_cache()
    if fresh is not None:
        if fresh != _TRUSTED:
            _TRUSTED = fresh
            try:
                path = _trusted_copy_path()
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps({"models": fresh}, ensure_ascii=False), encoding="utf-8")
                temporary.replace(path)
            except OSError:
                pass
        return fresh
    if _TRUSTED is not None:
        return _TRUSTED
    try:
        saved = _parse(json.loads(_trusted_copy_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        saved = None
    if saved is not None:
        _TRUSTED = saved
    return saved


def list_codex_models() -> list[dict]:
    """给页面看的目录（读不到又没有可信副本时是空列表）。"""
    return list(current_catalog() or [])


def available_slugs() -> set[str] | None:
    """账号里现在有的模型；目录未知时返回 None，表示「不知道」（明确是空目录时返回空集合）。"""
    catalog = current_catalog()
    return None if catalog is None else {m["id"] for m in catalog}


def codex_model_key(slug: str) -> str:
    """gpt-6.1-sol → Codex-6.1-Sol（和原来写死的名字一致）。"""
    body = slug[4:] if slug.startswith("gpt-") else slug
    return "Codex-" + "-".join(part.capitalize() if part.isalpha() else part for part in body.split("-"))


def owned_keys() -> set[str]:
    """现在由本模块补进 MODELS 的名字（保存中转站时拿来防重名）。"""
    from config import MODELS
    return {key for key, cfg in _OWNED.items() if MODELS.get(key) is cfg}


def sync_models() -> list[str]:
    """按可信目录补进 / 去掉 Codex 条目。只动自己补进去的那些（按对象认），
    同名的内置条目、小猫自己存的线路一律不碰。目录未知且没有可信副本时什么都不改。"""
    from config import MODELS
    catalog = current_catalog()
    if catalog is None:
        return sorted(owned_keys())
    wanted = {codex_model_key(item["id"]): item for item in catalog}
    for key, cfg in list(_OWNED.items()):
        if key not in wanted:
            if MODELS.get(key) is cfg:
                MODELS.pop(key, None)
            _OWNED.pop(key, None)
    added = []
    for key, item in wanted.items():
        present = MODELS.get(key)
        if present is not None and present is not _OWNED.get(key):
            continue  # 内置的或小猫自己存的同名线路，照旧
        cfg = {"provider": "codex_cli", "model": item["id"], "vision": item["vision"], "transport_mode": "safe_live"}
        MODELS[key] = cfg
        _OWNED[key] = cfg
        added.append(key)
    return sorted(added)


CLEANUP_SECONDS = 3.0


async def _stop(process) -> None:
    """先关输入让 Codex 自己退出；不退就结束整组进程（包括 Node 包装器启动的原生程序），每步都有时限。"""
    if process.returncode is not None:
        return
    try:
        process.stdin.close()
    except Exception:
        pass
    try:
        await asyncio.wait_for(process.wait(), CLEANUP_SECONDS)
        return
    except asyncio.TimeoutError:
        pass
    if os.name == "nt":
        from generation_control import terminate_process_tree
        try:
            await asyncio.wait_for(terminate_process_tree(process), CLEANUP_SECONDS)
        except asyncio.TimeoutError:
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)  # 启动时开了独立进程组，只杀这一组
        except (ProcessLookupError, PermissionError):
            pass
    try:
        await asyncio.wait_for(process.wait(), CLEANUP_SECONDS)
    except asyncio.TimeoutError:
        pass


async def refresh(timeout: float = 40.0) -> list[dict]:
    """启动一次 codex app-server，只问模型列表，让 Codex 更新它的缓存，再同步进 MODELS。
    整个过程（含清理）最多约 timeout + 3×CLEANUP_SECONDS 秒。"""
    import shutil
    import ai_providers
    if not ai_providers._CODEX_SCRIPT:
        raise RuntimeError("服务器上没有装 Codex")
    env = ai_providers._build_codex_chat_environment()
    extra = {} if os.name == "nt" else {"start_new_session": True}  # 独立进程组，清理时整组结束
    process = await asyncio.create_subprocess_exec(
        shutil.which("node") or "node", ai_providers._CODEX_SCRIPT, "app-server", "--stdio",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env=env, **extra,
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
        await _stop(process)
    sync_models()
    return list_codex_models()
