"""网易云调用卡住时：网页按时得到回复，卡住的线程有上限、不越堆越多（Codex 审查 B1）。"""

import asyncio
import threading
import time

import pytest
from fastapi import HTTPException

import music
import routes.music_station as ms


def test_pyncm_requests_get_a_default_network_timeout(monkeypatch):
    import pyncm
    import requests
    seen = {}

    def fake_request(self, method, url, *args, **kwargs):
        seen.update(kwargs)
        return None
    monkeypatch.setattr(requests.Session, "request", fake_request)
    pyncm.Session.request(pyncm.Session(), "GET", "https://music.163.com/x")
    assert seen["timeout"] == music.NETEASE_TIMEOUT


def test_hung_calls_keep_their_slot_and_extra_requests_are_refused(monkeypatch):
    monkeypatch.setattr(ms, "NETEASE_WAIT_SECONDS", 0.05)
    release = threading.Event()
    started = []

    def hang(i):
        started.append(i)
        release.wait(5)
        return i

    async def run():
        results = await asyncio.gather(*(ms._run_netease(hang, i) for i in range(ms.NETEASE_MAX_IN_FLIGHT)),
                                       return_exceptions=True)
        assert all(isinstance(r, HTTPException) and r.status_code == 504 for r in results)
        # 超时返回了，但底层还在跑：名额没还，新的请求直接拒绝，不排队
        with pytest.raises(HTTPException) as busy:
            await ms._run_netease(hang, 99)
        assert busy.value.status_code == 503 and 99 not in started
    asyncio.run(run())
    assert ms._netease_in_flight == ms.NETEASE_MAX_IN_FLIGHT
    release.set()
    deadline = time.time() + 5
    while ms._netease_in_flight and time.time() < deadline:
        time.sleep(0.01)
    assert ms._netease_in_flight == 0  # 底层真正结束后才归还


def test_normal_calls_return_and_free_their_slot():
    async def run():
        return await ms._run_netease(lambda x: x * 2, 21)
    assert asyncio.run(run()) == 42
    deadline = time.time() + 2
    while ms._netease_in_flight and time.time() < deadline:
        time.sleep(0.01)
    assert ms._netease_in_flight == 0
