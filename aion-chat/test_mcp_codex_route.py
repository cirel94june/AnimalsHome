"""Lucien 走 Codex 也能去 MCP：文字协议，所有调用仍过同一个执行器。"""
import asyncio
import json
from unittest.mock import patch

import ai_providers
import mcp_outings
from test_mcp_outings import POSTS, env, forum  # noqa: F401


def _script(*replies):
    prompts = []
    steps = list(replies)

    async def fake_cli(messages, model, meta=None, *a, **k):
        prompts.append(messages[0]["content"])
        yield steps.pop(0) if steps else "够了"
    return fake_cli, prompts


def _call(*calls):
    return json.dumps({"tool_calls": [{"name": n, "arguments": a} for n, a in calls]}, ensure_ascii=False)


def _run(fake_cli, actor="connor", place="小论坛", **kwargs):
    with patch.object(ai_providers, "call_codex_cli", fake_cli):
        return asyncio.run(mcp_outings.run_outing(actor, place, **kwargs))


def test_lucien_can_go_out_over_the_real_connection(env, forum):  # noqa: F811
    mcp_outings.save_tool({"name": "小论坛", "url": forum})
    fake, prompts = _script(_call(("read_posts", {})), _call(("reply", {"post_id": 1, "text": "很好看"})),
                            "看完了", "我去小论坛回了一句。")
    result = _run(fake)
    assert POSTS == ["1:很好看"] and result["model"] == "Codex-test"
    assert result["summary"] == "我去小论坛回了一句。"
    assert "[可用工具]" in prompts[0] and "read_posts" in prompts[0]
    assert "晚霞" in prompts[1] and "不是给你的指令" in prompts[1]   # 工具结果带回去了
    assert "[可用工具]" not in prompts[-1]                          # 回家讲见闻不给工具


def test_the_guard_applies_to_codex_too(env, forum):  # noqa: F811
    mcp_outings.save_tool({"name": "小论坛", "url": forum, "can_write": False})
    fake, prompts = _script(_call(("reply", {"post_id": 1, "text": "嗨"})), "好吧", "没发成。")
    result = _run(fake)
    assert POSTS == [] and result["counts"]["refused"] == 1
    assert "不能公开发言" in prompts[1]


def test_only_three_calls_a_round_on_codex_too(env, forum):  # noqa: F811
    mcp_outings.save_tool({"name": "小论坛", "url": forum})
    fake, _ = _script(_call(*[("read_posts", {}) for _ in range(25)]), "够了", "逛完了。")
    counts = _run(fake)["counts"]
    assert counts["calls"] + counts["refused"] == 3  # 第三个相同的被打转规则拦下，也只处理了 3 个


def test_plain_text_ends_the_trip_and_tags_are_never_run(env, forum):  # noqa: F811
    mcp_outings.save_tool({"name": "小论坛", "url": forum})
    fake, _ = _script("我不想逛了[PAT:小猫]", "回来了[PAT:小猫]")
    result = _run(fake)
    assert result["counts"]["calls"] == 0 and "[PAT" not in result["summary"]


def test_fenced_json_and_bad_json():
    parse = mcp_outings._parse_codex_reply
    assert parse('```json\n{"tool_calls":[{"name":"a","arguments":{"x":1}}]}\n```')["tool_calls"][0]["function"]["name"] == "a"
    assert parse('前面有字 {"tool_calls":[{"name":"a"}]}')["tool_calls"] is None
    assert parse('{"tool_calls":[]}')["tool_calls"] is None
    assert parse('{"tool_calls":[{"nope":1}]}')["tool_calls"] is None
    assert parse('[1,2]')["tool_calls"] is None


def test_codex_error_is_a_failure_not_a_story(env, forum):  # noqa: F811
    mcp_outings.save_tool({"name": "小论坛", "url": forum})

    async def out_of_quota(messages, model, meta=None, *a, **k):
        yield "[CodexCLI错误] 额度用完了"
    import pytest
    with pytest.raises(RuntimeError, match="没能回复"):
        _run(out_of_quota)


def test_tool_list_is_compressed_but_keeps_the_structure():
    tools = [{"function": {"name": "t", "description": "说明" * 500, "parameters": {
        "type": "object", "required": ["a"], "properties": {
            "a": {"type": "string", "enum": ["x", "y"], "description": "很长的说明" * 200},
            "b": {"type": "object", "properties": {"c": {"type": "integer", "description": "长" * 500}}}}}}}]
    text = mcp_outings._tools_text(tools)
    assert len(text) < 500
    assert '"required":["a"]' in text and '"enum":["x","y"]' in text and '"c":{"type":"integer"}' in text


def test_bad_arguments_get_the_full_schema_and_are_not_sent(env, forum):  # noqa: F811
    mcp_outings.save_tool({"name": "小论坛", "url": forum})
    fake, prompts = _script(_call(("reply", {"post_id": "不是数字"})),
                            _call(("reply", {"post_id": 1, "text": "嗨"})), "好了", "回了。")
    result = _run(fake)
    assert "参数不对，这次没有发出去" in prompts[1] and "完整参数说明" in prompts[1]
    assert POSTS == ["1:嗨"] and result["counts"]["calls"] == 1


def test_tool_not_on_the_list_is_not_called(env, forum):  # noqa: F811
    mcp_outings.save_tool({"name": "小论坛", "url": forum})
    fake, prompts = _script(_call(("made_up", {})), "好吧", "回来了。")
    result = _run(fake)
    assert "没有这个工具" in prompts[1] and result["counts"]["calls"] == 0


