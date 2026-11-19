"""Private Unix-socket client; credentials never returned through public routes."""
from pathlib import Path
import httpx

SOCKET = '/run/xhs-browser/api.sock'


def configured():
    import xhs_remote
    if xhs_remote.load():return True
    return Path(SOCKET).exists()


async def call(operation, data=None):
    if operation not in {'status','start','cancel','frame','candidate','accept','credentials'}:
        raise ValueError('unsupported_browser_operation')
    import xhs_remote
    if xhs_remote.load():return await xhs_remote.call(operation,data)
    try:
        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=SOCKET),timeout=90) as client:
            response = await client.post('http://localhost/'+operation,json=data or {})
            response.raise_for_status()
            if operation=='frame':return response.content
            return response.json()
    except (httpx.HTTPError,ValueError,OSError):
        raise RuntimeError('云端登录暂时不可用，请稍后重试。') from None
