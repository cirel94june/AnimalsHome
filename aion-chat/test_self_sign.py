import actors
import self_sign


def setup(monkeypatch):
    saved = []
    monkeypatch.setattr(actors, "set_sign", lambda actor, sign: saved.append((actor, sign)) or True)
    monkeypatch.setattr(self_sign, "_changes", {})
    return saved


def test_tag_is_hidden_and_saved(monkeypatch):
    saved = setup(monkeypatch)
    assert self_sign.process("早呀\n[签名：今天也在想你]", "aion") == "早呀"
    assert saved == [("aion", "今天也在想你")]


def test_not_allowed_only_strips(monkeypatch):
    saved = setup(monkeypatch)
    assert self_sign.process("嗯 [签名:x]", "ai3", allowed=False) == "嗯"
    assert saved == []


def test_cleaning_and_daily_limit(monkeypatch):
    saved = setup(monkeypatch)
    self_sign.process("[签名:<b>" + "长" * 80 + "]", "aion", now=1)
    assert "<" not in saved[0][1] and len(saved[0][1]) <= self_sign.MAX_CHARS
    for i in range(5):
        self_sign.process(f"[签名:第{i}个]", "aion", now=2 + i)
    assert len(saved) == self_sign.MAX_PER_DAY
    self_sign.process("[签名:第二天]", "aion", now=90000)
    assert saved[-1] == ("aion", "第二天")


def test_plain_text_untouched(monkeypatch):
    setup(monkeypatch)
    assert self_sign.process("我想换个签名", "aion") == "我想换个签名"


def test_games_switch_blocks_only_self_outings():
    import pathlib
    src = pathlib.Path("mcp_outings.py").read_text(encoding="utf-8")
    assert "if not wake and not garden_wake.games_on(actor):" in src


def test_regenerate_only_removes_the_one_message():
    import pathlib
    src = pathlib.Path("routes/chatroom.py").read_text(encoding="utf-8")
    start = src.index("async def regenerate_chatroom_message")
    block = src[start:src.index("@router", start)]
    assert 'DELETE FROM chatroom_messages WHERE id=?' in block and "created_at>=?\", (room_id, msg_created_at))" not in block
    assert "actor=target[\"sender\"]" in block


def test_outing_model_call_retries_empty_reply(monkeypatch):
    import asyncio
    import sys
    import types
    import httpx
    import mcp_outings
    usage = types.ModuleType("usage_log")
    usage.route_for_model = lambda *a: {}
    usage.record_json = lambda *a, **k: None
    usage.messages_text = lambda *a: ""
    monkeypatch.setitem(sys.modules, "usage_log", usage)
    monkeypatch.setattr(mcp_outings, "_endpoint", lambda key: ("https://relay.invalid/v1/chat/completions", {}, "m"))
    monkeypatch.setattr(mcp_outings, "RELAY_RETRY_SECONDS", 0)
    replies = [httpx.Response(200, content=b""), httpx.Response(200, json={"choices": [{"message": {"content": "好"}}]})]

    class Client:
        def __init__(self, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return replies.pop(0)
    monkeypatch.setattr(mcp_outings.httpx, "AsyncClient", Client)
    assert asyncio.run(mcp_outings.call_model("k", []))["content"] == "好"
    replies[:] = [httpx.Response(200, content=b""), httpx.Response(200, content=b"<html>")]
    try:
        asyncio.run(mcp_outings.call_model("k", []))
        assert False
    except RuntimeError as error:
        assert "空内容" in str(error)
