"""Independent review: cancellation and non-replayed writes over the real MCP SDK."""
import asyncio
import json
import threading
import time
from contextlib import contextmanager

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP

import mcp_outings
import mcp_policy
from test_mcp_outings import _free_port, env


@contextmanager
def server_case(mode):
    hub = FastMCP('review-lifecycle', stateless_http=True)
    counts = {'initialize': 0, 'calls': 0, 'game_effects': 0}
    entered = threading.Event()

    @hub.tool()
    async def get_guide(game: str) -> str:
        entered.set()
        if mode == 'wait':
            await asyncio.sleep(1)
        return 'guide'

    @hub.tool()
    async def play(game: str, action: str) -> str:
        return 'STATE'

    app = hub.streamable_http_app()

    async def wrapper(scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'POST':
            return await app(scope, receive, send)
        message = await receive()
        try: body = json.loads(message.get('body') or b'{}')
        except ValueError: body = {}
        if body.get('method') == 'initialize': counts['initialize'] += 1
        if body.get('method') == 'tools/call':
            counts['calls'] += 1
            params = body.get('params', {})
            if mode == 'game_failure' and params.get('name') == 'play' and params.get('arguments', {}).get('action') == 'cast':
                # The external operation was committed before its HTTP response failed.
                counts['game_effects'] += 1
                await send({'type': 'http.response.start', 'status': 503, 'headers': [(b'content-type', b'text/plain')]})
                await send({'type': 'http.response.body', 'body': b'Service unavailable after commit'})
                return
        first = True

        async def replay():
            nonlocal first
            if first:
                first = False
                return message
            return await receive()
        return await app(scope, replay, send)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(wrapper, host='127.0.0.1', port=port, log_level='warning'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started: break
        time.sleep(.05)
    try:
        yield {'name': 'toy', 'url': f'http://127.0.0.1:{port}/mcp'}, counts, entered
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.parametrize('cancel_kind', ['cancel', 'timeout'])
def test_review_external_cancel_does_not_retry_and_closes_owner(env, monkeypatch, cancel_kind):
    monkeypatch.setattr(mcp_policy, 'site_of', lambda server: 'toy.cedarstar.org')
    with server_case('wait') as (server, counts, entered):
        async def run():
            async with mcp_outings._Session(server) as session:
                owner = session._owner
                if cancel_kind == 'cancel':
                    task = asyncio.create_task(session.call_result('get_guide', {'game': 'fishing'}))
                    for _ in range(100):
                        if entered.is_set(): break
                        await asyncio.sleep(.01)
                    assert entered.is_set()
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError): await task
                else:
                    with pytest.raises(TimeoutError):
                        async with asyncio.timeout(.2):
                            await session.call_result('get_guide', {'game': 'fishing'})
                assert owner.done(), 'owner task must be closed when caller is cancelled'
            assert counts['initialize'] == 1
            assert counts['calls'] == 1
        asyncio.run(run())


def test_review_game_committed_before_transport_error_is_not_replayed(env, monkeypatch):
    monkeypatch.setattr(mcp_policy, 'site_of', lambda server: 'toy.cedarstar.org')
    with server_case('game_failure') as (server, counts, _):
        async def run():
            async with mcp_outings._Session(server) as session:
                with pytest.raises(Exception):
                    await session.call_result('play', {'game': 'fishing', 'action': 'cast'})
                assert counts['game_effects'] == 1
                assert await session.call_result('play', {'game': 'fishing', 'action': 'status'}) == ('STATE', False)
            assert counts == {'initialize': 2, 'calls': 2, 'game_effects': 1}
        asyncio.run(run())
