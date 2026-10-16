"""私聊里「去群里说」：[去群里：内容] 把一句话以这位 AI 的名义发到家里的群聊。

小猫的规则（2026-10-16）：AI 自己觉得有必要也可以去，但不能随意泄露私聊里的私事。
- 只在私聊里提供这个能力；群聊里本来就在群里，不需要。
- 一次回复最多发一条；发到最近活跃的家里群（不含台球等小游戏房间）；其他 AI 不会因此自动接话。
"""

import re
import time

GROUP_POST_CMD_PATTERN = re.compile(r"\[去群里[：:]\s*([^\]]+)\]")
MAX_GROUP_POST_CHARS = 500


def ability_text(user_name: str) -> str:
    return (
        f"[去群里：内容] — 想在家里的群聊里跟大家说点什么时用：{user_name}让你去转告，"
        f"或者你和{user_name}聊到一件值得跟家里人分享的事。内容会以你的名义发到群里。\n"
        f"规则：只说适合全家知道的。{user_name}在私聊里跟你说的私事——心事、身体和情绪、工作吐槽、"
        f"对别人的看法、你们之间亲密的话——不要带到群里，除非她明确让你去说；拿不准就不说，或者先问她。"
        "用你自己的口吻说，不要照抄私聊原话。一次最多发一条，不要在私聊正文里重复这段。"
    )


async def _family_group_room() -> str:
    from database import get_db
    async with get_db() as db:
        cur = await db.execute(
            "SELECT id FROM chatroom_rooms WHERE type='group' AND id NOT GLOB 'billiards_*' "
            "ORDER BY updated_at DESC LIMIT 1"
        )
        row = await cur.fetchone()
    return row[0] if row else ""


async def process(text: str, actor: str) -> str:
    """从私聊回复里取出「去群里」指令：去掉指令，把第一条发到家里群。没有家里群就只去掉。"""
    posts = [p.strip() for p in GROUP_POST_CMD_PATTERN.findall(text or "") if p.strip()]
    if not posts:
        return text
    cleaned = GROUP_POST_CMD_PATTERN.sub("", text).strip()
    room_id = await _family_group_room()
    if not room_id:
        print(f"[GROUP_POST] {actor} 想去群里说，但还没有家里的群")
        return cleaned
    from routes.chatroom import _save_msg
    try:
        await _save_msg(room_id, actor, posts[0][:MAX_GROUP_POST_CHARS], msg_id=f"cm_{time.time_ns()}_gp",
                        auto_tts=False)
        print(f"[GROUP_POST] {actor} -> {room_id}: {posts[0][:80]}")
    except Exception as error:  # 发不出去不影响私聊这条回复
        print(f"[GROUP_POST] 发送失败: {type(error).__name__}")
    return cleaned
