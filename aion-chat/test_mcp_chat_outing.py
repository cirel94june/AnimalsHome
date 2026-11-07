"""聊天里叫 AI 出门：[出门:地方名:想做什么]（docs/施工方案-MCP出门.md 第四节）。"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import capabilities
import context_builder
import mcp_outings
import mcp_policy
import tts
import web_search
from routes import mcp_tools
from safe_live_stream import _BRACKET_COMMAND_PREFIXES, _LONG_FORM_BRACKET_PREFIXES
from test_mcp_outings import env  # noqa: F401


def test_pattern():
    find = mcp_policy.OUTING_CMD_PATTERN.search
    assert find("[出门:花园论坛:看看新帖]").groups() == ("花园论坛", "看看新帖")
    assert find("［出门：花园论坛：看看］").groups() == ("花园论坛", "看看")
    assert find("[出门:花园论坛]").groups() == ("花园论坛", None)
    assert find("我要出门了") is None


def test_hidden_everywhere():
    assert context_builder.strip_tool_commands("好[出门:花园:看看]的") == "好的"
    assert web_search.clean_web_command_text("好[出门:花园:看看]的") == "好的"
    assert web_search.strip_untrusted_reply("好[出门:花园:看看]的") == "好的"
    assert any(p.search("[出门:花园:看看]") for p in tts._STRIP_PATTERNS)
    assert "[出门" in _BRACKET_COMMAND_PREFIXES and "[出门" in _LONG_FORM_BRACKET_PREFIXES
    stream = web_search.WebCommandStreamFilter()
    assert stream.feed("等我[出") + stream.feed("门:花园:看看]好") + stream.flush() == "等我好"


gone_rooms_box = []


@pytest.fixture
def out(env, monkeypatch):  # noqa: F811
    import autonomy
    monkeypatch.setattr(autonomy, "_actor_label", lambda a: "狗蛋")
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda key: True)
    mcp_outings.save_tool({"name": "花园  论坛", "url": "https://a.example/mcp", "actors": ["ai3"]})
    mcp_outings.save_tool({"name": "别人的", "url": "https://b.example/mcp", "actors": ["aion"]})
    gone = []

    async def fake_go_out(actor, name, purpose="", source="auto", reply_room=""):
        gone.append((actor, name, purpose, source))
        gone_rooms.append(reply_room)
        return {"ok": True}
    gone_rooms = []
    gone_rooms_box.clear()
    gone_rooms_box.append(gone_rooms)
    monkeypatch.setattr(mcp_tools, "go_out", fake_go_out)
    monkeypatch.setattr(mcp_tools, "_running", set())
    monkeypatch.setattr(mcp_tools, "_out_now", set())
    return gone


def _process(text, **kwargs):
    told = []

    async def notify(message):
        told.append(message)

    async def run():
        clean = await mcp_tools.process_outing_commands(text, "ai3", notify=notify, **kwargs)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return clean
    return asyncio.run(run()), told


def test_goes_out_in_the_background_and_tells_her(out):
    clean, told = _process("我去看看[出门:花园 论坛:看看新帖]等我")
    assert clean == "我去看看等我"
    assert out == [("ai3", "花园  论坛", "看看新帖", "chat")]
    assert "去「花园  论坛」了" in told[0]


def test_called_in_a_group_comes_back_to_that_group(out):
    _, told = _process("[出门:花园 论坛:看看]", reply_room="group-1")
    assert gone_rooms_box[0] == ["group-1"] and "回来会在这里讲" in told[0]


def test_called_in_private_comes_back_to_private(out):
    _, told = _process("[出门:花园 论坛:看看]")
    assert gone_rooms_box[0] == [""] and "私聊" in told[0]


def _summary_trip(monkeypatch, reply_room, save_group):
    import autonomy
    from routes import chatroom
    private = AsyncMock(return_value={"id": "p"})
    monkeypatch.setattr(autonomy, "_save_private_message", private)
    monkeypatch.setattr(autonomy, "_actor_label", lambda a: "狗蛋")
    monkeypatch.setattr(autonomy, "append_idle_event", AsyncMock(return_value={}))
    monkeypatch.setattr(chatroom, "_save_msg", save_group)

    async def fake_run(actor, name, purpose=""):
        return {"summary": "我回来了", "actions": [], "model": "m", "counts": {}}
    monkeypatch.setattr(mcp_outings, "run_outing", fake_run)
    asyncio.run(mcp_tools._go_out("ai3", "甲", "", reply_room))
    return private


def test_group_trip_says_it_in_the_group(monkeypatch):
    group = AsyncMock(return_value={"id": "g"})
    private = _summary_trip(monkeypatch, "group-1", group)
    assert group.await_args.args[:3] == ("group-1", "ai3", "我回来了") and group.await_args.kwargs["auto_tts"] is False
    private.assert_not_called()


def test_private_trip_says_it_in_private(monkeypatch):
    group = AsyncMock()
    private = _summary_trip(monkeypatch, "", group)
    group.assert_not_called()
    assert private.await_args.args[:2] == ("ai3", "我回来了")


def test_if_the_group_is_gone_the_story_is_not_lost(monkeypatch):
    private = _summary_trip(monkeypatch, "group-1", AsyncMock(side_effect=RuntimeError("群没了")))
    assert private.await_args.args[:2] == ("ai3", "我回来了")


def test_failure_in_a_group_is_said_in_the_group(monkeypatch):
    from routes import chatroom
    group = AsyncMock(return_value={"id": "g"})
    monkeypatch.setattr(chatroom, "_save_msg", group)
    asyncio.run(mcp_tools._tell_she_did_not_make_it("ai3", "甲", "连不上", "group-1"))
    assert "没去成：连不上" in group.await_args.args[2]


def test_only_one_trip_per_reply(out):
    _process("[出门:花园 论坛:甲][出门:花园 论坛:乙]")
    assert len(out) == 1


def test_a_place_that_is_not_theirs_is_only_removed(out):
    clean, told = _process("[出门:别人的:看看]好")
    assert clean == "好" and out == [] and "不认识" in told[0]


def test_system_wakes_only_remove_the_tag(out):
    clean, told = _process("好[出门:花园 论坛:看看]", allowed=False)
    assert clean == "好" and out == [] and told == []


def test_switch_off_only_removes_the_tag(out, monkeypatch):
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda key: key != "mcp_chat")
    clean, told = _process("好[出门:花园 论坛:看看]")
    assert clean == "好" and out == [] and told == []


def test_already_out_is_said_not_started(out, monkeypatch):
    monkeypatch.setattr(mcp_tools, "_out_now", {"ai3"})
    _, told = _process("[出门:花园 论坛:看看]")
    assert out == [] and "已经在外面了" in told[0]


def test_no_tag_is_untouched(out):
    assert _process("我想出门走走")[0] == "我想出门走走" and out == []


def test_failure_from_chat_tells_her_in_private(env, monkeypatch):  # noqa: F811
    import autonomy
    saved = AsyncMock()
    monkeypatch.setattr(autonomy, "_save_private_message", saved)
    monkeypatch.setattr(mcp_tools, "go_out", AsyncMock(return_value={"ok": False, "error": "连不上"}))
    monkeypatch.setattr(mcp_tools, "_running", set())
    monkeypatch.setattr(mcp_tools, "_out_now", set())

    async def run():
        assert mcp_tools.start_outing("ai3", "甲", "", "chat")
        await asyncio.gather(*list(mcp_tools._tasks))
    asyncio.run(run())
    assert "没去成：连不上" in saved.await_args.args[1] and saved.await_args.kwargs["force_private"] is True


def test_ability_lists_only_their_places(out, monkeypatch):
    monkeypatch.setattr(mcp_outings, "tool_model_for", lambda a: "m")
    text = capabilities.mcp_chat_ability_text("小猫", "ai3")
    assert "[出门:地方名:想做什么]" in text and "花园  论坛" in text and "别人的" not in text
    assert capabilities.mcp_chat_ability_text("小猫", "ai5") == ""
    monkeypatch.setattr(mcp_outings, "tool_model_for", lambda a: "")
    assert capabilities.mcp_chat_ability_text("小猫", "ai3") == ""


def test_outing_only_reply_is_not_a_failure_in_source():
    """狐狸 1414d2e Medium（完整复现在他的探针里）：只写出门指令，原来会记成回复失败。"""
    room = (Path(__file__).parent / "routes" / "chatroom.py").read_text(encoding="utf-8")
    assert "if side_effect_notice or triggered or went_out:" in room
    assert room.count('or triggered.get("outing")) and not clean_text') >= 2


def test_hook_sites():
    root = Path(__file__).parent
    chat = (root / "routes" / "chat.py").read_text(encoding="utf-8")
    assert chat.count("full_text = await _process_outing(full_text, conv_id)") == 3
    room = (root / "routes" / "chatroom.py").read_text(encoding="utf-8")
    assert "not extra_prompt and capture_user_turn and not hub_background" in room
    # 读过外部内容的续答不经过这些入口
    for name in ("perform_web_search_check", "perform_poi_check"):
        start = chat.index(f"async def {name}")
        assert "_process_outing" not in chat[start:chat.index("\nasync def ", start + 10)]
