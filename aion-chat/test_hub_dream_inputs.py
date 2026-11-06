"""Hub #145：梦靠 context 收到她这一轮的原话、capture 写对 chat_type。"""
import asyncio
import json
from pathlib import Path

import memory_hub_bridge as bridge
import seat_chat


def test_voice_words_are_what_she_said():
    voice = [{"type": "voice", "url": "/a.webm", "transcript": "昨晚做了什么梦"}]
    assert bridge.spoken_text("", voice) == "昨晚做了什么梦"
    assert bridge.spoken_text("还有", voice) == "昨晚做了什么梦\n还有"
    assert bridge.spoken_text("", json.dumps(voice, ensure_ascii=False)) == "昨晚做了什么梦"
    assert bridge.spoken_text("打字的话", None) == "打字的话"
    assert bridge.spoken_text("打字的话", ["/pic.png", {"type": "image"}]) == "打字的话"
    assert bridge.spoken_text("打字的话", "不是 json") == "打字的话"
    assert bridge.spoken_text(None, [{"type": "voice"}]) == ""


OLD_DREAM_TALK = [{"sender": "user", "content": "你昨晚做了什么梦", "created_at": 1},
                  {"sender": "ai3", "content": "梦到海了", "created_at": 2}]


def test_seat_in_a_group_hears_her_not_the_ai_before_it():
    room = [{"sender": "user", "content": "你们昨晚梦到什么了", "created_at": 1},
            {"sender": "aion", "content": "我先说", "created_at": 2},
            {"sender": "connor", "content": "我接着说", "created_at": 3}]
    assert seat_chat.hub_query_for("ai3", room) == "你们昨晚梦到什么了"


def test_seat_hears_her_voice_message():
    room = [{"sender": "user", "content": "", "attachments": [{"type": "voice", "transcript": "梦到什么了"}]}]
    assert seat_chat.hub_query_for("ai3", room) == "梦到什么了"


def test_system_wake_is_not_her_asking_again():
    """狐狸 §175 M1：闹铃叫醒时，旧的问梦不能算这一轮在问。"""
    assert seat_chat.hub_query_for("ai3", OLD_DREAM_TALK + [{"sender": "user", "content": "再问一次梦"}],
                                   system_wake=True) == ""
    assert seat_chat.hub_query_for("ai3", [], system_wake=True) == ""


def test_question_already_answered_is_not_asked_again():
    assert seat_chat.hub_query_for("ai3", OLD_DREAM_TALK) == ""


def test_ai_to_ai_turn_uses_the_last_line_like_before():
    room = OLD_DREAM_TALK + [{"sender": "aion", "content": "你们在聊什么", "created_at": 3}]
    assert seat_chat.hub_query_for("ai3", room, her_turn=False) == "你们在聊什么"
    assert seat_chat.hub_query_for("ai3", [], her_turn=False) == ""


def _sent_to_hub(monkeypatch, **kw):
    seen = {}

    async def fake_block(actor, message, chat_id="", chat_type="private"):
        seen.update(message=message, chat_type=chat_type)
        return ""
    monkeypatch.setattr(bridge, "context_block", fake_block)
    monkeypatch.setattr(seat_chat, "_persona_text", lambda a: "人设")
    asyncio.run(seat_chat.build_context("ai3", OLD_DREAM_TALK, context_limit=10, chat_id="room", group=True, **kw))
    return seen


def test_build_context_does_not_guess_from_the_merged_timeline(monkeypatch):
    assert _sent_to_hub(monkeypatch) == {"message": "", "chat_type": "private_group"}
    assert _sent_to_hub(monkeypatch, query_text="她这轮的话")["message"] == "她这轮的话"


def test_real_entry_passes_the_explicit_query():
    source = (Path(__file__).parent / "routes" / "chatroom.py").read_text(encoding="utf-8")
    assert "query_text=seat_chat.hub_query_for(actor_id, msgs, system_wake=bool(extra_prompt)," in source
    assert "her_turn=capture_user_turn" in source


def test_every_chat_path_sends_her_words():
    root = Path(__file__).parent
    chat = (root / "routes" / "chat.py").read_text(encoding="utf-8")
    assert chat.count('context_block("aion", memory_hub_bridge.spoken_text(body.content, body.attachments), conv_id)') == 2
    assert 'context_block("aion", body.content' not in chat
    # 狐狸 §175 M2：重新生成也要带语音转写，而且要在附件转换之前取
    assert 'context_block("aion", hub_query_text, conv_id)' in chat
    assert chat.index("hub_query_text = (") < chat.index("_process_voice_attachments_in_history(history, keep_idx=last_user_idx)", chat.index("hub_query_text = ("))
    room = (root / "routes" / "chatroom.py").read_text(encoding="utf-8")
    assert 'query_text = msgs[-1]["content"]' not in room
    assert room.count('memory_hub_bridge.spoken_text(msgs[-1]["content"], msgs[-1].get("attachments"))') == 5
