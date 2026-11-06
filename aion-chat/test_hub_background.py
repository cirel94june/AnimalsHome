"""Hub：background=true 表示这一轮不是小猫在说话；斗地主用单独的 chat_id。"""
import asyncio
from pathlib import Path

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
    assert room.count("memory_hub_background=bool(ambient_context) or not capture_user_turn") == 2  # 环境音、自主接话
    assert "background=bool(extra_prompt) or not capture_user_turn" in room                        # 座位
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
