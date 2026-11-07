"""闲时活动换着做的提示（11-07 小猫）。"""
import asyncio
import json
from contextlib import asynccontextmanager

import aiosqlite

import autonomy


def _db_with(picks):
    @asynccontextmanager
    async def get_db():
        db = await aiosqlite.connect(":memory:")
        await db.execute("CREATE TABLE idle_events (actor TEXT, action TEXT, metadata TEXT, created_at REAL)")
        for index, picked in enumerate(picks):
            await db.execute("INSERT INTO idle_events VALUES (?,?,?,?)",
                             ("aion", "select", json.dumps({"selected_action": picked}), 100 - index))
        await db.execute("INSERT INTO idle_events VALUES ('connor','select',?,200)", (json.dumps({"selected_action": "travel"}),))
        try:
            yield db
        finally:
            await db.close()
    return get_db


def _hint(monkeypatch, picks):
    monkeypatch.setattr(autonomy, "get_db", _db_with(picks))
    return asyncio.run(autonomy._recent_choices_hint("aion"))


def test_repeated_choices_get_a_gentle_nudge(monkeypatch):
    text = _hint(monkeypatch, ["private_chat", "private_chat", "rest", "xhs_roam"])
    assert "找小猫说话、找小猫说话、休息、逛小红书" in text and "不是必须" in text


def test_no_nudge_when_varied_or_too_few(monkeypatch):
    assert _hint(monkeypatch, ["private_chat", "rest", "travel"]) == ""
    assert _hint(monkeypatch, ["private_chat", "private_chat"]) == ""


def test_only_this_ais_choices_count(monkeypatch):
    assert "旅行" not in _hint(monkeypatch, ["rest", "rest", "rest"])


def test_broken_db_never_blocks_waking(monkeypatch):
    @asynccontextmanager
    async def broken():
        raise RuntimeError("坏了")
        yield
    monkeypatch.setattr(autonomy, "get_db", broken)
    assert asyncio.run(autonomy._recent_choices_hint("aion")) == ""


def test_hint_is_in_the_selection_prompt():
    from pathlib import Path
    source = (Path(__file__).parent / "autonomy.py").read_text(encoding="utf-8")
    assert "{options}{niche_hint}{await _recent_choices_hint(actor)}" in source
