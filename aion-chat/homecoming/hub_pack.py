"""归巢带上 Hub 记忆：每位 AI 拉一份只读精简包（Hub #123 `/api/memory/export-lite`）。

- 用共用钥匙读，不用主人钥匙。
- 包是只读的：手机上的条目 id 都带 `hub:` 前缀，回到线上时这类改动/删除一律不写回（见 return_planner）。
- Hub 连不上或没接时，这一位就不带 Hub 记忆，归巢照常打包。
- 2026-10-31 小猫同意共享 + 各自私人记忆都放进手机。
"""

from __future__ import annotations

import asyncio
import json

import httpx

import actors
import memory_hub_bridge

from .contracts import HUB_MEMORY_ID_PREFIX as HUB_ID_PREFIX

EXPORT_PARAMS = {"max_bytes": 60000, "max_items": 400, "recent_days": 30, "include_dreams": "false"}
TIMEOUT_SECONDS = 12.0        # httpx 单次连接/等一块数据的时限
TOTAL_SECONDS = 15.0          # 每位从发请求到读完正文的总时限（Codex M1：httpx 的 timeout 不管总时长）
MAX_RESPONSE_BYTES = 1_000_000  # 正常一份约 94KB；max_bytes 只管正文预算，不等于回应大小
MAX_ITEMS = 400


def _base_and_headers() -> tuple[str, dict[str, str]] | None:
    cfg = memory_hub_bridge.load_config()
    if not memory_hub_bridge._is_active(cfg) or not str(cfg.get("token") or "").strip():
        return None
    base = str(cfg["url"]).rstrip("/")
    if base.endswith("/mcp"):
        base = base[: -len("/mcp")]
    return base, memory_hub_bridge._headers(cfg)


def portable_items(owner: str, data: dict) -> list[dict]:
    """把 Hub 的条目换成手机记忆的样子。手机库里 id 全局唯一，同一条共享记忆在每位 AI 那里各一份，所以 id 带上 owner。

    结构不对（顶层不是对象、items 不是列表）直接抛 ValueError，由调用方只放弃这一位。
    """
    if not isinstance(data, dict) or not isinstance(data.get("items", []), list):
        raise ValueError("export-lite 结构不对")
    rows = []
    for item in data.get("items") or []:
        if len(rows) >= MAX_ITEMS:
            break
        if not isinstance(item, dict):
            continue
        hub_id = str(item.get("id") or "").strip()
        content = str(item.get("content") or "").strip()
        if not hub_id or not content:
            continue
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        fact_ref = str(item.get("fact_ref") or "").strip()
        rows.append({
            "id": f"{HUB_ID_PREFIX}{owner}:{hub_id}",
            "content": content,
            "keywords": ",".join(str(tag).strip() for tag in tags if str(tag).strip()),
            "importance": item.get("importance"),
            "type": str(item.get("kind") or ""),
            "room": str(item.get("room") or ""),
            "event_date": str(item.get("event_date") or ""),
            "fact_ref": f"{HUB_ID_PREFIX}{owner}:{fact_ref}" if fact_ref else "",
            "source": "hub",
            "read_only": True,
        })
    return rows


async def _download(client: httpx.AsyncClient, base: str, headers: dict, owner: str, hub_id: str) -> list[dict]:
    async with client.stream("GET", base + "/api/memory/export-lite", headers=headers,
                             params={"ai_id": hub_id, **EXPORT_PARAMS}) as response:
        if response.status_code != 200:
            raise ValueError(f"返回 {response.status_code}")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("回应太大")
    return portable_items(owner, json.loads(bytes(body)))


async def _fetch_one(client: httpx.AsyncClient, base: str, headers: dict, owner: str, hub_id: str) -> list[dict]:
    """每位一个失败边界：超时、连不上、结构不对、太大，都只放弃这一位，归巢照常打包。取消不吞。"""
    try:
        return await asyncio.wait_for(_download(client, base, headers, owner, hub_id), TOTAL_SECONDS)
    except Exception as error:  # noqa: BLE001 — CancelledError 是 BaseException，不会被这里吃掉
        print(f"[homecoming] Hub 记忆包 {owner} 没拿到（{type(error).__name__}），这一位不带 Hub 记忆")
        return []


async def fetch_hub_memories(owner_actors: dict[str, str]) -> dict[str, list[dict]]:
    """owner_actors：手机上的 owner → 家里的 actor（main→aion、second→connor、ai3→ai3…）。"""
    hub = _base_and_headers()
    if hub is None:
        return {}
    base, headers = hub
    targets = {owner: actors.memory_hub_id(actor) for owner, actor in owner_actors.items()}
    targets = {owner: hub_id for owner, hub_id in targets.items() if hub_id}
    if not targets:
        return {}
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        results = await asyncio.gather(*(
            _fetch_one(client, base, headers, owner, hub_id) for owner, hub_id in targets.items()
        ))
    return {owner: rows for owner, rows in zip(targets, results) if rows}
