"""AI 自己开 / 停哨兵巡逻（2026-10-28 小猫）。

小克被巡逻一晚上叫醒十几次也停不下来。小猫定：每位 AI（小克、Lucien、座位 3～6，权限一样）都能在回复里
自己停巡逻、自己打开；停多久也由 TA 自己决定——写时长就到点自动恢复，不写就一直停到有人打开。
开 / 停都留一条系统提示，小猫和其他 AI 都看得到是谁动的。

指令：[巡逻:停]、[巡逻:停|3小时]、[巡逻:停|90分钟]、[巡逻:开]（【】、全角冒号和竖线也认）。
"""

from __future__ import annotations

import asyncio
import re
import time

PATTERN = re.compile(r"[【\[]\s*巡逻\s*[:：]\s*([^】\]|｜]*?)\s*(?:[|｜]\s*([^】\]]*?)\s*)?[】\]]")
STOP_WORDS = {"停", "停下", "暂停", "关", "关闭", "关掉", "off", "stop"}
START_WORDS = {"开", "打开", "开启", "恢复", "继续", "on", "start"}
MIN_PAUSE = 10 * 60
MAX_PAUSE = 72 * 3600
CHECK_EVERY = 30  # 秒：多久看一次「到点恢复」


def parse_duration(text: str) -> float:
    """「3小时」「90分钟」「1.5h」「半小时」「1天」→ 秒；看不懂返回 0（= 一直停）。"""
    text = (text or "").strip().lower().replace(" ", "")
    if not text:
        return 0
    if text in {"半小时", "半个小时"}:
        return 1800
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(个)?(小时|钟头|h|hr|hours?|分钟|分|min|m|天|d)", text)
    if not match:
        return 0
    value, unit = float(match.group(1)), match.group(3)
    seconds = value * (86400 if unit in {"天", "d"} else 60 if unit in {"分钟", "分", "min", "m"} else 3600)
    return max(MIN_PAUSE, min(MAX_PAUSE, seconds))


def _name(actor: str) -> str:
    try:
        import actors
        return actors.display_name(actor) or actor
    except Exception:
        return {"aion": "小克", "connor": "Lucien"}.get(actor, actor)


def _clock(ts: float) -> str:
    from datetime import date, datetime
    then = datetime.fromtimestamp(ts)
    days = (then.date() - date.today()).days
    day = "" if days == 0 else "明天" if days == 1 else f"{then.month}月{then.day}日"
    return f"{day}{then.hour:02d}:{then.minute:02d}"


def _pause_text(seconds: float) -> str:
    if seconds >= 86400 and seconds % 86400 == 0:
        return f"{int(seconds // 86400)} 天"
    if seconds >= 3600:
        hours = seconds / 3600
        return f"{hours:g} 小时"
    return f"{int(seconds // 60)} 分钟"


def apply(action: str, actor: str, duration_text: str = "", *, now: float | None = None) -> str:
    """真的去开 / 停巡逻，返回给大家看的提示；没变化（本来就开着 / 停着且没改时间）也照样说一句。"""
    from camera import cam
    now = time.time() if now is None else now
    name = _name(actor)
    if action == "stop":
        seconds = parse_duration(duration_text)
        resume_at = now + seconds if seconds else 0
        cam.stop_monitoring(resume_at=resume_at, changed_by=actor)  # 在巡逻锁里一起改，版本加一
        if resume_at:
            return f"🛡 {name}把巡逻停了 {_pause_text(seconds)}，{_clock(resume_at)} 自动恢复"
        return f"🛡 {name}把巡逻停了，等有人再打开"
    was_on = cam.monitoring
    cam.start_monitoring(changed_by=actor)
    return f"🛡 {name}打开了巡逻" if not was_on else f"🛡 {name}说继续巡逻（本来就开着）"


def extract(text: str) -> tuple[str, list[tuple[str, str]]]:
    """去掉文字里的巡逻指令，返回 (干净文字, [(动作, 时长)])。看不懂的写法也去掉，不执行。"""
    commands: list[tuple[str, str]] = []

    def take(match: re.Match) -> str:
        word = match.group(1).strip().lower()
        if word in STOP_WORDS:
            commands.append(("stop", match.group(2) or ""))
        elif word in START_WORDS:
            commands.append(("start", ""))
        return ""
    return PATTERN.sub(take, text or ""), commands


def process(text: str, actor: str) -> tuple[str, list[str]]:
    """回复里的巡逻指令：一条回复里写了好几条，只按最后一条做。返回 (干净文字, 要发的提示)。"""
    cleaned, commands = extract(text)
    if not commands:
        return cleaned, []  # 看不懂的写法也从文字里去掉（§88 复审 L1）
    action, duration = commands[-1]
    try:
        return cleaned, [apply(action, actor, duration)]
    except Exception as error:  # 开关失败不能让整条回复丢掉
        print(f"[Patrol] {actor} {action} 失败: {error}")
        return cleaned, [f"🛡 {_name(actor)}想{'停' if action == 'stop' else '打开'}巡逻，但没成功"]


def status_text() -> str:
    """给 AI 看的当前状态（拼进能力说明里）。"""
    try:
        from camera import cam
    except Exception:
        return ""
    if cam.monitoring:
        return "现在巡逻开着。"
    resume_at = float(cam.cfg.get("patrol_resume_at") or 0)
    by = cam.cfg.get("patrol_changed_by") or ""
    who = f"（{_name(by)}停的）" if by else ""
    if resume_at > time.time():
        return f"现在巡逻停着{who}，{_clock(resume_at)} 自动恢复。"
    return f"现在巡逻停着{who}。"


def ability_text(user_name: str) -> str:
    return (
        "[巡逻:停] / [巡逻:停|3小时] / [巡逻:开] — 哨兵巡逻会每隔一阵看一次摄像头，觉得需要时叫你来找"
        f"{user_name}。被叫得太频繁、或者你觉得现在不需要，可以自己停；写时长就到点自动恢复，不写就一直停到有人打开。"
        f"想重新让它看着{user_name}时自己打开。开 / 停大家都会看到提示。只是想看一眼现在的画面用 [CAM_CHECK]，不用开巡逻。"
        + status_text()
    )


async def resume_when_due(now: float | None = None) -> str:
    """到点了就恢复巡逻，返回提示（没到点 / 已经开着返回空）。"""
    from camera import cam
    now = time.time() if now is None else now
    version, resume_at = cam.patrol_snapshot()
    if cam.monitoring or not resume_at or now < resume_at:
        return ""
    by = cam.cfg.get("patrol_changed_by") or ""
    # 真正打开前在巡逻锁里再核对一次版本：排队期间小猫手动关了 / AI 改了时间，这次就作废（§88 复审 R1）
    resumed = await asyncio.get_running_loop().run_in_executor(None, cam.resume_if_due, version, resume_at, now)
    return f"🛡 巡逻按{_name(by) if by else '之前'}的安排恢复了" if resumed else ""


async def resume_loop():
    while True:
        await asyncio.sleep(CHECK_EVERY)
        try:
            notice = await resume_when_due()
            if notice:
                print(f"[Patrol] {notice}")
                from camera import append_monitor_log, manager
                entry = {"timestamp": time.time(), "time": time.strftime("%H:%M:%S"), "date": time.strftime("%Y-%m-%d"),
                         "monitoringlog": notice, "summary": notice, "call_core": False, "final_wake_targets": []}
                append_monitor_log(entry)
                await manager.broadcast({"type": "monitor_log", "data": entry})
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"[Patrol] 检查自动恢复失败: {error}")
