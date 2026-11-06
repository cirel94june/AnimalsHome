"""Hub：background=true 表示这一轮不是小猫在说话；斗地主用单独的 chat_id。"""
import asyncio
import time
from pathlib import Path

import pytest

import memory_hub_bridge as bridge
import seat_chat

ROOT = Path(__file__).parent


def _calls(monkeypatch, *, new_session=False):
    calls = []

    async def fake_tool(cfg, tool, arguments, sent=None):
        calls.append((tool, arguments))
        return "{}"
    monkeypatch.setattr(bridge, "load_config", lambda: {"enabled": True, "url": "https://hub.example/mcp"})
    monkeypatch.setattr(bridge, "_is_active", lambda cfg: True)
    monkeypatch.setattr(bridge, "_in_cooldown", lambda kind="read": False)
    monkeypatch.setattr(bridge, "source_ai_for", lambda actor, cfg=None: "claude")
    monkeypatch.setattr(bridge, "is_new_session", lambda *a, **k: new_session)
    monkeypatch.setattr(bridge, "last_activity", lambda actor: None)
    monkeypatch.setattr(bridge, "_call_tool", fake_tool)
    return calls


def test_real_chat_never_sends_the_flag(monkeypatch):
    calls = _calls(monkeypatch, new_session=True)
    asyncio.run(bridge.context_block("aion", "你好", "room", "private"))
    assert len(calls) == 2 and all("background" not in args for _, args in calls)


def test_background_is_sent_on_every_context_call(monkeypatch):
    calls = _calls(monkeypatch, new_session=True)
    asyncio.run(bridge.context_block("aion", "", "room", "private", background=True))
    assert len(calls) == 2 and all(tool == "context" and args["background"] is True for tool, args in calls)
    calls = _calls(monkeypatch)
    asyncio.run(bridge.context_block("aion", "", "room", "private_group", background=True))
    assert len(calls) == 1 and calls[0][1]["background"] is True


def test_seat_passes_it_through(monkeypatch):
    seen = []

    async def fake_block(actor, message, chat_id="", chat_type="private", **kw):
        seen.append(kw)
        return ""
    monkeypatch.setattr(bridge, "context_block", fake_block)
    monkeypatch.setattr(seat_chat, "_persona_text", lambda a: "人设")
    asyncio.run(seat_chat.build_context("ai3", [], background=True))
    asyncio.run(seat_chat.build_context("ai3", []))
    assert seen == [{"background": True}, {}]


def test_doudizhu_has_its_own_chat_id():
    source = (ROOT / "routes" / "doudizhu.py").read_text(encoding="utf-8")
    assert source.count('memory_hub_chat_id="doudizhu"') == 2


def test_every_background_path_says_so():
    schedule = (ROOT / "schedule.py").read_text(encoding="utf-8")
    assert schedule.count("memory_hub_background=True") == 4  # 闹铃、主动关心
    camera = (ROOT / "camera.py").read_text(encoding="utf-8")
    assert camera.count("memory_hub_background=True") == 2    # 监控叫醒
    room = (ROOT / "routes" / "chatroom.py").read_text(encoding="utf-8")
    assert room.count("memory_hub_background=hub_background or bool(ambient_context) or not capture_user_turn") == 2  # 环境音、自主接话
    assert "background=hub_background or bool(extra_prompt) or not capture_user_turn" in room                        # 座位
    assert "memory_hub_background" not in (ROOT / "routes" / "chat.py").read_text(encoding="utf-8")  # 真实聊天不传


def test_every_connor_context_call_in_background_jobs_is_marked():
    """闹铃、监控里的每一处 Lucien 上下文都要标，漏一处就占她的提醒次数。"""
    for name in ("schedule.py", "camera.py"):
        source = (ROOT / name).read_text(encoding="utf-8")
        for fn in ("build_connor_1v1_context(", "build_connor_group_context("):
            start = 0
            while True:
                at = source.find(fn, start)
                if at < 0:
                    break
                start = at + 1
                if source[at - 6:at] == "async ":  # 定义
                    continue
                call = source[at:source.index(")", at)]
                if "import" in source[max(0, at - 40):at]:
                    continue
                assert "memory_hub_background=True" in call, (name, at)


def _rejecting(monkeypatch, error):
    calls = _calls(monkeypatch, new_session=True)

    async def tool(cfg, name, arguments, sent=None):
        calls.append((name, arguments))
        if arguments.get("background"):
            raise error
        return "记忆"
    monkeypatch.setattr(bridge, "_call_tool", tool)
    monkeypatch.setattr(bridge, "_background_paused_until", 0.0)
    monkeypatch.setitem(bridge._cooldown_until, "read", 0.0)
    monkeypatch.setitem(bridge._consecutive_failures, "read", 0)
    monkeypatch.setattr(bridge, "_in_cooldown", lambda kind="read": time.monotonic() < bridge._cooldown_until.get(kind, 0.0))
    return calls


REJECTED = bridge.HubToolError("context 返回错误", "Unexpected keyword argument: background")


def test_old_hub_rejecting_the_flag_does_not_hurt_real_chat(monkeypatch):
    """狐狸 §178 M2：新对话同时请求两次，旧 Hub 各拒绝一次，原来会触发全局冷却，真实聊天也读不到。"""
    calls = _rejecting(monkeypatch, REJECTED)
    assert asyncio.run(bridge.context_block("aion", "", "room", "private", background=True)) == ""
    assert bridge._cooldown_until["read"] == 0.0 and bridge._consecutive_failures.get("read", 0) == 0
    assert "记忆" in asyncio.run(bridge.context_block("aion", "你好", "room", "private"))


def test_after_a_rejection_background_reads_pause_and_do_not_drop_the_flag(monkeypatch):
    calls = _rejecting(monkeypatch, REJECTED)
    asyncio.run(bridge.context_block("aion", "", "room", "private", background=True))
    seen = len(calls)
    assert asyncio.run(bridge.context_block("aion", "", "room", "private", background=True)) == ""
    assert len(calls) == seen  # 暂停期间不发请求
    assert all(args.get("background") is True for _, args in calls)  # 也没有去掉标记重试
    monkeypatch.setattr(bridge, "_background_paused_until", time.monotonic() - 1)
    asyncio.run(bridge.context_block("aion", "", "room", "private", background=True))
    assert len(calls) > seen  # 过了暂停时间再试


def test_rejection_inside_an_exception_group_is_recognised():
    assert bridge._background_rejected(ExceptionGroup("g", [ExceptionGroup("h", [REJECTED])]))


class McpError(Exception):
    """协议层报错，按类型名认，和 mcp 包里的同名。"""


@pytest.mark.parametrize("text", [
    "Unexpected keyword argument: background",
    "1 validation error\nbackground\n  Unexpected keyword argument [type=unexpected_keyword_argument]",
    "background\n  Extra inputs are not permitted [type=extra_forbidden]",
    'Invalid arguments {"code":"unrecognized_keys","keys":["background"]}',  # 狐狸 §180 的反例：原来漏判
    "Additional properties are not allowed ('background' was unexpected)",
    "Unknown argument: background",
])
def test_real_rejection_formats_are_recognised(text):
    assert bridge._background_rejected(bridge.HubToolError("context 返回错误", text))
    assert bridge._background_rejected(McpError(text))


@pytest.mark.parametrize("error", [
    ConnectionError("background stream: unexpected EOF"),  # 狐狸 §180 的反例：原来误判，错停十分钟
    TimeoutError("background unexpected"),
    RuntimeError("Unexpected keyword argument: background"),  # 不是工具或协议层的报错
    bridge.HubToolError("context 返回错误", "background job failed: unexpected token"),
    bridge.HubToolError("context 返回错误", "数据库忙"),
])
def test_other_errors_are_not_mistaken_for_it(error):
    assert not bridge._background_rejected(error)


def test_network_failures_still_trip_the_normal_breaker(monkeypatch):
    _rejecting(monkeypatch, ConnectionError("连不上"))
    asyncio.run(bridge.context_block("aion", "", "room", "private", background=True))
    assert bridge._consecutive_failures.get("read", 0) >= 1 and bridge._background_paused_until == 0.0


def test_hub_error_text_is_not_in_the_message_that_gets_logged():
    assert "Unexpected" not in str(REJECTED)


def test_ai_chat_later_rounds_are_background_first_is_not(monkeypatch):
    """狐狸 §178 M1：第二轮的「最后一句」是 AI 说的。"""
    from routes import chatroom as room

    seen = []

    async def reply(*a, hub_background=False, **k):
        seen.append(hub_background)
        return None

    async def seat(*a, hub_background=False, **k):
        seen.append(("seat", hub_background))

    async def load(room_id):
        return {"type": "group", "ai_chat_rounds": 2, "context_minutes": 30}, [{"content": "聊聊", "sender": "user"}]
    tasks = []
    monkeypatch.setattr(room, "_load_room_and_messages", load)
    monkeypatch.setattr(room, "_room_models", lambda body: ("m", "c"))
    monkeypatch.setattr(room, "_reply_aion", reply)
    monkeypatch.setattr(room, "_reply_connor", reply)
    monkeypatch.setattr(room, "_generate_seat_reply", seat)
    monkeypatch.setattr(room, "_group_seat_members", lambda: ["ai3"])
    monkeypatch.setattr(room, "load_chatroom_config", lambda: {"reply_order": "aion"})
    monkeypatch.setattr(room, "spawn_generation_task", lambda coro: tasks.append(coro))

    class Body:
        rounds = 2
        tts_enabled = False
        tts_aion_voice = ""
        tts_connor_voice = ""

    async def run():
        await room.trigger_ai_chat("r", Body())
        await tasks[0]
    asyncio.run(run())
    assert seen[:3] == [False, False, ("seat", False)]
    assert seen[3:6] == [True, True, ("seat", True)]
