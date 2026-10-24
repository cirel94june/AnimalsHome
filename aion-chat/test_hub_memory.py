"""记忆库的「Hub 记忆」：服务器替小猫去 Hub 取 / 改 / 归档 / 删每位 AI 的记忆，密钥不出服务器。"""

import asyncio

import httpx
import pytest
from fastapi import HTTPException

import actors
import memory_hub_bridge
import routes.hub_memory as hub


@pytest.fixture
def fake_hub(monkeypatch):
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        if request.url.path == "/api/memory/list":
            return httpx.Response(200, json={"total": 1, "items": [
                {"id": "abc-1", "content": "狗蛋记得小猫爱吃草莓", "layer": "private", "embedding": [0.1], "history": ["x"]}]})
        if "/missing" in request.url.path:
            return httpx.Response(404, json={"detail": "nope"})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(hub.httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))
    monkeypatch.setattr(memory_hub_bridge, "load_config", lambda: {
        "enabled": True, "url": "http://127.0.0.1:8888/mcp", "token": "SECRET"})
    monkeypatch.setattr(actors, "memory_hub_id", lambda actor: {"aion": "claude", "ai3": "jasper"}.get(actor, ""))
    return seen


def test_list_uses_rest_with_secret_and_trims_fields(fake_hub):
    data = asyncio.run(hub.list_memories("ai3", status="active", page=1, per_page=20))
    request = fake_hub[0]
    assert str(request.url).startswith("http://127.0.0.1:8888/api/memory/list")  # 去掉 /mcp，走管理接口
    assert request.url.params["ai_id"] == "jasper" and request.headers["authorization"] == "Bearer SECRET"
    item = data["items"][0]
    assert item["content"] == "狗蛋记得小猫爱吃草莓" and "embedding" not in item and "history" not in item
    assert "SECRET" not in str(data)  # 密钥不会出现在返回给浏览器的内容里


def test_seat_without_hub_or_unknown_seat_is_refused(fake_hub):
    with pytest.raises(HTTPException) as err:
        asyncio.run(hub.list_memories("ai4", status="active", page=1, per_page=20))
    assert err.value.status_code == 400
    with pytest.raises(HTTPException) as err:
        asyncio.run(hub.list_memories("nobody", status="active", page=1, per_page=20))
    assert err.value.status_code == 404
    assert fake_hub == []  # 没有发到 Hub


def test_edit_archive_delete(fake_hub):
    asyncio.run(hub.edit_memory("abc-1", hub.MemoryEdit(content="  新内容 ", importance=7)))
    put = fake_hub[-1]
    assert put.method == "PUT" and put.url.path == "/api/memory/abc-1"
    import json
    assert json.loads(put.content) == {"content": "新内容", "importance": 7, "changed_by": "ceci"}
    asyncio.run(hub.archive_memory("abc-1"))
    assert fake_hub[-1].method == "POST" and fake_hub[-1].url.path == "/api/memory/abc-1/archive"
    with pytest.raises(HTTPException):  # 彻底删除必须带确认
        asyncio.run(hub.delete_memory("abc-1", confirm=""))
    asyncio.run(hub.delete_memory("abc-1", confirm="delete"))
    assert fake_hub[-1].method == "DELETE"
    with pytest.raises(HTTPException) as err:
        asyncio.run(hub.archive_memory("missing"))
    assert err.value.status_code == 404


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "a" * 81, "id?x=1"])
def test_bad_memory_ids_never_reach_hub(fake_hub, bad):
    with pytest.raises(HTTPException):
        asyncio.run(hub.archive_memory(bad))
    assert fake_hub == []


def test_hub_not_connected(monkeypatch):
    monkeypatch.setattr(memory_hub_bridge, "load_config", lambda: {"enabled": False, "url": "", "token": ""})
    monkeypatch.setattr(actors, "memory_hub_id", lambda actor: "jasper")
    with pytest.raises(HTTPException) as err:
        asyncio.run(hub.list_memories("ai3", status="active", page=1, per_page=20))
    assert err.value.status_code == 503
