import asyncio
from unittest.mock import AsyncMock, patch
import context_builder as c
import capabilities
import proactive_companionship as p

HUB="[跨端记忆]\n- a distinct useful cross-end event"
def item(i, **kw):
    return {"id":str(i),"content":"local event "+str(i),"importance":.4,
            "_surfacing_reason":"recent_fill","unresolved":False,**kw}
def test_only_low_priority_recent_fillers_trimmed(monkeypatch):
    monkeypatch.setenv("AIONSHOME_LIGHT_BACKGROUND","1")
    rows=[item(i) for i in range(6)]
    rows.extend([item(6,importance=.9),item(7,unresolved=True),
                 item(8,_surfacing_reason="topic"),item(9,importance=None),
                 item(10,_surfacing_reason=None)])
    ids={r["id"] for r in rows}
    result,seen=c.trim_recent_background(rows,ids,HUB,{})
    assert [r["id"] for r in result]==["0","1","6","7","8","9","10"]
    assert seen==ids-{"2","3","4","5"}
    assert len(rows)==11 and len(ids)==11
def test_hub_failure_detail_background_and_disabled_fallback(monkeypatch):
    rows=[item(i) for i in range(8)]
    ids={r["id"] for r in rows}
    for hub,digest,bg in [("",{},False),("[跨端记忆] ",{},False),(HUB,{"is_search_needed":True},False),
                          (HUB,{"require_detail":True},False),(HUB,{},True)]:
        assert c.trim_recent_background(rows,ids,hub,digest,background=bg)==(rows,ids)
    from memory_hub_bridge import _CONTEXT_HEADER
    assert c.trim_recent_background(rows,ids,_CONTEXT_HEADER,{})==(rows,ids)
    monkeypatch.setenv("AIONSHOME_LIGHT_BACKGROUND","0")
    assert c.trim_recent_background(rows,ids,HUB,{})==(rows,ids)
def test_trimmed_item_can_be_recalled_by_normal_rag(monkeypatch):
    monkeypatch.setenv("AIONSHOME_LIGHT_BACKGROUND","1")
    rows=[item(i) for i in range(8)]
    match={**rows[5],"score":.8}
    with patch.object(c,"build_health_summary",AsyncMock(return_value="")), \
         patch.object(c,"build_surfacing_memories",AsyncMock(return_value=(rows,{r["id"] for r in rows}))), \
         patch.object(c,"recall_memories",AsyncMock(return_value=([match],[match]))), \
         patch("memory_hub_bridge.context_block",AsyncMock(return_value=HUB)), \
         patch("memory_hub_bridge.read_status",return_value={}):
        result=asyncio.run(c.build_memory_blocks("local event 5",digest_result={"topic":"local event"},
                                                  memory_hub_actor="aion"))
    assert "local event 0" in result["background_block"]
    assert "local event 5" not in result["background_block"]
    assert "local event 5" in result["memory_block"]
def test_short_shared_protocol_keeps_control_contracts(monkeypatch):
    monkeypatch.setattr(p,"is_proactive_enabled",lambda actor:True)
    text=p.proactive_ability_text("ai3")
    assert "[NEXT_CHAT:x]" in text and "[NEXT_CHAT:NONE]" in text and "10～60" in text
    text=capabilities.build_pat_ability_text("cat")
    assert "[PAT:动作短句]" in text and "方括号" in text and "至多一次" in text
    text=capabilities.seat_memory_ability_text("cat")
    for value in ["[MEMORY:内容|分类]","第三人称","200字","「梗：」","people","animal_dorm","待办","最多3条"]:
        assert value in text
