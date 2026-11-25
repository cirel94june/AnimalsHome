import asyncio
import copy
import json
from unittest.mock import patch

import chatroom
import cache_request_trace as trace
import ai_providers
import reply_usage

def test_exact_duplicate_persona_removed_but_all_text_retained():
    persona = "synthetic persona\npersonality and relationship"
    messages = [
        {"role":"user","content":"[系统设定 - 你的角色设定]\n"+persona},
        {"role":"assistant","content":"收到，我会按照设定扮演角色。"},
        {"role":"user","content":"[用户信息]\nsynthetic user"},
        {"role":"assistant","content":"收到，我会记住。"},
        {"role":"user","content":"[历史记录说明]", "_context":"history"},
        {"role":"user","content":"synthetic question"},
    ]
    original = copy.deepcopy(messages)
    result = chatroom._prepare_connor_persona(messages, persona)
    old = ai_providers._build_cli_prompt([{"role":"system","content":persona}]+messages)
    new = ai_providers._build_cli_prompt(result)
    assert old.count(persona) == 2
    assert new.count(persona) == 1
    assert result[1:] == original[2:]
    assert messages == original
    assert "synthetic user" in new and "synthetic question" in new

def test_no_fuzzy_removal_or_existing_system_mutation():
    messages=[{"role":"user","content":"[系统设定 - 你的角色设定]\nold persona"},
              {"role":"assistant","content":"收到，我会按照设定扮演角色。"}]
    result=chatroom._prepare_connor_persona(messages,"new persona")
    assert result[1:]==messages
    assert chatroom._prepare_connor_persona(result,"new persona") is result
    original=copy.deepcopy(messages)
    original[0]["attachments"]=[{"type":"image"}]
    assert chatroom._prepare_connor_persona(original,"old persona")[1:]==original

def test_sections_count_text_not_secret_content():
    messages=[
        {"role":"system","content":"secret persona","_context":"persona"},
        {"role":"user","content":"[user信息]\nprivate profile"},
        {"role":"assistant","content":"ack"},
        {"role":"user","content":"ability\nLIVE\n实时","_context":"abilities"},
        {"role":"user","content":"secret memory","_context":"memory"},
        {"role":"assistant","content":"ack","_context":"memory_ack"},
        {"role":"user","content":"[历史记录说明]","_context":"history"},
        {"role":"user","content":[{"type":"text","text":"chat text"},{"type":"image_url","image_url":{"url":"SECRET_IMAGE"}}]},
    ]
    snapshot=copy.deepcopy(messages)
    s=trace.context_sections(messages,"LIVE")
    assert s["persona"]["chars"]==len("secret persona")
    assert s["abilities"]["chars"]==len("ability\n")
    assert s["live"]["bytes"]==len("LIVE\n实时".encode())
    assert s["memory"]["chars"]==len("secret memoryack")
    assert s["history"]["chars"]==len("[历史记录说明]chat text")
    assert sum(v["chars"] for v in s.values())==sum(len(m["content"]) if isinstance(m["content"],str) else len("chat text") for m in messages)
    assert messages==snapshot
    meta={"prompt_tokens":200,"completion_tokens":10,"raw":{},"_context_sections":{**s,"untrusted":{"secret":"bad"}},"codex_prompt_chars":42}
    reply_usage.observe(meta,True)
    item=reply_usage.attachments(meta)[0]
    assert item["context_sections"]==s
    assert item["codex_prompt_chars"]==42
    assert "secret" not in json.dumps(item).lower()
    assert "untrusted" not in json.dumps(item)

def test_actual_connor_route_uses_one_persona_and_records_breakdown():
    persona="synthetic fox"
    source=[{"role":"user","content":"[系统设定 - 你的角色设定]\n"+persona},
            {"role":"assistant","content":"收到，我会按照设定扮演角色。"},
            {"role":"user","content":"[历史记录说明]","_context":"history"},
            {"role":"user","content":"question"}]
    seen=[]
    async def fake_call(messages,model,meta):
        seen.extend(messages)
        yield "ok"
    async def run():
        meta={}
        with patch.object(chatroom,"_read_connor_persona",return_value=persona), \
             patch.object(chatroom,"with_current_device_context",side_effect=lambda m:m), \
             patch.object(chatroom,"call_codex_cli",fake_call):
            result=[x async for x in chatroom.stream_connor_cli(messages=source,meta=meta)]
        return result,meta
    result,meta=asyncio.run(run())
    assert result==["ok"]
    assert ai_providers._build_cli_prompt(seen).count(persona)==1
    assert meta["_context_sections"]["persona"]["chars"]==len(persona)
