import asyncio
from unittest.mock import AsyncMock, patch
import routes.moments as m
import chatroom
import ai_providers
import pytest

@pytest.mark.parametrize("prefix,ack",[
    ("[系统设定 - 你的角色设定]\n","收到，我会按照设定扮演角色。"),
    ("[系统设定 - 你的角色设定]\n","收到。"),
    ("[你的角色设定]\n","收到，我会按照设定扮演角色。"),
])
def test_background_persona_exact_pair_deduplicated(prefix,ack):
    p="synthetic fox persona"
    tail=[{"role":"user","content":"private context not persona"},
          {"role":"user","content":"task"}]
    messages=[{"role":"user","content":prefix+p},{"role":"assistant","content":ack}]+tail
    out=chatroom._prepare_connor_persona(messages,p)
    assert out==[{"role":"system","content":p}]+tail
    assert ai_providers._build_cli_prompt(out).count(p)==1
    altered=[{**messages[0],"content":prefix+p+" custom supplement"},*messages[1:]]
    assert chatroom._prepare_connor_persona(altered,p)[1:]==altered

def test_context_limits_keep_direct_and_user_replies_full(monkeypatch):
    monkeypatch.setenv("AIONSHOME_LIGHT_PEER_MOMENTS","1")
    for a in ("aion","connor","ai3"):
        assert m._moment_context_limits({"author":a},None)==(12,3)
        assert m._moment_context_limits({"author":a},"comment")== (30,5)
    assert m._moment_context_limits({"author":"user"},None)==(30,5)
    assert m._moment_context_limits({"author":"unknown"},None)==(30,5)
    monkeypatch.setenv("AIONSHOME_LIGHT_PEER_MOMENTS","0")
    assert m._moment_context_limits({"author":"aion"},None)==(30,5)

def test_peer_comment_route_passes_bounded_context_and_preserves_post(monkeypatch):
    monkeypatch.setenv("AIONSHOME_LIGHT_PEER_MOMENTS","1")
    post={"author":"ai3","id":"x","content":"public post","created_at":1,"comments":[]}
    history=AsyncMock(return_value=[{"sender":"user","content":"history"}])
    memory=AsyncMock(return_value=["relevant memory"])
    sentinel=RuntimeError("stop before model")
    from unittest.mock import Mock
    build=Mock(side_effect=sentinel)
    with patch.object(m,"_get_moment_with_comments",AsyncMock(return_value=post)), \
         patch.object(m,"_get_recent_context_messages",history), \
         patch.object(m,"_get_recent_memories",memory), \
         patch.object(m,"_build_moment_reply_messages",build):
        with pytest.raises(RuntimeError,match="stop before model"):
            asyncio.run(m._ai_reply_to_moment("connor","x"))
    history.assert_awaited_once_with("connor",limit=12)
    memory.assert_awaited_once_with("connor",limit=3,query="public post")
    assert build.call_args.args[1] is post
