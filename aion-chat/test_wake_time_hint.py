"""回话前最后看到的地方写清现在几点（11-07：狗蛋七点说成十二点半，小克七点问核磁做完没）。"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import memory_hub_bridge
import seat_chat


def _build(monkeypatch, msgs, **kw):
    monkeypatch.setattr(memory_hub_bridge, "context_block", AsyncMock(return_value=""))
    monkeypatch.setattr(seat_chat, "_persona_text", lambda a: "人设")
    monkeypatch.setattr(seat_chat, "now_text", lambda seconds=True: "2026年11月7日 星期六 早上 07:04（北京时间）")
    return asyncio.run(seat_chat.build_context("ai3", msgs, **kw))


def test_time_is_right_next_to_her_last_words(monkeypatch):
    built = _build(monkeypatch, [{"sender": "user", "content": "我醒了", "created_at": 1}], group=True)
    assert built[-1]["role"] == "user"
    assert built[-1]["content"].endswith("（现在是 2026年11月7日 星期六 早上 07:04（北京时间））")
    assert "我醒了" in built[-1]["content"]


def test_also_after_another_ais_line_in_the_group(monkeypatch):
    built = _build(monkeypatch, [{"sender": "aion", "content": "早", "created_at": 1}], group=True)
    assert built[-1]["content"].endswith("07:04（北京时间））")


def test_empty_history_is_left_alone(monkeypatch):
    built = _build(monkeypatch, [], group=True)
    assert "（现在是" not in str(built[-1].get("content"))


def test_monitor_wakes_say_what_time_it_is():
    source = (Path(__file__).parent / "camera.py").read_text(encoding="utf-8")
    assert source.count("现在是 {now_text(seconds=False)}") == 3
