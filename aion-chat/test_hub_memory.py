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
        status = {"PUT": "updated", "DELETE": "deleted"}.get(request.method)
        if request.url.path.endswith("/archive"):
            status = "archived"
        if request.url.path.endswith("/unarchive"):
            status = "unarchived"
        return httpx.Response(200, json={"id": "abc-1", "status": status})

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



@pytest.mark.parametrize("reply", [{}, {"ok": False}, {"status": "denied"}, {"status": "guardrail_blocked"}, {"status": "updated"}])
def test_archive_needs_hubs_success_status(monkeypatch, fake_hub, reply):
    """§79：Hub 没明确说「archived」就不算成功（空对象、ok=false、别的状态、别的操作的成功状态都不算）。"""
    async def call(method, path, **kw):
        return reply
    monkeypatch.setattr(hub, "_call", call)
    with pytest.raises(HTTPException):
        asyncio.run(hub.archive_memory("abc-1"))


@pytest.mark.parametrize("op", ["edit", "archive", "delete", "unarchive"])
def test_not_found_is_404_everywhere(monkeypatch, fake_hub, op):
    async def call(method, path, **kw):
        return {"status": "not_found"}
    monkeypatch.setattr(hub, "_call", call)
    run = {"edit": lambda: hub.edit_memory("abc-1", hub.MemoryEdit(content="x")),
           "archive": lambda: hub.archive_memory("abc-1"),
           "delete": lambda: hub.delete_memory("abc-1", confirm="delete"),
           "unarchive": lambda: hub.unarchive_memory("abc-1")}[op]
    with pytest.raises(HTTPException) as err:
        asyncio.run(run())
    assert err.value.status_code == 404



@pytest.mark.parametrize("reply,ok", [({"status": "already_archived"}, True), ({"status": "refused", "reason": "anchored"}, False)])
def test_archive_new_hub_replies(monkeypatch, fake_hub, reply, ok):
    """§80：Hub 新协议：已经归档过的算成功；refused 算拒绝（不把 reason 原文给页面）。"""
    async def call(method, path, **kw):
        return reply
    monkeypatch.setattr(hub, "_call", call)
    if ok:
        assert asyncio.run(hub.archive_memory("abc-1"))["status"] == "already_archived"
    else:
        with pytest.raises(HTTPException) as err:
            asyncio.run(hub.archive_memory("abc-1"))
        assert err.value.status_code == 400 and "anchored" not in str(err.value.detail)


def test_list_passes_fact_ref_through(fake_hub, monkeypatch):
    """2026-10-30 Hub 把同一件事的补充挂在原来那条名下（fact_ref），页面要拿到它才能叠在一起。"""
    assert "fact_ref" in hub._FIELDS


def test_page_stacks_same_fact_under_the_original(tmp_path):
    import json
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node:
        pytest.skip("没有 node")
    page = (Path(__file__).parent / "static" / "hub-memory.js").read_text(encoding="utf-8")
    start = page.index("  function stacked(items)")
    fn = page[start:page.index("\n  }\n", start) + 4]
    items = [
        {"id": "a", "fact_ref": None}, {"id": "b", "fact_ref": "a"}, {"id": "c", "fact_ref": "b"},  # 两层都收到 a 下
        {"id": "d", "fact_ref": "gone"},                       # 指向的不在本页：单独显示，标「补充」
        {"id": "e", "fact_ref": "d"},                          # 收到 d 下
        {"id": "x", "fact_ref": "y"}, {"id": "y", "fact_ref": "x"},  # 互相指：各自单独
        {"id": "z"},
    ]
    script = tmp_path / "stack.js"
    script.write_text(fn + "\nconsole.log(JSON.stringify(stacked(" + json.dumps(items) + ").map("
                      "g => [g.m.id, g.extras.map(x => x.id), g.orphan])));", encoding="utf-8")
    result = subprocess.run([node, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        ["a", ["b", "c"], False], ["d", ["e"], True], ["x", [], True], ["y", [], True], ["z", [], False]]
