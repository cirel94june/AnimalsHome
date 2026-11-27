import asyncio
from unittest.mock import AsyncMock, patch
import capabilities as c
import context_builder as b

def test_shared_schedule_stays_executable():
    for seat in (False,True):
        short="\n".join(c.schedule_ability_texts(monitor=True,seat=seat))
        for x in ("[ALARM:YYYY-MM-DDTHH:MM|内容]","[REMINDER:YYYY-MM-DD|内容]","[SCHEDULE_DEL:日程id]","[Monitor:YYYY-MM-DDTHH:MM|内容]","ISO","不会响铃"):
            assert x in short
        assert ("不能删别人的" in short)==seat
        assert "[Monitor:" not in "\n".join(c.schedule_ability_texts(monitor=False,seat=seat))

def test_memory_short_catalog_keeps_direct_search_options():
    text=c.format_ability_block(["[MEMORY_SEARCH:查询|选项] long original description"])
    for x in ("latest","earliest","date=","range=","detail","history","open=","最多5条","等证据再答"):
        assert x in text

def test_outing_has_discoverable_full_help():
    text=c.format_ability_block(["[出门:地方名:想做什么] long original description"])
    assert "[ABILITY_HELP:outing]" in text
    assert "[ABILITY_HELP:能力名]" in text
    clean,keys=c.extract_ability_help_requests("我去看看。[ABILITY_HELP:outing]")
    assert clean=="我去看看。" and keys==["outing"]
    with patch.object(c,"mcp_chat_ability_text",return_value="full list"):
        assert c.ability_help_text("outing","cat","connor")=="full list"

def test_local_room_memory_dedup_does_not_hide_distinct_or_evidence():
    shared={"id":"one","content":"shared memory "+ "x"*110,"score":.9,"source_start_ts":1}
    distinct={"id":"two","content":shared["content"]+" different ending","score":.8,"source_start_ts":2}
    async def run(detail):
        with patch.object(b,"build_health_summary",AsyncMock(return_value="")):
            return await b.build_memory_blocks("query",use_main_memories=False,
                chatroom_surfacing_fn=AsyncMock(return_value=([shared],{"one"})),
                chatroom_recall_fn=AsyncMock(return_value=[shared,shared,distinct]),
                chatroom_source_fn=AsyncMock(return_value="source evidence"),
                digest_result={"topic":"query","keywords":[],"require_detail":detail},max_recalled_memories=5)
    ordinary=asyncio.run(run(False))
    assert "shared memory" in ordinary["background_block"]
    assert ordinary["memory_block"].count("shared memory")==1
    assert "different ending" in ordinary["memory_block"]
    evidence=asyncio.run(run(True))
    assert evidence["memory_block"].count("shared memory")==2
    assert "source evidence" in evidence["memory_block"]
