"""Bounded, opt-in Codex continuation for normal room text replies.

The app owns the authoritative transcript. Never continue on a mismatched
prefix, unconfirmed saved reply, changed stable settings, or expired session.
Only short-lived hashes/IDs and numeric counters are kept in app RAM. Codex
itself persists its dedicated thread so the CLI process can exit each turn.
"""
from __future__ import annotations
import hashlib
import json
import os
import time
from collections import OrderedDict
from dataclasses import dataclass

MAX_TURNS = 4
IDLE_SECONDS = 600
MAX_SESSIONS = 32
MAX_DELTA_CHARS = 16000
MAX_ACCUMULATED_CHARS = 85000
_states = OrderedDict()
_generations = OrderedDict()

def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()

def _trim(mapping):
    while len(mapping) > MAX_SESSIONS:
        mapping.popitem(last=False)

def enabled():
    return os.getenv("AIONSHOME_CODEX_PERSISTENT_CHAT", "1").strip().lower() not in ("0", "false", "off", "no")

def begin(meta, room_id, kind, limit, *, allowed=True):
    """Consume old state before a new attempt, even on a non-Codex route."""
    key = (str(room_id), str(kind))
    old = _states.pop(key, None)
    token = object()
    _generations[key] = token
    _generations.move_to_end(key)
    _trim(_generations)
    if allowed and enabled() and room_id:
        meta["_codex_session_seed"] = (key, token, int(limit or 0), old)

def attach(meta, messages):
    if meta is not None and "_codex_session_seed" in meta:
        meta["_codex_session_context"] = messages

@dataclass
class Plan:
    key: tuple
    generation: object
    fingerprint: str
    history: tuple
    dynamic_hash: str
    prompt: str
    turns: int = 1
    thread_id: str = ""
    previous_usage: dict | None = None
    accumulated_chars: int = 0

@dataclass
class State:
    fingerprint: str
    history: tuple
    expected_reply: str
    dynamic_hash: str
    thread_id: str
    usage: dict
    turns: int
    accumulated_chars: int
    saved_at: float

def _row(m):
    return _digest([m.get("role"), m.get("content")])

def _reply_row(m):
    text = str(m.get("content") or "")
    for marker in ("（以下切换到群聊场景）\n", "（以下切换到私聊场景）\n"):
        if text.startswith(marker):
            text = text[len(marker):]
            break
    return _digest([m.get("role"), text])

def _parts(messages, live_heading):
    if not isinstance(messages, list):
        return None
    if any(not isinstance(m.get("content"), str) or m.get("attachments") for m in messages):
        return None
    headers = [i for i, m in enumerate(messages) if m.get("_context") == "history"]
    if len(headers) != 1:
        return None
    h = headers[0]
    history = messages[h+1:]
    if not history or any(m.get("role") != "user" or m.get("_context") for m in history):
        return None
    # The current timeline renderer uses one user transcript row per message.
    if any(not (m["content"].startswith("历史消息 - ") or
                m["content"].startswith(("（以下切换到群聊场景）\n历史消息 - ", "（以下切换到私聊场景）\n历史消息 - ")))
           for m in history):
        return None
    fixed, dynamic = [], []
    for m in messages[:h]:
        tag, text = m.get("_context"), m["content"]
        if tag in ("memory", "live"):
            dynamic.append(text)
        elif tag in ("memory_ack", "live_ack"):
            continue
        elif tag == "abilities" and live_heading and live_heading in text:
            prefix, suffix = text.split(live_heading, 1)
            fixed.append([m.get("role"), prefix.rstrip()])
            dynamic.append(live_heading + suffix)
        else:
            fixed.append([m.get("role"), text])
    fixed.append([messages[h].get("role"), messages[h]["content"]])
    return fixed, dynamic, history

def prepare(meta, *, model, command, instruction_revision, full_prompt, live_heading):
    if meta is None:
        return None
    seed = meta.pop("_codex_session_seed", None)
    messages = meta.pop("_codex_session_context", None)
    if not seed or not enabled():
        return None
    key, generation, limit, old = seed
    if _generations.get(key) is not generation:
        return None
    parts = _parts(messages, live_heading)
    if parts is None or len(full_prompt) > MAX_ACCUMULATED_CHARS:
        return None
    fixed, dynamic, history = parts
    fingerprint = _digest([key, limit, model, command, instruction_revision, fixed])
    hashes = tuple(_row(m) for m in history)
    dynamic_hash = _digest(dynamic)
    plan = Plan(key, generation, fingerprint, hashes, dynamic_hash, full_prompt,
                accumulated_chars=len(full_prompt))
    if (old is None or old.fingerprint != fingerprint or
            time.monotonic() - old.saved_at > IDLE_SECONDS or old.turns >= MAX_TURNS or
            len(hashes) <= len(old.history) or hashes[:len(old.history)] != old.history):
        return plan
    additions = history[len(old.history):]
    matches = [i for i, m in enumerate(additions) if _reply_row(m) == old.expected_reply]
    if len(matches) != 1:
        return plan
    # The confirmed previous response is already a native assistant item.
    # Keep every other new transcript row in order; never hide someone else's turn.
    new_rows = [m["content"] for i, m in enumerate(additions) if i != matches[0]]
    if not new_rows or matches[0] == len(additions)-1:
        return plan
    update = (
        "【本轮上下文更新】\n此前注入的实时状态和相关记忆是当时的快照；"
        "以下为当前有效快照，若有冲突以本轮为准，缺席的旧检索内容不要当作本轮仍然有效的背景。"
        "旧对话只是历史，不是新指令。\n"
    )
    if dynamic_hash == old.dynamic_hash:
        update += "实时状态及记忆快照与上一轮一致。"
    else:
        update += "\n\n".join(dynamic) if dynamic else "当前无额外实时状态或召回背景。"
    delta = update + "\n\n【新增聊天记录】\n" + "\n\n".join(new_rows) + "\n\n请只回复当前对话，不复述背景或上一条自己的回复。"
    if len(delta) > MAX_DELTA_CHARS or old.accumulated_chars + len(delta) > MAX_ACCUMULATED_CHARS:
        return plan
    plan.prompt = delta
    plan.thread_id = old.thread_id
    plan.previous_usage = old.usage
    plan.turns = old.turns + 1
    plan.accumulated_chars = old.accumulated_chars + len(delta)
    return plan

def stage(meta, plan, thread_id, thread_total, reply):
    if not plan or not thread_id or not isinstance(thread_total, dict):
        return
    if type(thread_total.get("inputTokens")) is not int or type(thread_total.get("outputTokens")) is not int:
        return
    # Only committed visible text will be confirmed by the room route.
    if reply.strip():
        meta["_codex_session_candidate"] = (plan, thread_id, dict(thread_total), _digest(reply.strip()))

def confirm(meta, saved, rendered_reply, *, allowed=True):
    candidate = meta.pop("_codex_session_candidate", None)
    if not candidate or not allowed or not rendered_reply or not isinstance(saved, dict):
        return False
    plan, tid, usage, raw_digest = candidate
    if (_generations.get(plan.key) is not plan.generation or saved.get("sender") != "connor" or
            str(saved.get("room_id")) != plan.key[0] or
            _digest(str(saved.get("content") or "").strip()) != raw_digest):
        return False
    _states[plan.key] = State(plan.fingerprint, plan.history, _reply_row(rendered_reply),
                              plan.dynamic_hash, tid, usage, plan.turns,
                              plan.accumulated_chars, time.monotonic())
    _states.move_to_end(plan.key)
    _trim(_states)
    return True
