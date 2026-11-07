"""花园论坛唤醒桥：轮到出牌、有人找他时，花园叫醒这位 AI，家里替他跑一趟（docs/施工方案-一起玩.md 第二节 v3）。

- 桥是官方的（WenXiaoWendy/galatea-garden-wake-bridge），装在服务器 /opt/garden-wake，每位 AI 一个
  systemd 实例 garden-wake@<AI>：Restart=no、不开机自启。断了不自动重连（作者明确禁止），
  家里只在本机看它还在不在，断了告诉小猫，她点「重新连接」才再启动一次。
- 桥叫醒时启动 tools/garden_wake_inject.py，它把 reason 和 message 发到本机 /api/garden-wake/<AI>/wake。
  家里收下就回「已接收」，之后排队跑。
- 轮到出牌（game_turn_required）一定跑；论坛、私信通知按「有人找他就去看」开关（默认开，11-07 小猫）。
- 见闻只发到这位 AI 和小猫的私聊，不发群。
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger("garden_wake")

ACTORS = ("aion", "connor", "ai3")
GAME_REASON = "game_turn_required"
MAX_REASON, MAX_MESSAGE = 64, 4096
WAIT_FREE_SECONDS = 15 * 60   # 他正在别处逛：最多等这么久再去
WATCH_SECONDS = 60            # 本机看一眼桥还在不在（只看 systemd，不连花园）
SYSTEMCTL = "/usr/bin/systemctl"

_pending: dict[str, dict[str, str]] = {}     # 每位 AI：reason → 最新一条 message
_workers: dict[str, asyncio.Task] = {}
_tasks: set[asyncio.Task] = set()


def _path():
    from config import DATA_DIR
    return DATA_DIR / "garden_wake.json"


def _settings() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as error:
        log.warning("[GARDEN] 读设置失败: %s", type(error).__name__)
        return {}


def _save_settings(data: dict[str, Any]) -> None:
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _actor_settings(actor: str) -> dict[str, Any]:
    entry = _settings().get(actor)
    return entry if isinstance(entry, dict) else {}


def _update(actor: str, **fields: Any) -> None:
    data = _settings()
    entry = data.get(actor) if isinstance(data.get(actor), dict) else {}
    data[actor] = {**entry, **fields}
    _save_settings(data)


def notify_on(actor: str) -> bool:
    """有人找他就去看（论坛回复、@、私信）。默认开。"""
    return bool(_actor_settings(actor).get("notify", True))


def set_notify(actor: str, on: bool) -> None:
    _update(actor, notify=bool(on))


def server_for(actor: str) -> dict[str, Any] | None:
    """这位 AI 自己的花园连接：花园的地址，只给他一个人用。"""
    import mcp_outings
    for server in mcp_outings._cfg()["servers"]:
        host = (urlsplit(str(server.get("url") or "")).hostname or "").lower()
        if host == "galatea.abysslumina.com" and (server.get("actors") or []) == [actor] and server.get("enabled", True):
            return server
    return None


# ── 本机的桥进程（systemd）──

def _unit(actor: str) -> str:
    if actor not in ACTORS:
        raise ValueError("不认识这位 AI")
    return f"garden-wake@{actor}.service"


def bridge_state(actor: str) -> str:
    """active / inactive / failed / activating……看不了就是 unknown。"""
    try:
        result = subprocess.run([SYSTEMCTL, "is-active", _unit(actor)], capture_output=True, text=True, timeout=5)
        return (result.stdout or "").strip() or "unknown"
    except Exception:
        return "unknown"


def bridge_active(actor: str) -> bool:
    return bridge_state(actor) == "active"


def _systemctl(verb: str, actor: str) -> tuple[bool, str]:
    """只允许 start / stop 这三个实例。家里的服务开了 NoNewPrivileges，不能用 sudo；
    服务器上的 polkit 规则只允许 aionshome 对 garden-wake@{aion,connor,ai3} 做 start / stop
    （deploy/linux/garden-wake/50-aionshome-garden-wake.rules）。"""
    if verb not in ("start", "stop"):
        raise ValueError("不支持")
    try:
        result = subprocess.run([SYSTEMCTL, "--no-ask-password", verb, _unit(actor)],
                                capture_output=True, text=True, timeout=30)
    except Exception as error:
        return False, type(error).__name__
    return result.returncode == 0, (result.stderr or "").strip()[:200]


def connect(actor: str) -> tuple[bool, str]:
    """小猫点「连接 / 重新连接」：启动一次。不重试。"""
    if not server_for(actor):
        return False, "这位还没接花园论坛"
    ok, error = _systemctl("start", actor)
    if ok:
        _update(actor, want=True, since=time.time(), told=False)
    return ok, error


def disconnect(actor: str) -> tuple[bool, str]:
    _update(actor, want=False)
    return _systemctl("stop", actor)


def status() -> list[dict[str, Any]]:
    import actors as actors_registry
    out = []
    for actor in ACTORS:
        server = server_for(actor)
        entry = _actor_settings(actor)
        out.append({"id": actor, "name": actors_registry.display_name(actor), "garden": (server or {}).get("name") or "",
                    "state": bridge_state(actor) if server else "", "notify": notify_on(actor),
                    "want": bool(entry.get("want")), "waiting": len(_pending.get(actor) or {}),
                    "last_wake": entry.get("last_wake") or 0})
    return out


# ── 被叫醒 ──

def receive(actor: str, reason: str, message: str) -> str:
    """桥送来的一条唤醒。返回 queued / skipped（开关关着）。格式不对抛 ValueError。"""
    if actor not in ACTORS:
        raise ValueError("不认识这位 AI")
    reason, message = str(reason or "").strip(), str(message or "").strip()
    if not reason or len(reason) > MAX_REASON or not message or len(message) > MAX_MESSAGE:
        raise ValueError("唤醒格式不对")
    _update(actor, last_wake=time.time())
    if reason != GAME_REASON and not notify_on(actor):
        log.info("[GARDEN] %s 有通知（%s），开关关着，不去看", actor, reason)
        return "skipped"
    _pending.setdefault(actor, {})[reason] = message  # 同一种只留最新一条
    worker = _workers.get(actor)
    if not worker or worker.done():
        _workers[actor] = asyncio.create_task(_work(actor))
    return "queued"


async def _work(actor: str) -> None:
    from routes import mcp_tools
    while _pending.get(actor):
        pending = _pending[actor]
        reason = GAME_REASON if GAME_REASON in pending else next(iter(pending))
        message = pending.pop(reason)
        server = server_for(actor)
        if not server:
            log.warning("[GARDEN] %s 被叫醒，但花园连接不在了", actor)
            continue
        waited = 0
        while actor in mcp_tools._out_now and waited < WAIT_FREE_SECONDS:  # 他在别处逛：等他回来
            await asyncio.sleep(3)
            waited += 3
        try:
            await mcp_tools.go_out(actor, server["name"], message, source="garden_wake", wake=reason)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.warning("[GARDEN] %s 这趟没跑成: %s", actor, type(error).__name__)


async def watch_loop() -> None:
    """桥断了告诉小猫一次（只看本机 systemd，不连花园，不重启）。"""
    while True:
        await asyncio.sleep(WATCH_SECONDS)
        for actor in ACTORS:
            try:
                entry = _actor_settings(actor)
                if not entry.get("want") or entry.get("told"):
                    continue
                state = bridge_state(actor)
                if state in ("active", "activating", "unknown"):
                    continue
                _update(actor, want=False, told=True)
                await _tell_her(actor)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log.warning("[GARDEN] 看桥状态出错: %s", type(error).__name__)


async def _tell_her(actor: str) -> None:
    import autonomy
    await autonomy._save_private_message(
        actor, "（系统）我和花园论坛的唤醒连接断了，轮到我出牌也叫不醒我。"
               "你看一下 MCP 工具页的「花园唤醒」，没问题的话点「重新连接」。", force_private=True)
