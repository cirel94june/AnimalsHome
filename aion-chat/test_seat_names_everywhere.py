"""2026-10-30 小猫：很多记录里狗蛋还被写成 AI3。

拼聊天记录（给 AI 看的记忆整理、群聊摘要、搜索、活动日志……）的名字表只认 user / aion / connor，
座位的话原样写成「ai3」，AI 读到后又照抄进日记和记忆。这些地方都要写名字。
"""

import re
from pathlib import Path

import pytest

import actors


@pytest.fixture(autouse=True)
def goudan(monkeypatch):
    monkeypatch.setattr(actors, "_SEAT_NAMES_CACHE", (0.0, {}))
    monkeypatch.setattr(actors, "list_actors", lambda include_disabled=True: [
        {"id": "aion", "name": "小克"}, {"id": "connor", "name": "Lucien"},
        {"id": "ai3", "name": "狗蛋"}, {"id": "ai4", "name": "空位 4"}])
    import chatroom
    monkeypatch.setattr(chatroom, "get_chatroom_names", lambda: ("小猫", "小克", "Lucien"))


def test_seat_names_skips_the_first_two_and_is_cached(monkeypatch):
    assert actors.seat_names() == {"ai3": "狗蛋", "ai4": "空位 4"}
    monkeypatch.setattr(actors, "list_actors", lambda include_disabled=True: 1 / 0)
    assert actors.seat_names()["ai3"] == "狗蛋"  # 几秒内不重读


def test_seat_names_never_raises(monkeypatch):
    monkeypatch.setattr(actors, "list_actors", lambda include_disabled=True: 1 / 0)
    assert actors.seat_names() == {}


def test_chatroom_display_name_for_sender():
    import chatroom
    assert chatroom.display_name_for_sender("ai3") == "狗蛋"
    assert chatroom.display_name_for_sender("connor") == "Lucien"


def test_memory_page_sender_name():
    import routes.memories as memories
    assert memories._chatroom_sender_name("ai3", "小猫", "小克") == "狗蛋"


def test_activity_log_names():
    import activity
    assert activity._user_dynamic_names()["ai3"] == "狗蛋"


_FILES = ["chatroom.py", "memory.py", "routes/memories.py", "routes/seeky.py", "routes/search.py",
          "activity.py", "context_builder.py"]


@pytest.mark.parametrize("name", _FILES)
def test_no_two_ai_only_name_tables_left(name):
    """名字表里有 connor 的，都要带上座位（**seat_names()）。"""
    source = (Path(__file__).parent / name).read_text(encoding="utf-8")
    left = re.findall(r'"connor": (?:connor_name|companion_name|second_name)\}', source)
    assert not left, f"{name} 还有只认小克 / Lucien 的名字表"
