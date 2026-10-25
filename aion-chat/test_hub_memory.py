"""记忆库的「Hub 记忆」：服务器替小猫去 Hub 取 / 改 / 归档 / 删每位 AI 的记忆，密钥不出服务器。"""

import asyncio

import httpx
import pytest
from fastapi import HTTPException

import actors
import memory_hub_bridge
import routes.hub_memory as hub

REAL_CLIENT = httpx.AsyncClient


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
    asyncio.run(hub.edit_memory("abc-1", hub.MemoryEdit(content="  新内容 ", importance=0.7)))
    put = fake_hub[-1]
    assert put.method == "PUT" and put.url.path == "/api/memory/abc-1"
    import json
    assert json.loads(put.content) == {"content": "新内容", "importance": 0.7, "changed_by": "ceci"}
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


def test_unarchive_and_owner_key(fake_hub, monkeypatch):
    asyncio.run(hub.unarchive_memory("abc-1"))
    assert fake_hub[-1].method == "POST" and fake_hub[-1].url.path == "/api/memory/abc-1/unarchive"
    assert fake_hub[-1].headers["authorization"] == "Bearer SECRET"
    monkeypatch.setenv("MEMORY_HUB_OWNER_SECRET", "OWNER")  # 主人钥匙上线后：这一页改用它
    asyncio.run(hub.archive_memory("abc-1"))
    assert fake_hub[-1].headers["authorization"] == "Bearer OWNER"


def test_unarchive_not_found(monkeypatch, fake_hub):
    async def call(method, path, **kw):
        return {"status": "not_found"}
    monkeypatch.setattr(hub, "_call", call)
    with pytest.raises(HTTPException) as err:
        asyncio.run(hub.unarchive_memory("abc-1"))
    assert err.value.status_code == 404


def test_importance_uses_hubs_zero_to_one_scale():
    """§78 M3：Hub 的重要度是 0～1；1～10 的整数不能直接写进去。"""
    with pytest.raises(Exception):
        hub.MemoryEdit(importance=5)
    assert hub.MemoryEdit(importance=0.5).importance == 0.5


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="<html>login</html>"),          # 登录页之类：不当成功
    httpx.Response(200, json=["not", "an", "object"]),
    httpx.Response(200, json={"error": "secret detail from hub"}),
])
def test_unclear_hub_responses_are_not_success(monkeypatch, fake_hub, response):
    """§78 L3：看不懂的回应不算成功；Hub 的错误原文不传给页面。"""
    transport = httpx.MockTransport(lambda request: response)
    monkeypatch.setattr(hub.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=transport, **kw))
    with pytest.raises(HTTPException) as err:
        asyncio.run(hub.archive_memory("abc-1"))
    assert "secret detail" not in str(err.value.detail)


def test_mutations_return_only_id_and_status(monkeypatch, fake_hub):
    async def call(method, path, **kw):
        return {"status": "archived", "content": "私事", "embedding": [1], "history": ["x"]}
    monkeypatch.setattr(hub, "_call", call)
    assert asyncio.run(hub.archive_memory("abc-1")) == {"ok": True, "id": "abc-1", "status": "archived"}
