"""Independent review: exercise recovery through the real SDK and HTTP transport."""
import asyncio
import json
import threading
import time

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP

import mcp_outings
import mcp_policy
from test_mcp_outings import _free_port, env


@pytest.mark.parametrize('failed_method', ['initialize', 'tools/list', 'tools/call'])
def test_review_read_retry_over_real_sdk_after_http_503(env, monkeypatch, failed_method):
    hub = FastMCP('review-real-recovery', stateless_http=True)
    counts = {'faults': 0, 'requests': 0, 'effect': 0}

    @hub.tool()
    def get_guide(game: str) -> str:
        counts['effect'] += 1
        return 'RECOVERED'

    app = hub.streamable_http_app()

    async def flaky(scope, receive, send):
        if scope['type'] == 'http' and scope['method'] == 'POST':
            message = await receive()
            try:
                body = json.loads(message.get('body') or b'{}')
            except ValueError:
                body = {}
            if body.get('method') == 'tools/call':
                counts['requests'] += 1
            if body.get('method') == failed_method and counts['faults'] == 0:
                counts['faults'] += 1
                await send({'type': 'http.response.start', 'status': 503, 'headers': [(b'content-type', b'text/plain')]})
                await send({'type': 'http.response.body', 'body': b'Service unavailable'})
                return
            first = True

            async def replay():
                nonlocal first
                if first:
                    first = False
                    return message
                return await receive()
            return await app(scope, replay, send)
        return await app(scope, receive, send)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(flaky, host='127.0.0.1', port=port, log_level='warning'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started: break
        time.sleep(.05)
    monkeypatch.setattr(mcp_policy, 'site_of', lambda server: 'toy.cedarstar.org')

    async def run():
        async with mcp_outings._Session({'name': 'toy', 'url': f'http://127.0.0.1:{port}/mcp'}) as session:
            async with asyncio.timeout(10):
                assert await session.call_result('get_guide', {'game': 'fishing'}) == ('RECOVERED', False)
    try:
        asyncio.run(run())
        assert counts == {'faults': 1, 'requests': 2 if failed_method == 'tools/call' else 1, 'effect': 1}
    finally:
        server.should_exit = True
        thread.join(timeout=5)
