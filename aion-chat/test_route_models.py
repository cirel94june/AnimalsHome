import asyncio

import httpx
import pytest
from fastapi import HTTPException

import routes.settings as rs

REAL_CLIENT = httpx.AsyncClient


def test_models_url_follows_the_same_rules_as_chat_requests():
    assert rs._models_url("https://relay.test") == "https://relay.test/v1/models"
    assert rs._models_url("https://relay.test/v1/") == "https://relay.test/v1/models"
    assert rs._models_url("https://relay.test/v1/chat/completions") == "https://relay.test/v1/models"
    for bad in ("ftp://relay.test", "https://u:p@relay.test", "https://relay.test/v1?x=1", "relay.test"):
        with pytest.raises(HTTPException):
            rs._models_url(bad)


def test_model_ids_accepts_openai_and_gemini_shapes():
    assert rs._model_ids({"data": [{"id": "gpt-5"}, {"id": "claude-x"}, {"id": "gpt-5"}]}) == ["claude-x", "gpt-5"]
    assert rs._model_ids({"models": [{"name": "models/gemini-3-pro"}]}) == ["gemini-3-pro"]
    assert rs._model_ids(["a", 3, None]) == ["a"]
    assert rs._model_ids({"error": "x"}) == []


def _patch_relay(monkeypatch, handler):
    seen = []

    def handle(request):
        seen.append(request)
        return handler(request)
    monkeypatch.setattr(rs.httpx, "AsyncClient",
                        lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handle), **kw))
    return seen


def _fetch(**body):
    return asyncio.run(rs.fetch_route_models(rs.RouteModelsQuery(**body)))


def test_fetch_sends_bearer_key_and_returns_sorted_ids(monkeypatch):
    seen = _patch_relay(monkeypatch, lambda r: httpx.Response(200, json={"data": [{"id": "b"}, {"id": "A"}]}))
    assert _fetch(base_url="https://relay.test/v1", api_key=" sk-1 ") == {"models": ["A", "b"]}
    assert str(seen[0].url) == "https://relay.test/v1/models"
    assert seen[0].headers["authorization"] == "Bearer sk-1"


@pytest.mark.parametrize("status, words", [(401, "Key 不对"), (404, "不支持"), (502, "502")])
def test_fetch_explains_relay_errors(monkeypatch, status, words):
    _patch_relay(monkeypatch, lambda r: httpx.Response(status))
    with pytest.raises(HTTPException) as err:
        _fetch(base_url="https://relay.test", api_key="k")
    assert words in err.value.detail


def test_fetch_rejects_non_list_and_oversized_answers(monkeypatch):
    _patch_relay(monkeypatch, lambda r: httpx.Response(200, text="<html>"))
    with pytest.raises(HTTPException):
        _fetch(base_url="https://relay.test")
    monkeypatch.setattr(rs, "MODEL_LIST_MAX_BYTES", 10)
    _patch_relay(monkeypatch, lambda r: httpx.Response(200, json={"data": [{"id": "x" * 50}]}))
    with pytest.raises(HTTPException) as err:
        _fetch(base_url="https://relay.test")
    assert "太大" in err.value.detail
