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


def _history(actor_id: str, msgs: list[dict], limit: int) -> list[dict]:
    """房间消息 → 对话轮。只保留用户和该座位的发言，失败提示不进上下文。"""
    out: list[dict] = []
    for msg in msgs[-max(1, limit):]:
        sender = msg.get("sender")
        atts = msg.get("attachments") or []
        if any(isinstance(a, dict) and a.get("type") == "chatroom_reply_failure" for a in atts):
            continue
        content = str(msg.get("content") or "").strip()
        if not content:
            if sender == "user" and atts:
                content = "[图片]"
            else:
                continue
        if sender == "user":
            out.append({"role": "user", "content": append_message_meta(content, msg.get("created_at"))})
        elif sender == actor_id:
            out.append({"role": "assistant", "content": content})
    while out and out[0]["role"] == "assistant":  # 前缀以 assistant 结尾，避免两条 assistant 相连
        out.pop(0)
    return out


async def build_context(actor_id: str, msgs: list[dict], *, context_limit: int = 30) -> list[dict]:
    actor = actors.get_actor(actor_id) or {"name": actor_id}
    wb = load_worldbook()
    user_name = (wb.get("user_name") or "").strip() or "用户"
    query_text = str((msgs[-1] if msgs else {}).get("content") or "")

    # 新对话开头的交接卡由 context_block 自己判断并附上
    hub_task = asyncio.create_task(memory_hub_bridge.context_block(actor_id, query_text))

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
    messages += [
        {"role": "user", "content": "\n\n".join(b for b in background if b)},
        {"role": "assistant", "content": "收到。"},
        {"role": "user", "content": (
            f"[私聊说明]\n你现在在和{user_name}的私聊窗口里。下面是最近的聊天记录，"
            "<meta> 里是发送时间，只供你参考，回复时不要带。直接用你自己的口吻回复最后一条。"
        )},
        {"role": "assistant", "content": "明白了。"},
    ]
    messages += _history(actor_id, msgs, context_limit)
    return messages
