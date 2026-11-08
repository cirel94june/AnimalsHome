"""中转线路断线自动重试（11-08）：还没回话就断才重试一次，说了一半不重发。"""
import asyncio

import httpx
import pytest

import ai_providers as ap


def run(script, monkeypatch, meta=None):
    calls = []

    async def once(messages, cfg, meta_arg=None, temperature=None, max_tokens=None, request_timeout=None):
        steps = script[len(calls)]
        calls.append(1)
        for step in steps:
            if isinstance(step, Exception):
                raise step
            yield step
    monkeypatch.setattr(ap, "_call_custom_openai_once", once)
    monkeypatch.setattr(ap, "RELAY_RETRY_DELAY", 0)

    async def collect():
        out = []
        async for piece in ap.call_custom_openai([], {}, meta):
            out.append(piece)
        return out
    return asyncio.run(collect()), calls


DROP = httpx.RemoteProtocolError("Server disconnected without sending a response.")


@pytest.mark.parametrize("error", [DROP, httpx.ConnectError("x"), httpx.ReadError("x"), httpx.WriteTimeout("x")])
def test_retries_once_when_nothing_was_said(monkeypatch, error):
    out, calls = run([[error], ["你好"]], monkeypatch)
    assert len(calls) == 2 and out[-1] == "你好"
    assert sum(1 for p in out if not p.startswith(ap.CLI_STATUS_PREFIX)) == 1


def test_status_lines_do_not_count_as_speaking(monkeypatch):
    out, calls = run([[f"{ap.CLI_STATUS_PREFIX}正在思考...", DROP], ["好"]], monkeypatch)
    assert len(calls) == 2 and out[-1] == "好"


def test_no_retry_after_text_started(monkeypatch):
    with pytest.raises(httpx.RemoteProtocolError):
        run([["说了一半", DROP], ["不该到这"]], monkeypatch)


def test_gives_up_after_second_drop(monkeypatch):
    with pytest.raises(httpx.RemoteProtocolError):
        run([[DROP], [DROP], ["不该到这"]], monkeypatch)


def test_slow_reply_is_not_retried(monkeypatch):
    with pytest.raises(httpx.ReadTimeout):
        run([[httpx.ReadTimeout("x")], ["不该到这"]], monkeypatch)


def test_partial_thinking_is_dropped_before_retry(monkeypatch):
    meta = {"reasoning_content": "想到一半"}
    run([[DROP], ["好"]], monkeypatch, meta)
    assert "reasoning_content" not in meta
