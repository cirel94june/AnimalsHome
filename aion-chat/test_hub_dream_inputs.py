"""Hub #145：梦靠 context 收到她这一轮的原话、capture 写对 chat_type。"""
import asyncio
import json
from pathlib import Path

import memory_hub_bridge as bridge


def test_voice_words_are_what_she_said():
    voice = [{"type": "voice", "url": "/a.webm", "transcript": "昨晚做了什么梦"}]
    assert bridge.spoken_text("", voice) == "昨晚做了什么梦"
    assert bridge.spoken_text("还有", voice) == "昨晚做了什么梦\n还有"
    assert bridge.spoken_text("", json.dumps(voice, ensure_ascii=False)) == "昨晚做了什么梦"
    assert bridge.spoken_text("打字的话", None) == "打字的话"
    assert bridge.spoken_text("打字的话", ["/pic.png", {"type": "image"}]) == "打字的话"
    assert bridge.spoken_text("打字的话", "不是 json") == "打字的话"
    assert bridge.spoken_text(None, [{"type": "voice"}]) == ""


def _seat_query(monkeypatch, msgs):
    import seat_chat
    seen = {}

    class Stop(Exception):
        pass

    async def fake_block(actor, message, chat_id="", chat_type="private"):
        seen.update(message=message, chat_type=chat_type)
        return ""
    monkeypatch.setattr(bridge, "context_block", fake_block)
    monkeypatch.setattr(seat_chat, "_persona_text", lambda a: "人设")

    async def run():
        try:
            await seat_chat.build_context("ai3", msgs, context_limit=10, chat_id="room", group=True)
        except Exception:
            pass  # 只看传给 Hub 的那句，后面的拼装和这里无关
        await asyncio.sleep(0)
    asyncio.run(run())
    return seen


def test_seat_in_a_group_hears_her_not_the_ai_before_it(monkeypatch):
    seen = _seat_query(monkeypatch, [{"sender": "user", "content": "你们昨晚梦到什么了", "created_at": 1},
                                     {"sender": "aion", "content": "我先说", "created_at": 2},
                                     {"sender": "connor", "content": "我接着说", "created_at": 3}])
    assert seen == {"message": "你们昨晚梦到什么了", "chat_type": "private_group"}


def test_seat_hears_her_voice_message(monkeypatch):
    seen = _seat_query(monkeypatch, [{"sender": "user", "content": "", "created_at": 1,
                                      "attachments": [{"type": "voice", "transcript": "梦到什么了"}]}])
    assert seen["message"] == "梦到什么了"


def test_seat_without_any_of_her_words_uses_the_last_line(monkeypatch):
    seen = _seat_query(monkeypatch, [{"sender": "aion", "content": "早", "created_at": 1}])
    assert seen["message"] == "早"


def test_every_chat_path_sends_her_words():
    root = Path(__file__).parent
    chat = (root / "routes" / "chat.py").read_text(encoding="utf-8")
    assert chat.count('context_block("aion", memory_hub_bridge.spoken_text(body.content, body.attachments), conv_id)') == 2
    assert 'context_block("aion", body.content' not in chat
    room = (root / "routes" / "chatroom.py").read_text(encoding="utf-8")
    assert 'query_text = msgs[-1]["content"]' not in room
    assert room.count('memory_hub_bridge.spoken_text(msgs[-1]["content"], msgs[-1].get("attachments"))') == 5
