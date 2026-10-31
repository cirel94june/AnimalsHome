"""Stable wire-format helpers for Homecoming snapshots."""

from __future__ import annotations

import hashlib
import json


HOMECOMING_SCHEMA_VERSION = 2
TEXT_WINDOW_SECONDS = 90 * 24 * 60 * 60
MAX_MESSAGES_PER_TIMELINE = 500
# 座位 3～6（狗蛋和以后接的 AI）各自的私聊时间线：seat_private:ai3（2026-10-29 归巢全员）
SEAT_TIMELINE_PREFIX = "seat_private:"
SEAT_OWNERS = ("ai3", "ai4", "ai5", "ai6")
# 归巢快照里来自 Hub 精简包的记忆 id 前缀（只读，回到线上不写回）
HUB_MEMORY_ID_PREFIX = "hub:"
BASE_TIMELINES = ("main_private", "companion_private", "group")


def seat_timeline(actor: str) -> str:
    return f"{SEAT_TIMELINE_PREFIX}{actor}"


def seat_of_timeline(timeline: str) -> str:
    """seat_private:ai3 → ai3；不是座位时间线返回空串。"""
    if not isinstance(timeline, str) or not timeline.startswith(SEAT_TIMELINE_PREFIX):
        return ""
    actor = timeline[len(SEAT_TIMELINE_PREFIX):]
    return actor if actor in SEAT_OWNERS else ""


def is_known_timeline(timeline: str) -> bool:
    return timeline in BASE_TIMELINES or bool(seat_of_timeline(timeline))
SECTION_NAMES = (
    "identity",
    "memories",
    "timelines",
    "schedules",
    "runtime_state",
    "route_descriptors",
)


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_hub_memory_id(memory_id) -> bool:
    """前后空白、大小写都不能绕过只读前缀（Codex M3）。"""
    return str(memory_id or "").strip().lower().startswith(HUB_MEMORY_ID_PREFIX)
