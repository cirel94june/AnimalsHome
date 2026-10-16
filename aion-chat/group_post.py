"""私聊里「去群里说」：[去群里：内容] 把一句话以这位 AI 的名义发到家里的群聊。

小猫的规则（2026-10-16）：AI 自己觉得有必要也可以去，但不能随意泄露私聊里的私事。
- 只在私聊里提供这个能力；群聊里本来就在群里，不需要。
- 一次回复最多发一条；发到最近活跃的家里群（不含台球等小游戏房间）；其他 AI 不会因此自动接话。
- 能力开关 group_post 关着时：指令照样从正文里去掉，但不发。
- 群聊里看得到私聊记录（私聊和群聊合成一条时间线），所以每个群聊说明都带上 group_privacy_rule。
"""

import re
import time

GROUP_POST_CMD_PATTERN = re.compile(r"\[去群里[：:]\s*([^\]]+)\]")
MAX_GROUP_POST_CHARS = 500


def _private_matters() -> str:
    return "心事、身体和情绪、工作吐槽、对别人的看法、你们之间亲密的话"


def ability_text(user_name: str) -> str:
    return (
        f"[去群里：内容] — 想在家里的群聊里跟大家说点什么时用：{user_name}让你去转告，"
        f"或者你和{user_name}聊到一件值得跟家里人分享的事。内容会以你的名义发到群里。\n"
        f"规则：只说适合全家知道的。{user_name}在私聊里跟你说的私事——{_private_matters()}——"
        "不要带到群里，除非她明确让你去说；拿不准就不说，或者先问她。"
        "用你自己的口吻说，不要照抄私聊原话。一次最多发一条，不要在私聊正文里重复这段。"
    )


def group_privacy_rule(user_name: str) -> str:
    """所有家里群聊说明共用：群里可以随便聊，但私聊里单独说的私事不在群里说出来。"""
    return (
        "这是只有家里人的小群，可以自由聊天、吐槽、开玩笑。"
        f"但{user_name}在私聊里单独跟你说的私事（{_private_matters()}），"
        "她没在群里提、也没让你说，就不要在群里说出来；她自己在群里说了，你才可以接。"
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


async def process_with_result(text: str, actor: str) -> tuple[str, bool]:
    """从私聊回复里取出「去群里」指令：去掉指令，开关开着时把第一条发到家里群。
    返回 (去掉指令后的正文, 是否真的发出去了)。出任何错都只打日志，不影响私聊这条回复。"""
    posts = [p.strip() for p in GROUP_POST_CMD_PATTERN.findall(text or "") if p.strip()]
    cleaned = GROUP_POST_CMD_PATTERN.sub("", text or "").strip() if posts or GROUP_POST_CMD_PATTERN.search(text or "") else text
    if not posts:
        return cleaned, False
    from capabilities import is_capability_enabled
    if not is_capability_enabled("group_post"):
        print(f"[GROUP_POST] 「去群里说」已关闭，{actor} 的指令只去掉不发")
        return cleaned, False
    try:
        room_id = await _family_group_room()
        if not room_id:
            print(f"[GROUP_POST] {actor} 想去群里说，但还没有家里的群")
            return cleaned, False
        from routes.chatroom import _save_msg
        await _save_msg(room_id, actor, posts[0][:MAX_GROUP_POST_CHARS], msg_id=f"cm_{time.time_ns()}_gp",
                        auto_tts=False)
    except Exception as error:  # 查群或发送失败都不影响私聊这条回复（任务取消照常往外抛）
        print(f"[GROUP_POST] 发送失败: {type(error).__name__}")
        return cleaned, False
    print(f"[GROUP_POST] {actor} -> {room_id}: {posts[0][:80]}")
    return cleaned, True


async def process(text: str, actor: str) -> str:
    return (await process_with_result(text, actor))[0]
