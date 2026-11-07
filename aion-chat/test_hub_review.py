"""家里的记忆 · 待确认（Hub 接口说明第 8 节）。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import memory_hub_bridge
from routes import hub_review

OWNER = "OWNER_SECRET_CANARY"
SHARED = "SHARED_SECRET_CANARY"


class Response:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


@pytest.fixture
def hub(monkeypatch):
    calls, answers = [], []
    monkeypatch.setenv("MEMORY_HUB_OWNER_SECRET", OWNER)
    monkeypatch.setattr(memory_hub_bridge, "load_config",
                        lambda: {"enabled": True, "url": "https://hub.example/mcp", "token": SHARED})
    monkeypatch.setattr(memory_hub_bridge, "_is_active", lambda cfg: True)

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, headers=None, **kwargs):
            calls.append({"method": method, "url": url, "headers": dict(headers or {}), **kwargs})
            return answers.pop(0)
    monkeypatch.setattr(hub_review.httpx, "AsyncClient", Client)
    app = FastAPI()
    app.include_router(hub_review.router)
    return TestClient(app), calls, answers


def test_summary_uses_the_shared_key(hub):
    client, calls, answers = hub
    answers.append(Response(200, {"waiting": 3, "review_after": 2, "oldest_at": "x", "extra": "no"}))
    assert client.get("/api/hub-review/summary").json() == {"waiting": 3, "review_after": 2, "oldest_at": "x"}
    assert calls[0]["url"] == "https://hub.example/api/proposals/summary"
    assert calls[0]["headers"]["Authorization"] == f"Bearer {SHARED}"


def test_list_uses_the_owner_key_and_never_returns_it(hub):
    client, calls, answers = hub
    answers.append(Response(200, {"total": 1, "page": 1, "items": [{
        "id": "p1", "content": "她喜欢芒果", "proposed_room": "living_room", "secret_field": "x",
        "conflict_targets": [{"id": "m1", "content": "她不喜欢芒果", "updated_at": "2026-11-07T01:00:00", "junk": 1}]}]}))
    response = client.get("/api/hub-review/list?status=review_after")
    assert calls[0]["headers"]["Authorization"] == f"Bearer {OWNER}"
    assert calls[0]["params"]["status"] == "review_after"
    body = response.json()
    assert "secret_field" not in body["items"][0] and "junk" not in body["items"][0]["conflict_targets"][0]
    assert OWNER not in response.text and SHARED not in response.text


def test_list_shows_names_not_hub_ids(hub, monkeypatch):
    client, _, answers = hub
    monkeypatch.setattr(hub_review, "_hub_names", lambda: {"jasper": "狗蛋"})
    answers.append(Response(200, {"items": [{"id": "p1", "content": "x", "proposer_ai_id": "Jasper"}]}))
    assert client.get("/api/hub-review/list").json()["items"][0]["who"] == "狗蛋"


def test_no_owner_key_means_no_review(hub, monkeypatch):
    client, calls, _ = hub
    monkeypatch.delenv("MEMORY_HUB_OWNER_SECRET")
    assert client.get("/api/hub-review/list").status_code == 503
    assert client.post("/api/hub-review/p1", json={"action": "seen"}).status_code == 503
    assert calls == []  # 不会退回用共用钥匙去审批


def test_each_button_sends_what_the_doc_says(hub):
    client, calls, answers = hub
    for _ in range(5):
        answers.append(Response(200, {"status": "ok"}))
    client.post("/api/hub-review/p1", json={"action": "approve"})
    client.post("/api/hub-review/p1", json={"action": "approve", "content": " 她写的 "})
    client.post("/api/hub-review/p1", json={"action": "approve", "retire_ids": [{"id": "m1", "updated_at": "2026-11-07T01:00:00.123+00:00"}]})
    client.post("/api/hub-review/p1", json={"action": "reject"})
    client.post("/api/hub-review/p1", json={"action": "seen", "retire": True})
    bodies = [c["json"] for c in calls]
    assert bodies[0] == {"action": "approve"}
    assert bodies[1] == {"action": "approve", "content": "她写的"}
    assert bodies[2]["retire_ids"] == [{"id": "m1", "updated_at": "2026-11-07T01:00:00.123+00:00"}]  # 版本原样带
    assert bodies[3] == {"action": "reject", "reject_reason": ""}
    assert bodies[4] == {"action": "seen", "retire": True}
    assert all(c["headers"]["Authorization"] == f"Bearer {OWNER}" for c in calls)
    assert calls[0]["url"] == "https://hub.example/api/proposals/p1/review"


@pytest.mark.parametrize("status,data,expect_code,expect_text", [
    (409, {"detail": "busy"}, 409, "刷新"),                   # 不能当成已保存
    (200, {"error": "legacy_operator_only"}, 422, "服务器上处理"),  # 200 带 error 也是失败
    (200, {"error": "别的原因"}, 422, "没保存"),
    (503, {"detail": "x"}, 503, "再点一次"),
    (404, {"detail": "x"}, 404, "刷新"),
    (403, {"detail": "x"}, 502, "钥匙"),
    (400, {"detail": "retire_ids 不对"}, 400, "retire_ids 不对"),
    (200, ValueError("坏了"), 502, "看不懂"),
    (500, {}, 502, "不算保存"),
])
def test_return_codes(hub, status, data, expect_code, expect_text):
    client, _, answers = hub
    answers.append(Response(status, data))
    response = client.post("/api/hub-review/p1", json={"action": "seen"})
    assert response.status_code == expect_code and expect_text in response.json()["detail"]


def test_already_done_counts_as_success(hub):
    client, _, answers = hub
    answers.append(Response(200, {"already": True}))
    assert client.post("/api/hub-review/p1", json={"action": "seen"}).json()["already"] is True


def test_bad_input_never_reaches_the_hub(hub):
    client, calls, _ = hub
    assert client.post("/api/hub-review/p1", json={"action": "adopt_legacy"}).status_code == 422
    assert client.post("/api/hub-review/bad%20id", json={"action": "seen"}).status_code == 400
    assert client.post("/api/hub-review/p1", json={"action": "seen", "retire_ids": [{"id": "m"}]}).status_code == 422
    assert calls == []
