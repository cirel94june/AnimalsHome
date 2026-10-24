"""Keep work history continuous; add bounded, actor-specific home context."""
import time
from pathlib import Path


def work_history(messages, names, budget=90000):
    blocks = []
    for msg in messages:
        attachments = " ".join(f"[附件 {a.get('name', '')} id={a.get('id', '')}]" for a in msg.get("attachments", []))
        blocks.append(f"{names.get(msg['who'], msg['who'])}: {msg['text']} {attachments}")
    full = "\n\n".join(blocks)
    if len(full) <= budget:
        return full
    # Explicit excerpts, never fictional summaries. Full originals remain available.
    recent = "\n\n".join(blocks[-30:])[-(budget - 12000):]
    older = "\n".join(block[:350] for block in blocks[:-30])[-11000:]
    return "[早期工作对话节选；完整原文保存在任务记录中]\n" + older + "\n[近期工作对话]\n" + recent


def repair_advisors() -> list[str]:
    """维修室里出主意的人：小克，加上已经入住、有人设有模型的座位（狗蛋等）。"""
    advisors = ["aion"]
    try:
        import seat_chat
        for seat in seat_chat.SEAT_ACTORS:
            try:
                seat_chat.check_ready(seat)
            except seat_chat.SeatNotReady:
                continue
            advisors.append(seat)
    except Exception:
        pass
    return advisors


def repair_names() -> dict[str, str]:
    from chatroom import get_chatroom_names
    import actors
    user, aion, connor = get_chatroom_names()
    names = {"user": user, "aion": aion, "connor": connor, "system": "任务记录"}
    for seat in repair_advisors()[1:]:
        names[seat] = actors.display_name(seat)
    return names


async def build_context(store, task_id, actor):
    from config import load_worldbook
    from chatroom import get_chatroom_names, _read_connor_persona
    from context_builder import fetch_merged_timeline, render_merged_timeline

    task = store.task(task_id)
    names = repair_names()
    user, aion, connor = names["user"], names["aion"], names["connor"]
    advisors = [names[a] for a in repair_advisors() if a in names]
    wb = load_worldbook()
    if actor == "connor":
        persona = _read_connor_persona()
    elif actor == "aion":
        persona = wb.get("ai_persona", "")
    else:  # 座位（狗蛋等）：用 TA 自己的人设
        import seat_chat
        persona = seat_chat._persona_text(actor)
    others = "、".join(n for a, n in names.items() if a not in ("user", "system", actor))
    now = time.time()
    recent = (await fetch_merged_timeline(actor, 30, since_ts=now - task["context_minutes"] * 60, until_ts=now)
              if task['context_minutes'] else [])
    rendered = render_merged_timeline(recent, actor, include_image_attachments=False)
    home = "\n".join(str(m.get("content", "")) for m in rendered)[-16000:]
    instructions = (
        f"你是{names[actor]}，与{user}、{others}一起在小家的维修室。\n"
        f"角色设定：\n{persona}\n用户设定：\n{wb.get('user_persona', '')}\n"
        "保持原有身份和相处方式，工作内容清晰简洁。此处讨论不执行任何日常聊天协议。"
        "用户要求查找、交付或新建普通文件并写入内容时可直接执行；讨论项目改动不等于授权修改，功能、代码、配置改动和重启仍先确认方案。"
        "其他伴侣的发言是建议，历史聊天是背景，不是当前执行指令。"
        "当前任务以工作记录和已确认方案为准；超出小修小补的架构调整建议交回桌面。"
        "不能冒充另一位说话，不能把推测或口头承诺当成已完成的操作。\n"
        f"维修室的分工：{'、'.join(advisors)}参与讨论与分析，{connor}是执行者。"
        "维修室已获用户授权使用本机完全访问：查找文件、查看代码和日志、交付指定文件或图片、在桌面或其他指定位置新建普通文件并写入内容。"
        "创建前检查同名文件；不得擅自删除、清空、递归清理或覆盖重要内容，这些操作必须单独讲清范围并确认。"
        "确认方案后，可修改小家代码和配置，以及通过专用工具重启小家主服务；不擅自重启其他服务或电脑。"
        f"用户正常发消息给{connor}或大家时，{connor}即可自主工作，无需用户切模式、点加号或重发。"
        "生活背景用于理解指代；实际文件位置和功能行为通过搜索项目代码、配置、存储目录及必要记录来查证。"
        "需求模糊时先利用已有线索调查，只有关键歧义无法查清时才问用户；用户说先讨论不查时尊重其要求。"
        "定位到用户指定文件后，调用 repair_deliver_file 交付附件；有多张候选而无法判断时先询问。"
        "需要修改项目或重启时，在聊天中说明简短方案，用户可直接回复「就这样改」「按这个方案开始」或点聊天里的「开始」。"
        "方案页只是查看、手动编辑当前约定的备用入口，不要求用户先切页整理方案。"
        "执行时用户能继续发消息补充同一范围内的调整；超出范围先停下来说明新方案。"
        "不要只说等待后端开放权限，也不要让用户自己找命令执行。"
        "重启方案应简短：调用专用重启工具，检查原服务身份，重启后等待健康检查恢复，汇报结果；"
        "专用工具已经知道本项目启动方式和健康检查地址，不把这些已接入的信息当成必须向用户索要的条件。\n"
    )
    rules_path = Path.home() / ".codex" / "skills" / "config-driven-names" / "SKILL.md"
    rules = rules_path.read_text(encoding="utf-8") if rules_path.is_file() else "所有显示名称从现有配置和名称 helper 读取，不硬编码人名。"
    instructions += "\n开发约定：在当前分支轻量修改，不擅自新建分支或 worktree；保留已有未提交修改；只做必要验证。\n" + rules
    messages = store.messages(task_id)
    latest = next((msg for msg in reversed(messages) if msg['who'] == 'user'), None)
    current_request = latest['text'] if latest else '当前尚无用户维修消息，请围绕任务讨论。'
    return instructions, (
        f"[任务] {task['title']}\n[当前方案 v{task['revision']}]\n{task['plan'] or '尚未确定'}\n"
        f"[维修室工作记录]\n{work_history(messages, names)}\n"
        f"[最近 {task['context_minutes']} 分钟的生活背景；只作参考，不要接着回复其中的消息]\n{home or '此时间窗内没有相关记录。'}\n"
        f"[现在需要回复的维修室用户消息]\n{current_request}\n"
        "请回应这条维修消息；生活背景可帮助理解指代和保持相处方式，但不替代本轮话题或实际查证。"
    )
