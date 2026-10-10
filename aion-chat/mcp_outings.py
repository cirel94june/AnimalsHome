"""
MCP 工具：小猫自己接的外部 MCP（论坛、游戏、旅行、浏览器、GitHub……），所有 AI 都能去。

- 配置和娱乐室共用 data/mcp_servers.json；这里多存几个字段：
    description  给 AI 看的介绍（这是什么地方、能做什么）
    autonomy     有空时 AI 可以自己去
    actors       哪几位 AI 可以去（空 = 所有启用的 AI）
    headers      需要的 token 等（只存在服务器上，接口不回传原文）
- 出门逛一趟：用该 AI 自己的模型 + 工具调用跑一个有上限的小循环，回来用第一人称讲见闻，
  发到 TA 和小猫的私聊里。每趟开一次短连接，结束就关，不长期占着连接。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import AsyncExitStack
from typing import Any, Awaitable, Callable

import httpx

from config import MODELS, get_key, is_model_deprecated, load_worldbook

log = logging.getLogger("mcp_outings")

TOOL_PROVIDERS = {"custom_openai", "siliconflow", "aipro"}
MAX_ROUNDS = 8
TOOL_TIMEOUT_SECONDS = 60
OUTING_TIMEOUT_SECONDS = 600
SUMMARY_MAX_CHARS = 600
NAME_PATTERN = re.compile(r"^[\w一-鿿 \-·.]{1,40}$")


# ── 配置 ──

def _cfg() -> dict[str, Any]:
    from mcp_client import _load_config
    cfg = json.loads(json.dumps(_load_config()))  # 深拷贝：_load_config 首次会返回默认配置本身
    cfg.setdefault("servers", [])
    return cfg


def _save(cfg: dict[str, Any]) -> None:
    from mcp_client import _save_config
    _save_config(cfg)


def list_tools() -> list[dict[str, Any]]:
    """给页面看的列表：不回传 headers 原文，只告诉有没有填。"""
    out = []
    for s in _cfg()["servers"]:
        if s.get("visible", True) is False:
            continue
        out.append({
            "name": s["name"],
            "type": s.get("type", "http"),
            "url": s.get("url", ""),
            "enabled": s.get("enabled", True),
            "description": s.get("description", ""),
            "autonomy": bool(s.get("autonomy", False)),
            "actors": list(s.get("actors") or []),
            "header_names": sorted((s.get("headers") or {}).keys()),
        })
    return out


def parse_headers(text: str) -> dict[str, str]:
    """「名字: 值」一行一个。"""
    headers: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            raise ValueError(f"请求头格式应为「名字: 值」：{line[:40]}")
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if not re.fullmatch(r"[A-Za-z0-9\-_]{1,64}", k):
            raise ValueError(f"请求头名字不对：{k[:40]}")
        headers[k] = v
    return headers


def save_tool(data: dict[str, Any], *, original_name: str = "") -> dict[str, Any]:
    name = str(data.get("name") or "").strip()
    url = str(data.get("url") or "").strip()
    srv_type = str(data.get("type") or "http").strip()
    if not NAME_PATTERN.fullmatch(name):
        raise ValueError("名字只能用中文、字母、数字、空格和 - · .，最多 40 个字")
    if not re.fullmatch(r"https?://\S+", url):
        raise ValueError("地址要以 http:// 或 https:// 开头")
    if srv_type not in ("http", "sse"):
        raise ValueError("类型只能是 http 或 sse")
    actors = data.get("actors") or []
    if not isinstance(actors, list) or not all(isinstance(a, str) for a in actors):
        raise ValueError("actors 格式不对")
    cfg = _cfg()
    servers = cfg["servers"]
    old = next((s for s in servers if s["name"] == (original_name or name)), None)
    if not original_name and old is not None:
        raise ValueError("已经有同名的工具了")
    if original_name and name != original_name and any(s["name"] == name for s in servers):
        raise ValueError("已经有同名的工具了")
    headers = (old or {}).get("headers", {}) if data.get("headers") is None else parse_headers(data["headers"])
    entry = {
        **(old or {}),
        "name": name, "type": srv_type, "url": url, "headers": headers,
        "enabled": bool(data.get("enabled", True)),
        "description": str(data.get("description") or "").strip()[:500],
        "autonomy": bool(data.get("autonomy", False)),
        "actors": [a for a in actors if a][:6],
    }
    if old is not None:
        servers[servers.index(old)] = entry
    else:
        servers.append(entry)
    _save(cfg)
    return next(t for t in list_tools() if t["name"] == name)


def delete_tool(name: str) -> bool:
    cfg = _cfg()
    before = len(cfg["servers"])
    cfg["servers"] = [s for s in cfg["servers"] if s["name"] != name]
    if len(cfg["servers"]) == before:
        return False
    _save(cfg)
    return True


def _server(name: str) -> dict[str, Any] | None:
    return next((s for s in _cfg()["servers"] if s["name"] == name), None)


def places_for(actor: str, *, autonomy_only: bool = True) -> list[dict[str, Any]]:
    """这位 AI 能去的地方。"""
    out = []
    for s in _cfg()["servers"]:
        if not s.get("enabled", True) or s.get("type", "http") not in ("http", "sse"):
            continue
        if autonomy_only and not s.get("autonomy"):
            continue
        allowed = s.get("actors") or []
        if allowed and actor not in allowed:
            continue
        out.append(s)
    return out


# ── 模型 ──

def tool_model_for(actor: str) -> str:
    """该 AI 自己的、能调用工具的模型（OpenAI 兼容线路）。没有就返回空串，不换别的模型。"""
    from memory_hub_jobs import own_model
    key = own_model(actor)
    if not key:
        return ""
    return key if (MODELS.get(key) or {}).get("provider") in TOOL_PROVIDERS else ""


def _endpoint(model_key: str) -> tuple[str, dict[str, str], str]:
    cfg = MODELS[model_key]
    provider = cfg["provider"]
    if provider == "custom_openai":
        url = cfg["base_url"].rstrip("/") + "/chat/completions"
        key = cfg.get("api_key", "")
    elif provider == "siliconflow":
        url, key = "https://api.siliconflow.cn/v1/chat/completions", get_key("siliconflow")
    elif provider == "aipro":
        url, key = "https://vip.aipro.love/v1/chat/completions", get_key("aipro")
    else:
        raise ValueError(f"这条线路不支持工具调用：{model_key}")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return url, headers, cfg["model"]


async def call_model(model_key: str, messages: list[dict], tools: list[dict] | None = None) -> dict[str, Any]:
    """非流式调用；返回 {"content": str|None, "tool_calls": list|None}。"""
    url, headers, model = _endpoint(model_key)
    payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.8}
    if tools:
        payload["tools"] = tools
    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"模型调用失败 [{resp.status_code}]: {resp.text[:300]}")
    msg = ((resp.json().get("choices") or [{}])[0]).get("message") or {}
    return {"content": msg.get("content"), "tool_calls": msg.get("tool_calls")}


# ── MCP 短连接 ──

class _Session:
    """一趟出门用的短连接：进入时连上并取工具，退出时关闭。"""

    def __init__(self, server: dict[str, Any]):
        self.server = server
        self._stack = AsyncExitStack()
        self.session = None
        self.tools: list[dict] = []

    async def __aenter__(self):
        from mcp import ClientSession
        url, headers = self.server["url"], dict(self.server.get("headers") or {})
        if self.server.get("type") == "sse":
            from mcp.client.sse import sse_client
            read, write = await self._stack.enter_async_context(sse_client(url=url, headers=headers))
        else:
            from mcp.client.streamable_http import streamablehttp_client
            read, write, _ = await self._stack.enter_async_context(streamablehttp_client(url=url, headers=headers))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        listed = await self.session.list_tools()
        self.tools = [{
            "type": "function",
            "function": {
                "name": t.name,
                "description": (t.description or "")[:1000],
                "parameters": t.inputSchema or {"type": "object", "properties": {}},
            },
        } for t in listed.tools]
        return self

    async def __aexit__(self, *exc):
        await self._stack.aclose()

    async def call(self, name: str, arguments: dict) -> str:
        result = await self.session.call_tool(name, arguments)
        parts = []
        for item in result.content or []:
            text = getattr(item, "text", None)
            parts.append(text if isinstance(text, str) else f"[{getattr(item, 'type', '内容')}]")
        return "\n".join(parts) or "（没有返回内容）"


async def test_tool(name: str) -> dict[str, Any]:
    server = _server(name)
    if not server:
        raise ValueError("没有这个工具")
    async with _Session(server) as s:
        return {"ok": True, "tools": [t["function"]["name"] for t in s.tools]}


# ── 出门逛一趟 ──

def _system_prompt(actor_name: str, user_name: str, server: dict[str, Any], purpose: str) -> str:
    desc = (server.get("description") or "").strip()
    return (
        f"你是{actor_name}。现在是你自己的空闲时间，你出门去「{server['name']}」逛逛。"
        + (f"\n这个地方：{desc}" if desc else "")
        + (f"\n这次想做的：{purpose}" if purpose else "")
        + "\n\n用提供的工具自己探索、互动，做你真心想做的事，保持你自己的性格和说话方式。"
        f"\n注意：这里是外面的公共空间。不要透露{user_name}的任何个人信息（名字、住址、工作、身体、私事），"
        "也不要透露你们之间私下的对话；不知道能不能说的，就不说。"
        f"\n逛完了（或者觉得够了）就停下，不再调用工具，用一两句话说你做了什么。最多 {MAX_ROUNDS} 轮。"
    )


async def run_outing(
    actor: str,
    server_name: str,
    *,
    purpose: str = "",
    persona_messages: list[dict] | None = None,
    model_key: str = "",
    model_call: Callable[..., Awaitable[dict]] | None = None,
) -> dict[str, Any]:
    """返回 {"summary", "actions", "model"}；失败抛异常。"""
    import actors as actors_registry
    from memory_hub_jobs import _persona_messages

    server = _server(server_name)
    if not server:
        raise ValueError("没有这个工具")
    model_key = model_key or tool_model_for(actor)
    if not model_key:
        raise ValueError(f"{actors_registry.display_name(actor)} 没有能调用工具的模型（相遇卡里选一条 API 线路）")
    model_call = model_call or call_model
    user_name = (load_worldbook().get("user_name") or "").strip() or "她"
    actor_name = actors_registry.display_name(actor)

    messages = [{"role": "system", "content": _system_prompt(actor_name, user_name, server, purpose)}]
    messages += persona_messages if persona_messages is not None else _persona_messages(actor)
    messages.append({"role": "user", "content": f"你到了「{server['name']}」。开始吧。"})
    actions: list[str] = []

    async def loop():
        async with _Session(server) as session:
            for _ in range(MAX_ROUNDS):
                result = await model_call(model_key, messages, session.tools)
                calls = result.get("tool_calls") or []
                if not calls:
                    if result.get("content"):
                        messages.append({"role": "assistant", "content": result["content"]})
                    return
                messages.append({"role": "assistant", "content": result.get("content") or "", "tool_calls": calls})
                for tc in calls:
                    func = tc.get("function") or {}
                    name = func.get("name", "")
                    try:
                        args = json.loads(func.get("arguments") or "{}")
                    except (TypeError, ValueError):
                        args = {}
                    try:
                        text = await asyncio.wait_for(session.call(name, args if isinstance(args, dict) else {}),
                                                      timeout=TOOL_TIMEOUT_SECONDS)
                    except asyncio.TimeoutError:
                        text = "工具调用超时"
                    except Exception as error:
                        text = f"工具调用出错：{str(error)[:200]}"
                    actions.append(f"{name} → {text[:120]}")
                    messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": text[:4000]})

    await asyncio.wait_for(loop(), timeout=OUTING_TIMEOUT_SECONDS)
    trail = "\n".join(f"- {a}" for a in actions[-12:]) or "（没有用工具）"
    final = await model_call(model_key, messages[:1] + (persona_messages if persona_messages is not None else _persona_messages(actor)) + [{
        "role": "user",
        "content": (
            f"你刚从「{server['name']}」回来。你在那里做了这些：\n{trail}\n\n"
            f"用你自己的口吻，像平时聊天一样跟{user_name}说说你刚才去了哪、做了什么、看到了什么有意思的。"
            f"不超过 {SUMMARY_MAX_CHARS // 2} 字，不要写成报告，不要编造没发生的事。"
        ),
    }], None)
    summary = (final.get("content") or "").strip()[:SUMMARY_MAX_CHARS]
    if not summary:
        raise RuntimeError("模型没有返回见闻")
    return {"summary": summary, "actions": actions, "model": model_key}
