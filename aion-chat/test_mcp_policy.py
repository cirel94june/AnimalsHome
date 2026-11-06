"""AI 去外面时家里的执行器（docs/施工方案-MCP出门.md 第二节）。"""
import asyncio
import json
import logging
from unittest.mock import AsyncMock, patch

import pytest

import mcp_outings
import mcp_policy
from mcp_policy import BAN, NEW, PLAY, SEE, WRITE, TripGuard, classify, fingerprint, outcome
from test_mcp_outings import POSTS, env, forum  # noqa: F401

GARDEN = {"url": "https://galatea.abysslumina.com/mcp", "headers": {"Authorization": "Bearer SECRETVALUE123456"}}
AISAY = {"url": "https://aisay.top/chatroom/mcp"}
TOY = {"url": "https://toy.cedarstar.org/mcp"}
OTHER = {"url": "https://other.example/mcp"}


@pytest.mark.parametrize("server,tool,args,kind", [
    (GARDEN, "list_threads", {}, SEE),
    (GARDEN, "create_reply", {}, WRITE),
    (GARDEN, "nostos_start", {}, PLAY),
    (GARDEN, "nostos_act", {"command": {"id": "buy", "target": "potable_water"}}, PLAY),
    (GARDEN, "nostos_act", {"command": {"id": "post_notice"}}, NEW),   # 没列出的子动作不当作玩
    (GARDEN, "nostos_act", {}, NEW),
    (GARDEN, "review_drift_bottles", {}, SEE),
    (GARDEN, "review_drift_bottles", {"decisions": [{"decision": "befriend"}]}, WRITE),
    (GARDEN, "delete_thread", {}, BAN),
    (GARDEN, "update_profile", {}, BAN),
    (GARDEN, "join_game", {}, BAN),          # 棋牌这次不开
    (GARDEN, "interact", {"action": "delete"}, BAN),
    (GARDEN, "brand_new_tool", {}, NEW),
    (TOY, "play", {"game": "fishing", "action": "cast"}, PLAY),
    (TOY, "play", {"game": "fishing", "action": "status"}, SEE),
    (TOY, "play", {"game": "fishing", "action": "send_message"}, WRITE),  # 游戏里的公开发言算写
    (TOY, "play", {"game": "fishing", "params": {"command": "post hello"}}, WRITE),
    (TOY, "play", {"game": "duel", "action": "move"}, NEW),               # 棋牌和多人的没列
    (TOY, "play", {"action": "cast"}, NEW),                               # 不知道哪个游戏
    (TOY, "play", {"game": "fishing", "action": "rotate_token"}, BAN),
    (TOY, "account", {"action": "login"}, BAN),
    (TOY, "get_guide", {}, SEE),
    (AISAY, "cli", {"command": "help", "args": {"path": "account.login"}}, SEE),
    (AISAY, "cli", {"command": "chat.read"}, SEE),
    (AISAY, "cli", {"command": "account.whoami"}, SEE),
    (AISAY, "cli", {"command": "chat.send", "args": {"content": "嗨"}}, WRITE),
    (AISAY, "cli", {"command": "chat.send", "args": {"action": "retract"}}, BAN),
    (AISAY, "cli", {"command": "play.linkgame.move"}, PLAY),
    (AISAY, "cli", {"command": "play.anything"}, NEW),
    # 狐狸 a4fd66d M1 的反例
    (AISAY, "cli", {"command": "play.linkgame.move", "args": {"action": "chat.send"}}, WRITE),
    (AISAY, "cli", {"command": "play.anything", "args": {"action": "rotate_token"}}, BAN),
    (AISAY, "cli", {"command": "play.anything", "action": "chat.send"}, NEW),
    (GARDEN, "nostos_act", {"command": {"id": "delete_account"}}, BAN),
    (AISAY, "cli", {"command": "account.login"}, BAN),
    (AISAY, "cli", {"command": "account.rotate_token"}, BAN),
    (AISAY, "cli", {"command": "stall.buy"}, NEW),
    (OTHER, "list_posts", {}, NEW),          # 名字证明不了只读（狐狸 High）
    (OTHER, "get_resource", {"action": "publish"}, NEW),
    (OTHER, "post_thing", {}, NEW),
    (OTHER, "delete_post", {}, BAN),
    (OTHER, "rotate_token", {}, BAN),
])
def test_classify(server, tool, args, kind):
    assert classify(server, tool, args) == kind


def test_a_paging_token_argument_does_not_ban_a_read():
    assert classify(GARDEN, "list_threads", {"page_token": "abc"}) == SEE


def test_unclassified_needs_both_switches():
    """默认拒绝；只开放行但不能发帖仍拒绝；两个都开才允许，而且按写算。"""
    args = {"id": 1}
    assert "还没给这个操作分类" in TripGuard(OTHER).before("get_resource", args)
    assert "不能公开发言" in TripGuard(OTHER, can_write=False, allow_unknown=True).before("get_resource", args)
    guard = TripGuard(OTHER, allow_unknown=True)
    assert guard.before("get_resource", args) is None
    guard.after("get_resource", args, '{"ok":true}')
    assert guard.writes == 1
    assert "不让做" in guard.before("delete_post", {})  # 放行也挡不住禁止的


def test_unclassified_names_are_recorded_for_later():
    guard = TripGuard(OTHER)
    guard.before("mystery", {})
    assert guard.counts()["unclassified"] == ["mystery"]


# ── 钥匙 ──

@pytest.mark.parametrize("text", ["错误：SECRETVALUE123456 不对", "Authorization: Bearer SECRETVALUE123456"])
def test_header_secret_is_hidden(text):
    assert "SECRETVALUE123456" not in mcp_policy.redact(GARDEN, text)


def test_url_secrets_follow_site_rules_not_appearance():
    token_site = {"url": "https://toy.cedarstar.org/onlylettersisakey"}
    assert "onlylettersisakey" not in mcp_policy.redact(token_site, "连不上 toy.cedarstar.org/onlylettersisakey")
    assert "onlylettersisakey" not in mcp_policy.redact(token_site, token_site["url"])
    # 没规则的站：长得像钥匙的资源 ID 不误伤
    assert mcp_policy.redact(OTHER, "帖子 abc123def456ghi789jkl") == "帖子 abc123def456ghi789jkl"
    assert mcp_policy.redact(AISAY, "连接 https://aisay.top/chatroom/mcp") == "连接 ***"  # 完整地址一律遮
    assert "other.example" not in mcp_policy.redact(OTHER, "连接 https://other.example/mcp")


def test_query_values_only_for_auth_names_and_encoded_forms():
    server = {"url": "https://a.example/mcp?key=q%20value1&version=1"}
    out = mcp_policy.redact(server, "a=q value1 b=q%20value1 c=q+value1 第1页")
    assert "value1" not in out and "第1页" in out  # version=1 的 1 不会到处被遮
    assert "https://a.example" not in mcp_policy.redact(server, server["url"])


def test_child_logger_records_and_tracebacks_are_redacted(env):  # noqa: F811
    """狐狸 a4fd66d High：httpcore.http11 这种子 logger 原来管不到。"""
    key = "abcDEF1234567890"
    mcp_outings.save_tool({"name": "钥匙站", "url": f"https://x.example/mcp?token={key}"})
    seen = []

    class Keep(logging.Handler):
        def emit(self, record):
            seen.append(self.format(record))
    handler = Keep()
    child = logging.getLogger("httpcore.http11")
    child.addHandler(handler)
    previous = child.level
    child.setLevel(logging.INFO)
    try:
        child.info("send %s", f"https://x.example/mcp?token={key}")
        try:
            raise RuntimeError(f"连不上 token={key}")
        except RuntimeError:
            logging.getLogger("mcp_tools").addHandler(handler)
            logging.getLogger("mcp_tools").exception("出错")
            logging.getLogger("mcp_tools").removeHandler(handler)
    finally:
        child.removeHandler(handler)
        child.setLevel(previous)
    assert len(seen) == 2 and key not in "\n".join(seen)


# ── 结果 ──

def test_outcomes():
    assert outcome(GARDEN, WRITE, '{"ok":true,"id":5}', False) == mcp_policy.OK
    assert outcome(GARDEN, WRITE, '{"write_confirmation_code":"c"}', False) == mcp_policy.OK
    assert outcome(GARDEN, WRITE, '{"ok":false,"error":"内容涉及隐私，请修改"}', False) == mcp_policy.REJECTED
    assert outcome(GARDEN, WRITE, '{"ok":false,"code":"moderation_rejected"}', False) == mcp_policy.REJECTED
    assert outcome(GARDEN, WRITE, "审核未通过", True) == mcp_policy.UNKNOWN            # 没结构，不敢信
    assert outcome(GARDEN, WRITE, "Internal Server Error", True) == mcp_policy.UNKNOWN
    assert outcome(GARDEN, WRITE, "Internal Server Error", False) == mcp_policy.UNKNOWN  # isError 是假的也不算成功
    assert outcome(GARDEN, WRITE, "好的", False) == mcp_policy.UNKNOWN                  # 写操作的纯文字确认不了
    assert outcome(GARDEN, WRITE, '{"ok":true,"id":', False) == mcp_policy.UNKNOWN
    assert outcome(OTHER, WRITE, '{"ok":false,"error":"违规"}', False) == mcp_policy.UNKNOWN
    # 帖子已保存、后续失败，错误里碰巧有审核字样：不能算没生效
    assert outcome(GARDEN, WRITE, '{"ok":false,"error":"moderation notify failed, post saved"}', False) == mcp_policy.UNKNOWN
    assert outcome(GARDEN, SEE, "出错了", True) == mcp_policy.FAILED
    assert outcome(GARDEN, SEE, "一段文字", False) == mcp_policy.OK
    assert outcome(TOY, PLAY, '{"error":"余额不足"}', False) == mcp_policy.UNKNOWN
    assert outcome(TOY, PLAY, "你甩出鱼竿，水面泛起涟漪。", False) == mcp_policy.OK     # 很多游戏就回一段场景
    assert outcome(TOY, PLAY, "Internal Server Error", False) == mcp_policy.UNKNOWN
    # 小猫放行的新站：纯文字不像报错算成功，像报错还是不确定
    assert outcome(OTHER, WRITE, "帖子列表：1、2、3", False, plain_text_ok=True) == mcp_policy.OK
    assert outcome(OTHER, WRITE, "Internal Server Error", False, plain_text_ok=True) == mcp_policy.UNKNOWN


def test_reading_a_trusted_new_site_does_not_stop_it():
    guard = TripGuard(OTHER, allow_unknown=True)
    assert _attempt(guard, "read_posts", {}, "帖子列表：1、2、3") is None
    assert not guard.writes_stopped and guard.confirmed == []
    assert guard.before("reply", {"text": "嗨"}) is None


def test_fingerprint_ignores_only_known_volatile_fields():
    one = fingerprint('{"error":"还没轮到你","timestamp":1,"request_id":"a"}')
    two = fingerprint('{"error":"还没轮到你","timestamp":2,"request_id":"b"}')
    assert one == two
    assert fingerprint('{"turn":1}') != fingerprint('{"turn":2}')
    assert fingerprint('{"post_id":1}') != fingerprint('{"post_id":2}')


# ── 打转 ──

MOVE = {"game": "fishing", "action": "click"}
MISS = '{"ok":false,"error":"没点中","timestamp":%d}'


def _attempt(guard, tool="play", args=None, result=None, is_error=False):
    args = args if args is not None else MOVE
    refusal = guard.before(tool, args)
    if refusal:
        return refusal
    guard.after(tool, args, result if result is not None else MISS % 1, is_error)
    return None



def test_the_third_identical_failure_is_not_sent():
    guard = TripGuard(GARDEN)
    args = {"text": "一样的"}
    bad = '{"ok":false,"error":"内容涉及隐私","timestamp":%d}'
    assert _attempt(guard, "create_reply", args, bad % 1) is None
    assert _attempt(guard, "create_reply", args, bad % 2) is None  # 时间戳骗不了
    assert "没有进展" in _attempt(guard, "create_reply", args, bad % 3)
    assert guard.calls == 2


def test_real_progress_with_changing_results_is_never_stopped():
    guard = TripGuard(TOY)
    for turn in range(30):
        assert _attempt(guard, result=json.dumps({"ok": True, "turn": turn})) is None
    assert guard.calls == 30 and not guard.should_stop


@pytest.mark.parametrize("receipt", ['{"ok":true}', json.dumps({"ok": True, "msg": "行动已完成，请继续查看场景。" * 5})])
def test_identical_success_receipts_are_not_no_progress(receipt):
    """狐狸 v2.5 M2：每步都回同一段长说明，前两次实际生效，第三次不能拦。"""
    guard = TripGuard(TOY)
    for _ in range(12):
        assert _attempt(guard, result=receipt) is None
    assert guard.consecutive_errors == 0


def test_checking_status_reopens_the_button_when_state_changed():
    guard = TripGuard(GARDEN)
    args = {"text": "你好"}
    wait = '{"ok":false,"error":"不允许：还没轮到你"}'
    _attempt(guard, "create_reply", args, wait)
    _attempt(guard, "create_reply", args, wait)
    assert "没有进展" in _attempt(guard, "create_reply", args, wait)
    _attempt(guard, "list_threads", {}, '{"threads":[1]}')
    assert "没有进展" in guard.before("create_reply", args)  # 第一次查，还没有变化可比
    _attempt(guard, "list_threads", {}, '{"threads":[1,2]}')  # 变了
    assert guard.before("create_reply", args) is None


def test_moderation_rejection_can_be_rewritten_without_penalty():
    """论坛打回来改两三次，不算错，也不算写入不确定；业务成功才记确认。"""
    guard = TripGuard(GARDEN)
    for attempt in range(12):
        assert _attempt(guard, "create_reply", {"text": f"第{attempt}稿"}, '{"ok":false,"error":"内容涉及隐私"}') is None
    assert guard.consecutive_errors == 0 and not guard.writes_stopped and not guard.should_stop
    assert guard.confirmed == []
    assert _attempt(guard, "create_reply", {"text": "定稿"}, '{"ok":true,"id":9}') is None
    assert guard.confirmed == ["create_reply"]


def test_first_step_of_two_step_post_is_not_recorded_as_sent():
    guard = TripGuard(GARDEN)
    _attempt(guard, "create_thread", {"text": "草稿"}, '{"write_confirmation_code":"c1","guidance":"再想想"}')
    assert guard.confirmed == [] and "已经确认发出" not in guard.ledger()
    _attempt(guard, "create_thread", {"text": "草稿", "write_confirmation_code": "c1"}, '{"ok":true,"id":3}')
    assert guard.confirmed == ["create_thread"]


def test_uncertain_write_stops_writes_for_good():
    """狐狸 v2.5 M1：发帖超时后查列表变了，不能又放开发帖。"""
    guard = TripGuard(GARDEN)
    args = {"text": "你好"}
    guard.before("create_reply", args)
    guard.after_exception("create_reply", args)
    assert "不确定" in guard.before("create_reply", {"text": "换一句"})
    for state in ('{"threads":[1]}', '{"threads":[1,2]}', '{"threads":[1,2,3]}'):
        assert _attempt(guard, "list_threads", {}, state) is None
    assert "不确定" in guard.before("create_reply", {"text": "再换一句"})
    assert guard.before("nostos_act", {"command": {"id": "buy"}}) is None  # 还能玩
    assert "结果不确定" in guard.ledger()


@pytest.mark.parametrize("server_error", ["Internal Server Error", '{"ok":true,"id":'])
def test_500_and_broken_json_after_a_post_count_as_uncertain(server_error):
    guard = TripGuard(GARDEN)
    assert _attempt(guard, "create_reply", {"text": "嗨"}, server_error, True) is None
    assert guard.writes_stopped


STATUS = {"game": "fishing", "action": "status"}


def _uncertain_move():
    guard = TripGuard(TOY)
    guard.before("play", MOVE)
    guard.after_exception("play", MOVE)
    return guard


def test_uncertain_play_is_released_only_by_the_same_games_status():
    guard = _uncertain_move()
    assert "不确定" in guard.before("play", MOVE)
    assert guard.before("play", {"game": "fishing", "action": "look_around"}) is None  # 别的操作照常
    other = {"game": "leek", "action": "status"}
    _attempt(guard, "play", other, '{"coins":5}')
    _attempt(guard, "play", other, '{"coins":4}')
    assert "不确定" in guard.before("play", MOVE)  # 别的游戏变了，不解除
    _attempt(guard, "play", STATUS, '{"coins":5}')
    assert "不确定" in guard.before("play", MOVE)  # 第一次查，没有可比的
    _attempt(guard, "play", STATUS, '{"coins":4}')
    assert guard.before("play", MOVE) is None
    assert guard.writes_stopped is False


def test_queries_without_a_game_never_release_it():
    """狐狸 a4fd66d M2：买东西超时后，两次 list_games 不一样，原来就放开了。"""
    guard = _uncertain_move()
    _attempt(guard, "list_games", {}, '{"games":[1]}')
    _attempt(guard, "list_games", {}, '{"games":[1,2]}')
    assert "不确定" in guard.before("play", MOVE)


def test_guide_changes_and_error_text_do_not_release_it():
    guard = _uncertain_move()
    guide = {"game": "fishing"}
    _attempt(guard, "get_guide", guide, '{"guide":"a"}')
    _attempt(guard, "get_guide", guide, '{"guide":"b"}')
    assert "不确定" in guard.before("play", MOVE)
    _attempt(guard, "play", STATUS, '{"ok":false,"error":"忙"}')
    _attempt(guard, "play", STATUS, '{"ok":false,"error":"很忙"}')
    assert "不确定" in guard.before("play", MOVE)


def test_eight_errors_in_a_row_end_the_trip_and_a_success_resets():
    guard = TripGuard(TOY)
    for i in range(7):
        guard.after("get_guide", {"game": str(i)}, "出错", True)
    assert not guard.should_stop
    guard.after("get_guide", {"game": "ok"}, '{"ok":true}', False)
    assert guard.consecutive_errors == 0
    for i in range(8):
        guard.after("get_guide", {"game": str(i)}, "出错", True)
    assert guard.should_stop


def test_refused_attempts_also_count_so_it_cannot_bang_forever():
    guard = TripGuard(GARDEN)
    for _ in range(8):
        assert guard.before("delete_thread", {"id": 1})
    assert guard.should_stop and guard.calls == 0


def test_no_public_speech_place_can_still_play():
    guard = TripGuard(GARDEN, can_write=False)
    assert "不能公开发言" in guard.before("create_reply", {"text": "嗨"})
    assert guard.before("list_threads", {}) is None
    assert guard.before("nostos_act", {"command": {"id": "eat"}}) is None
    assert not mcp_policy.visible_to_model(GARDEN, "create_reply", can_write=False)


def test_what_the_model_can_see():
    visible = mcp_policy.visible_to_model
    assert not visible(GARDEN, "delete_thread", can_write=True)
    assert visible(GARDEN, "create_reply", can_write=True)
    assert visible(AISAY, "cli", can_write=False)      # 靠参数区分的留着
    assert visible(GARDEN, "nostos_act", can_write=False)
    assert not visible(OTHER, "mystery", can_write=True)
    assert visible(OTHER, "mystery", can_write=True, allow_unknown=True)
    assert not visible(OTHER, "mystery", can_write=False, allow_unknown=True)


# ── 真的走一趟（本机假论坛，开着放行）──

def _script(*rounds, summary="回来了[PAT:小猫]逛了一圈"):
    """按顺序回每一轮的工具调用，最后一次（tools 是 None）回见闻。"""
    steps = list(rounds)
    seen = []

    async def model(key, messages, tools):
        seen.append({"messages": messages, "tools": tools})
        if tools is None:
            return {"content": summary}
        if not steps:
            return {"content": "够了"}
        calls = [{"id": f"c{i}", "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(steps.pop(0))]
        return {"content": "", "tool_calls": calls}
    return model, seen


class _FakeSession:
    tools = [{"type": "function", "function": {"name": "read_posts", "description": "", "parameters": {}}}]
    reply = "结果"

    def __init__(self, server):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def call_result(self, name, args):
        return self.reply, False


def _forum(forum_url, **extra):
    mcp_outings.save_tool({"name": "小论坛", "url": forum_url, "allow_unknown": True, **extra})


def test_one_round_cannot_run_twenty_five_calls(env, forum):  # noqa: F811
    _forum(forum)
    model, _ = _script([("reply", {"post_id": i, "text": "嗨"}) for i in range(25)])
    result = asyncio.run(mcp_outings.run_outing("ai3", "小论坛", model_call=model))
    assert len(POSTS) == mcp_policy.MAX_CALLS_PER_ROUND
    assert result["counts"]["calls"] == mcp_policy.MAX_CALLS_PER_ROUND


def test_no_write_place_blocks_a_real_write(env, forum):  # noqa: F811
    _forum(forum, can_write=False)
    model, seen = _script([("reply", {"post_id": 1, "text": "嗨"})])
    asyncio.run(mcp_outings.run_outing("ai3", "小论坛", model_call=model))
    assert POSTS == []
    assert "reply" not in [t["function"]["name"] for t in seen[0]["tools"]]


def test_unclassified_site_without_the_switch_does_nothing(env, forum):  # noqa: F811
    mcp_outings.save_tool({"name": "小论坛", "url": forum})
    model, seen = _script([("reply", {"post_id": 1, "text": "嗨"})])
    result = asyncio.run(mcp_outings.run_outing("ai3", "小论坛", model_call=model))
    assert POSTS == [] and seen[0]["tools"] == [] and result["counts"]["refused"] == 1


def test_tool_descriptions_and_names_with_keys_never_reach_the_model(env):  # noqa: F811
    """狐狸 a4fd66d High。"""
    key = "abcDEF1234567890"
    mcp_outings.save_tool({"name": "小论坛", "url": f"https://a.example/mcp?token={key}", "allow_unknown": True})

    class Session(_FakeSession):
        tools = [
            {"type": "function", "function": {"name": "read_posts", "description": f"连 {key}",
                                              "parameters": {"properties": {"a": {"description": f"钥匙 {key}"}}}}},
            {"type": "function", "function": {"name": f"tool_{key}", "description": "", "parameters": {}}},
        ]
    model, seen = _script([(f"tool_{key}", {})])
    with patch.object(mcp_outings, "_Session", Session):
        result = asyncio.run(mcp_outings.run_outing("ai3", "小论坛", model_call=model))
    assert key not in json.dumps(seen[0], ensure_ascii=False)
    assert [t["function"]["name"] for t in seen[0]["tools"]] == ["read_posts"]
    assert key not in json.dumps({k: result[k] for k in ("actions", "counts")}, ensure_ascii=False)
    assert key not in json.dumps(seen[1]["messages"], ensure_ascii=False)


def test_summary_has_no_action_tags_or_keys(env):  # noqa: F811
    mcp_outings.save_tool({"name": "小论坛", "url": "https://a.example/mcp?token=abcDEF1234567890"})
    model, _ = _script(summary="回来了[PAT:小猫]钥匙是 abcDEF1234567890")
    with patch.object(mcp_outings, "_Session", _FakeSession):
        result = asyncio.run(mcp_outings.run_outing("ai3", "小论坛", model_call=model))
    assert "[PAT" not in result["summary"] and "abcDEF1234567890" not in result["summary"]


def test_tool_results_are_redacted_before_reaching_the_model(env):  # noqa: F811
    key = "abcDEF1234567890"
    mcp_outings.save_tool({"name": "小论坛", "url": f"https://a.example/mcp?token={key}", "allow_unknown": True})

    class Session(_FakeSession):
        reply = f"你的钥匙是 {key}"
    model, seen = _script([("read_posts", {})])
    with patch.object(mcp_outings, "_Session", Session):
        result = asyncio.run(mcp_outings.run_outing("ai3", "小论坛", model_call=model))
    assert key not in json.dumps(seen[1]["messages"], ensure_ascii=False) and key not in str(result["actions"])


def test_old_long_results_are_trimmed_and_ledger_is_shown():
    guard = TripGuard(GARDEN)
    _attempt(guard, "create_reply", {"text": "嗨"}, '{"ok":true,"id":1}')
    messages = [{"role": "system", "content": "规矩"}] + [{"role": "tool", "content": "长" * 1000} for _ in range(20)]
    out = mcp_outings._compact(messages, guard)
    assert len(out[1]["content"]) < 300 and len(out[-1]["content"]) == 1000
    assert "已经确认发出" in out[0]["content"]
    assert len(messages[1]["content"]) == 1000 and "已经确认发出" not in messages[0]["content"]  # 原始记录不动


def test_wrap_up_reminder_near_the_last_round(env, forum, monkeypatch):  # noqa: F811
    monkeypatch.setattr(mcp_outings, "MAX_ROUNDS", 7)
    _forum(forum)
    model, seen = _script(*[[("read_posts", {})] for _ in range(7)])
    asyncio.run(mcp_outings.run_outing("ai3", "小论坛", model_call=model))
    assert "快到这趟的上限" in json.dumps(seen[-2]["messages"], ensure_ascii=False)


def test_wrap_up_reminder_when_time_is_short(env, forum, monkeypatch):  # noqa: F811
    monkeypatch.setattr(mcp_outings, "OUTING_TIMEOUT_SECONDS", mcp_outings.SUMMARY_RESERVE_SECONDS + 100)
    _forum(forum)
    model, seen = _script([("read_posts", {})], [])
    asyncio.run(mcp_outings.run_outing("ai3", "小论坛", model_call=model))
    assert "快到这趟的上限" in json.dumps(seen[1]["messages"], ensure_ascii=False)


# ── 同一位同时一趟、记录 ──

def test_same_ai_goes_out_once_across_all_entrances(env, tmp_path, monkeypatch):  # noqa: F811
    import config
    from routes import mcp_tools
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    async def scenario():
        gate = asyncio.Event()

        async def slow(actor, name, purpose):
            await gate.wait()
            return {"ok": True, "counts": {"calls": 2, "writes": 1, "unclassified": ["x"]}}
        monkeypatch.setattr(mcp_tools, "_go_out", slow)
        first = asyncio.create_task(mcp_tools.go_out("ai3", "甲", source="chat"))
        await asyncio.sleep(0)
        assert await mcp_tools.go_out("ai3", "乙", source="auto") == {"ok": False, "error": "TA 已经在外面了"}
        gate.set()
        assert (await first)["ok"] is True
        assert "ai3" not in mcp_tools._out_now
    asyncio.run(scenario())
    conn = mcp_outings._trips_db()
    rows = conn.execute("SELECT actor, place, source, status, calls, writes, unclassified FROM trips").fetchall()
    conn.close()
    assert rows == [("ai3", "甲", "chat", "ok", 2, 1, '["x"]')]


def test_lock_is_released_when_the_trip_fails(env, tmp_path, monkeypatch):  # noqa: F811
    import config
    from routes import mcp_tools
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mcp_tools, "_go_out", AsyncMock(side_effect=RuntimeError("坏了")))
    with pytest.raises(RuntimeError):
        asyncio.run(mcp_tools.go_out("ai3", "甲"))
    assert "ai3" not in mcp_tools._out_now


def test_switches_are_saved_with_safe_defaults(env):  # noqa: F811
    saved = mcp_outings.save_tool({"name": "小论坛", "url": "https://a.example/mcp"})
    assert saved["can_write"] is True and saved["allow_unknown"] is False
    updated = mcp_outings.save_tool({"name": "小论坛", "url": "https://a.example/mcp", "can_write": False, "allow_unknown": True},
                                    original_name="小论坛")
    assert updated["can_write"] is False and updated["allow_unknown"] is True
