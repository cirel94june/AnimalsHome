"""Transient MCP failure recovery without replaying game/writing actions."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

import mcp_outings


SERVER = {'name': 'toy', 'url': 'https://toy.cedarstar.org/PRIVATE_TEST_TOKEN', 'headers': {'X-Test': 'TEST'}, 'type': 'http'}


def result(text='ok'):
    return SimpleNamespace(content=[SimpleNamespace(text=text)], isError=False)


def install(monkeypatch, scripts):
    opened, closed, calls = [], [], []
    @asynccontextmanager
    async def transport(index):
        owner = asyncio.current_task()
        try:
            yield
        finally:
            assert asyncio.current_task() is owner, 'MCP cleanup must run in its owning task'
            closed.append(index)
    async def open_session(self):
        index = len(opened)
        opened.append(self.server.copy())
        await self._stack.enter_async_context(transport(index))
        plan = list(scripts[index])
        if isinstance(plan[0], Exception):
            raise plan.pop(0)
        async def call(name, args):
            calls.append((index, name, args))
            item = plan.pop(0)
            if isinstance(item, Exception): raise item
            return item
        self.session = SimpleNamespace(call_tool=call)
        return self
    monkeypatch.setattr(mcp_outings._Session, '_open', open_session)
    return opened, closed, calls


def test_handshake_recovers_once_using_same_identity(monkeypatch):
    opened, closed, _ = install(monkeypatch, [[httpx.ConnectError('reset')], [result()]])
    async def run():
        async with mcp_outings._Session(SERVER): pass
    asyncio.run(run())
    assert opened == [SERVER, SERVER] and closed == [0, 1]


def test_read_query_reconnects_and_retries_once(monkeypatch):
    opened, closed, calls = install(monkeypatch, [[result(), httpx.ReadError('reset')], [result('recovered')]])
    async def run():
        async with mcp_outings._Session(SERVER) as session:
            await session.call_result('list_games', {})
            assert await session.call_result('get_guide', {'game': 'fishing'}) == ('recovered', False)
    asyncio.run(run())
    assert len(opened) == 2 and closed == [0, 1]
    assert [call[1] for call in calls] == ['list_games', 'get_guide', 'get_guide']


def test_game_action_is_not_replayed_and_next_status_can_reconnect(monkeypatch):
    opened, closed, calls = install(monkeypatch, [[result(), httpx.ReadError('unknown outcome')], [result('state')]])
    async def run():
        async with mcp_outings._Session(SERVER) as session:
            await session.call_result('list_games', {})
            with pytest.raises(httpx.ReadError):
                await session.call_result('play', {'game': 'fishing', 'action': 'cast'})
            assert await session.call_result('play', {'game': 'fishing', 'action': 'status'}) == ('state', False)
    asyncio.run(run())
    assert len(opened) == 2 and closed == [0, 1]
    assert [call[1] for call in calls].count('play') == 2


def test_auth_failure_is_not_retried(monkeypatch):
    response = httpx.Response(401, request=httpx.Request('POST', SERVER['url']))
    opened, closed, _ = install(monkeypatch, [[httpx.HTTPStatusError('auth', request=response.request, response=response)]])
    async def run():
        async with mcp_outings._Session(SERVER): pass
    with pytest.raises(httpx.HTTPStatusError): asyncio.run(run())
    assert len(opened) == 1 and closed == [0]
