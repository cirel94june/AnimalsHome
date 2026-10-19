"""
座位 3～6 的私聊（房间类型 seat_1v1）。

前两位（主 AI / 第二 AI）的私聊流程很重：本地记忆、摄像头、自主行动、归档都按它们的身份写死。
座位私聊走一条独立的轻量线路：
  人设 = 座位自己的 persona_sections（人设包导入）+ 世界书「关于用户」
  记忆 = Memory Hub（座位的 memory_hub 身份）：每轮读 incremental 上下文，新对话开头附交接卡
  模型 = 座位设置里的 model；没设或线路已停用时不回复，不偷偷换别的模型
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import actors
import memory_hub_bridge
from config import MODELS, is_model_deprecated, load_worldbook
from context_builder import append_message_meta
from group_post import group_privacy_rule

SEAT_ROOM_TYPE = "seat_1v1"
SEAT_ACTORS = tuple(a for a in actors.SEAT_IDS if a not in ("aion", "connor"))


class SeatNotReady(Exception):
    """座位不能回复（未启用 / 没人设 / 没选模型），message 直接展示给用户。"""


def seat_model(actor_id: str) -> str:
    """返回座位自己的模型键；没设、不存在或已停用时返回空串（调用方必须跳过，不能降级）。"""
    actor = actors.get_actor(actor_id) or {}
    key = str(actor.get("model") or "").strip()
    if not key or key not in MODELS or is_model_deprecated(key):
        return ""
    return key


def check_ready(actor_id: str) -> tuple[dict, str]:
    if actor_id not in SEAT_ACTORS:
        raise SeatNotReady("这个房间没有对应的座位")
    actor = actors.get_actor(actor_id)
    if not actor or not actor.get("enabled"):
        raise SeatNotReady("这个座位还没启用")
    if not actors.persona_sections(actor_id).get("identity_core"):
        raise SeatNotReady(f"{actor['name']} 还没有人设，请先导入人设包")
    model = seat_model(actor_id)
    if not model:
        raise SeatNotReady(f"还没给 {actor['name']} 选模型（相遇卡 → 编辑 → 模型）")
    return actor, model


def _persona_text(actor_id: str) -> str:
    from persona_evolution import _compile_ai_persona_sections
    return _compile_ai_persona_sections(actors.persona_sections(actor_id))


def _speaker_name(sender: str, user_name: str) -> str:
    if sender == "user":
        return user_name
    try:
        return actors.display_name(sender) or sender
    except Exception:
        return sender


async def merged_messages(actor_id: str, limit: int) -> list[dict]:
    """这位座位自己的私聊 + 所有家里群（不含台球等小游戏房间），合成一条时间线（同小克、Lucien）。
    每条带 source：private / group。系统消息不放进来。"""
    import json
    from database import get_db
    async with get_db() as db:
        cur = await db.execute(
            "SELECT m.sender, m.content, m.created_at, m.attachments, r.type FROM chatroom_messages m "
            "JOIN chatroom_rooms r ON r.id = m.room_id "
            "WHERE m.sender != 'system' AND r.id NOT GLOB 'billiards_*' "
            "AND (r.type = 'group' OR (r.type = ? AND r.actor = ?)) "
            "ORDER BY m.created_at DESC, m.rowid DESC LIMIT ?",
            (SEAT_ROOM_TYPE, actor_id, max(1, int(limit)) * 2),
        )
        rows = await cur.fetchall()
    out = []
    for sender, content, created_at, attachments, room_type in reversed(rows):
        try:
            atts = json.loads(attachments) if attachments else []
        except ValueError:
            atts = []
        out.append({"sender": sender, "content": content, "created_at": created_at,
                    "attachments": atts if isinstance(atts, list) else [],
                    "source": "group" if room_type == "group" else "private"})
    return out


def _history(actor_id: str, msgs: list[dict], limit: int, *, group: bool = False, user_name: str = "用户") -> list[dict]:
    """房间消息 → 对话轮。私聊只保留用户和该座位的发言；群聊里别人（小猫和其他 AI）的话都标上名字、
    合并成 user 一侧。失败提示不进上下文。
    消息带 source（私聊 + 群聊合并的时间线）时按每条自己的来源处理，并在发送时间后标「群聊 / 私聊」。"""
    out: list[dict] = []
    for msg in msgs[-max(1, limit):]:
        sender = msg.get("sender")
        atts = msg.get("attachments") or []
        source = msg.get("source")
        in_group = source == "group" if source else group
        label = ("群聊" if in_group else "私聊") if source else ""
        if any(isinstance(a, dict) and a.get("type") == "chatroom_reply_failure" for a in atts):
            continue
        content = str(msg.get("content") or "").strip()
        if not content:
            if sender == "user" and atts:
                content = "[图片]"
            else:
                continue
        if sender == actor_id:
            out.append({"role": "assistant", "content": content})
        elif sender == "user" and not in_group:
            line = append_message_meta(content, msg.get("created_at"), label)
            if out and out[-1]["role"] == "user":
                out[-1]["content"] += "\n" + line
            else:
                out.append({"role": "user", "content": line})
        elif in_group and sender:
            line = append_message_meta(f"【{_speaker_name(sender, user_name)}】{content}", msg.get("created_at"), label)
            if out and out[-1]["role"] == "user":
                out[-1]["content"] += "\n" + line  # 连着几个人说话：并成一轮
            else:
                out.append({"role": "user", "content": line})
    while out and out[0]["role"] == "assistant":  # 前缀以 assistant 结尾，避免两条 assistant 相连
        out.pop(0)
    return out


async def build_context(actor_id: str, msgs: list[dict], *, context_limit: int = 30, chat_id: str = "",
                        group: bool = False) -> list[dict]:
    actor = actors.get_actor(actor_id) or {"name": actor_id}
    wb = load_worldbook()
    user_name = (wb.get("user_name") or "").strip() or "用户"
    query_text = str((msgs[-1] if msgs else {}).get("content") or "")

    # 新对话开头的交接卡由 context_block 自己判断并附上
    hub_task = asyncio.create_task(memory_hub_bridge.context_block(
        actor_id, query_text, chat_id, "private_group" if group else "private"))

    messages: list[dict] = [
        {"role": "user", "content": f"[你的角色设定]\n你是{actor['name']}。\n\n{_persona_text(actor_id)}"},
        {"role": "assistant", "content": "收到，我会按照设定做自己。"},
    ]
    if wb.get("user_persona"):
        messages += [
            {"role": "user", "content": f"[{user_name}信息]\n{wb['user_persona']}"},
            {"role": "assistant", "content": "收到，我会记住。"},
        ]
    now = datetime.now().strftime("%Y-%m-%d %H:%M（%A）")
    background = [f"[当前时间] {now}", await hub_task]
    from capabilities import is_capability_enabled
    if is_capability_enabled("schedule"):  # 日程不会自己响，要在提示词里看得到才能「合适时机自然提起」
        try:
            from schedule import build_schedule_prompt, get_active_schedules
            background.append("[当前日程列表]\n" + build_schedule_prompt(await get_active_schedules(viewer=actor_id)))
        except Exception:
            pass
    messages += [
        {"role": "user", "content": "\n\n".join(b for b in background if b)},
        {"role": "assistant", "content": "收到。"},
        {"role": "user", "content": (
            f"[群聊说明]\n你现在在家里的群聊里，群里有{user_name}和其他几位家人。"
            f"{group_privacy_rule(user_name)}下面是最近的聊天记录，"
            "【】里是说话的人，<meta> 里是发送时间，只供你参考，回复时都不要带。"
            "轮到你说话了：用你自己的口吻接话，可以回应任何人，不用替别人说话。"
        ) if group else (
            f"[私聊说明]\n你现在在和{user_name}的私聊窗口里。下面是最近的聊天记录，"
            "<meta> 里是发送时间，只供你参考，回复时不要带。直接用你自己的口吻回复最后一条。"
        )},
        {"role": "assistant", "content": "明白了。"},
    ]
    abilities = _ability_lines(user_name, group)
    if abilities:
        messages += [
            {"role": "user", "content": "[你能用的指令]\n" + "\n\n".join(abilities)},
            {"role": "assistant", "content": "好的，需要时我会用。"},
        ]
    if any(m.get("source") for m in msgs):
        messages += [
            {"role": "user", "content": "[聊天记录说明]\n下面的记录把你和" + user_name
             + "的私聊、家里群聊按时间排在一起，<meta> 里标了每条是「群聊」还是「私聊」。"},
            {"role": "assistant", "content": "明白了。"},
        ]
    messages += _history(actor_id, msgs, context_limit, group=group, user_name=user_name)
    return messages


def _ability_lines(user_name: str, group: bool) -> list[str]:
    """座位目前能用的指令：点歌、联网搜索、自拍 / 画画、许愿（第二批）；群里说悄悄话、私聊里去群里说（第一批）。"""
    from capabilities import is_capability_enabled, seat_ability_texts
    lines = seat_ability_texts(user_name)
    if group and is_capability_enabled("private_whisper"):
        lines.append(
            f"[悄悄话：内容] — 当你想私下和{user_name}说悄悄话，不让别人听见时用。"
            f"内容会发到你和{user_name}的私聊窗口。不要在群聊正文里重复这段悄悄话。"
        )
    if not group and is_capability_enabled("group_post"):
        import group_post
        lines.append(group_post.ability_text(user_name))
    return lines
