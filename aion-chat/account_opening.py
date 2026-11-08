"""替 AI 开户（11-08 小猫）：小猫在 MCP 工具页点，家里的程序去注册，钥匙直接存进这位 AI 自己的连接。

- AI 自己的出门规则不变：账号、注册、换钥匙仍然禁止，钥匙不经过模型，也不进聊天和日志。
- 密码只用这一次，不存、不记日志、不回给页面；钥匙只存进 mcp_servers.json 的地址里（和手动接的一样）。
- 现在有 4399；AIsay 的注册要邀请码或答题，以后在 SITES 里加。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import urlsplit

CALL_TIMEOUT = 30
_USERNAME = re.compile(r"^[A-Za-z0-9_一-鿿]{2,20}$")
_TOKEN = re.compile(r"^[A-Za-z0-9._~-]{16,200}$")
_locks: dict[str, asyncio.Lock] = {}


class OpeningError(Exception):
    """给小猫看的话，里面不带密码和钥匙。"""


def _toy_token_servers(actor: str) -> list[dict[str, Any]]:
    import mcp_outings
    out = []
    for server in mcp_outings._cfg()["servers"]:
        parts = urlsplit(str(server.get("url") or ""))
        if (parts.hostname == "toy.cedarstar.org" and parts.path.strip("/") not in ("", "mcp")
                and actor in (server.get("actors") or [])):
            out.append(server)
    return out


def has_account(site: str, actor: str) -> bool:
    return site == "4399" and bool(_toy_token_servers(actor))


def _connection_name(actor: str) -> str:
    import actors
    return f"{actors.display_name(actor)}4399"


def _find_token(data: Any) -> str:
    holders = [data] + ([data["data"]] if isinstance(data, dict) and isinstance(data.get("data"), dict) else [])
    for holder in holders:
        token = holder.get("token") if isinstance(holder, dict) else None
        if isinstance(token, str) and _TOKEN.fullmatch(token.strip()):
            return token.strip()
    return ""


def _safe(text: Any, *secrets: str) -> str:
    out = str(text or "")
    for secret in secrets:
        if secret:
            out = out.replace(secret, "***")
    return out.replace("【cedartoy】", "")[:200]


async def open_toy_account(actor: str, username: Any, password: Any) -> dict[str, Any]:
    """注册 4399 账号，存成只给这位 AI 用的连接。返回 {connection, username}。"""
    import actors
    import mcp_outings
    import turtle_soup
    if actor not in {a["id"] for a in actors.list_actors(include_disabled=False)}:
        raise OpeningError("没有这位 AI")
    username, password = str(username or "").strip(), str(password or "")
    if not _USERNAME.fullmatch(username):
        raise OpeningError("用户名要 2 到 20 个字，只能用中文、字母、数字和下划线")
    if len(password) < 6 or len(password) > 128:
        raise OpeningError("密码至少 6 位")
    lock = _locks.setdefault(f"4399:{actor}", asyncio.Lock())
    if lock.locked():
        raise OpeningError("正在给 TA 开户，等一下")
    async with lock:
        if has_account("4399", actor):
            raise OpeningError("TA 已经有 4399 账号了")
        name = _connection_name(actor)
        if mcp_outings._server(name):  # 注册之前先查，免得注册成功了却存不下
            raise OpeningError(f"工具页已经有「{name}」了，先改名或删掉")
        guest = turtle_soup._guest_server()
        if not guest:
            raise OpeningError("工具页要先有一个 4399 的游客连接（toy.cedarstar.org/mcp）")
        try:
            async with asyncio.timeout(CALL_TIMEOUT):
                async with mcp_outings._Session(guest) as session:
                    text, is_error = await session.call_result(
                        "account", {"action": "login_or_register", "username": username, "password": password})
        except Exception as error:
            print(f"[开户] 4399 调用失败: {type(error).__name__}")
            raise OpeningError("4399 连不上，不确定有没有注册上。先别重试，告诉我") from None
        if is_error:
            raise OpeningError(_safe(text, password) or "4399 没有同意注册")
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        token = _find_token(data)
        if not token:
            detail = (data.get("message") or data.get("error")) if isinstance(data, dict) else ""
            raise OpeningError(_safe(detail, password) or "4399 没有给钥匙，这次没有存")
        try:
            mcp_outings.save_tool({"name": name, "url": f"https://toy.cedarstar.org/{token}", "type": "http",
                                   "description": "替 AI 开户时自动接上的 4399 账号", "autonomy": False,
                                   "actors": [actor]})
        except Exception as error:
            print(f"[开户] 钥匙保存失败: {type(error).__name__}")
            raise OpeningError("账号注册好了，但没存上。别重复注册，告诉我") from None
        return {"connection": name, "username": username}


SITES = {"4399": open_toy_account}


def overview() -> dict[str, Any]:
    import actors
    return {"sites": [{"id": "4399", "name": "4399 小游戏"}],
            "actors": [{"id": a["id"], "name": a["name"], "has": {"4399": has_account("4399", a["id"])}}
                       for a in actors.list_actors(include_disabled=False)]}
