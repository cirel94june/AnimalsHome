"""
运行状态页 /ops/status：不用 SSH，在手机上就能看服务是否正常、内存、最近更新和报错。
只读；服务只监听本机并经 Tailscale 暴露，外网访问不到。
"""

from __future__ import annotations

import html
import os
import subprocess
import time
from collections import deque
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

import memory_hub_bridge

router = APIRouter()

LOG_DIR = Path(os.environ.get("AIONSHOME_LOG_DIR", "/var/lib/aionshome/logs"))
REPO_DIR = Path(__file__).resolve().parent.parent
STARTED_AT = time.time()


def _tail(path: Path, lines: int) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return "".join(deque(f, maxlen=lines))
    except OSError:
        return "（暂无）"


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO_DIR), *args], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:
        return ""


# 启动时的代码版本：自动更新重启后，用它确认跑起来的确实是新版本
STARTUP_COMMIT = _git("rev-parse", "HEAD")


@router.get("/ops/healthz")
async def healthz():
    """给自动更新用的健康检查：能返回说明启动流程（lifespan）已经走完。"""
    return {"ok": True, "commit": STARTUP_COMMIT, "started_at": STARTED_AT}


def _meminfo() -> dict[str, int]:
    info: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            info[key] = int(value.split()[0]) // 1024
    except Exception:
        pass
    return info


def _own_rss_mb() -> int:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


def _uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    return f"{days}天{hours}小时{rest // 60}分" if days else f"{hours}小时{rest // 60}分"


@router.get("/ops/status", response_class=HTMLResponse)
async def ops_status():
    mem = _meminfo()
    hub = memory_hub_bridge.load_config()
    if not memory_hub_bridge._is_active(hub):
        hub_state = "未启用"
    elif memory_hub_bridge._in_cooldown("read") or memory_hub_bridge._in_cooldown("write"):
        hub_state = "最近调用失败，暂时跳过（1 分钟后重试）"
    else:
        hub_state = "已启用"
    if memory_hub_bridge._is_active(hub):
        stats = memory_hub_bridge.outbox_stats()
        hub_state += f" · 待补传 {stats['pending']} 条"
        if stats["uncertain"]:
            hub_state += f" · 不确定是否送达 {stats['uncertain']} 条（未自动重发，避免重复）"
        if stats["dropped"]:
            hub_state += f" · 队列满丢弃 {stats['dropped']} 条"
    rows = [
        ("服务", f"运行中 · 已运行 {_uptime(time.time() - STARTED_AT)}"),
        ("版本", f"{_git('rev-parse', '--abbrev-ref', 'HEAD')} @ {_git('log', '-1', '--format=%h %s (%cr)')}"),
        ("本服务内存", f"{_own_rss_mb()} MB"),
        ("整机内存", f"可用 {mem.get('MemAvailable', 0)} / {mem.get('MemTotal', 0)} MB · "
                    f"swap 已用 {mem.get('SwapTotal', 0) - mem.get('SwapFree', 0)} / {mem.get('SwapTotal', 0)} MB"),
        ("跨端记忆 Memory Hub", hub_state),
    ]
    table = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>" for k, v in rows)
    update_log = html.escape(_tail(LOG_DIR / "update.log", 20))
    app_log = html.escape(_tail(LOG_DIR / "app.log", 150))
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>运行状态</title>
<style>
:root{{--bg:#f6f4ef;--fg:#2b2a28;--muted:#77736b;--card:#fff;--line:#e6e1d8}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16171a;--fg:#e8e6e1;--muted:#9a968e;--card:#202226;--line:#30333a}}}}
body{{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,sans-serif}}
h1{{font-size:20px;margin:0 0 12px}} h2{{font-size:15px;margin:20px 0 8px;color:var(--muted)}}
table{{width:100%;border-collapse:collapse;background:var(--card);border-radius:12px;overflow:hidden}}
th,td{{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{width:30%;color:var(--muted);font-weight:500}}
pre{{background:var(--card);border-radius:12px;padding:12px;overflow-x:auto;font-size:12px;white-space:pre-wrap;word-break:break-all;margin:0}}
</style></head><body>
<h1>运行状态</h1><table>{table}</table>
<h2>最近的自动更新</h2><pre>{update_log}</pre>
<h2>最近日志（截图发给小克即可排查）</h2><pre>{app_log}</pre>
</body></html>"""
