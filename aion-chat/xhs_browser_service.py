"""On-demand owner login helper. Separate Unix user, persistent private profile.

Only fixed Xiaohongshu navigation is supported. No CDP listener, arbitrary URL,
typing, publication, or model tool interface. The application validates the
account through its existing worker before accepting these credentials.
"""
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import time

ROOT = Path('/var/lib/xhs-browser')
SOCKET = Path('/run/xhs-browser/api.sock')
HOME_URL = 'https://www.xiaohongshu.com/explore'


def atomic_json(path, data):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.session-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(data, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):os.unlink(name)


class LoginBrowser:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.driver = self.context = self.page = None
        self.last_used = 0
        self.login_deadline = 0
        self.refresh_cache = None

    async def close(self):
        context, driver = self.context, self.driver
        self.context = self.driver = self.page = None
        self.login_deadline = 0
        try:
            if context:await asyncio.wait_for(context.close(), 10)
        finally:
            if driver:await asyncio.wait_for(driver.stop(), 10)

    async def open(self):
        self.last_used = time.monotonic()
        if self.page and not self.page.is_closed():return
        await self.close()
        memory={k:int(v.split()[0]) for k,v in (line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())}
        if memory['MemAvailable']<350*1024:
            raise RuntimeError('insufficient_memory')
        try:
            await self._launch()
        except BaseException:
            await self.close()
            raise

    async def _launch(self):
        from playwright.async_api import async_playwright
        self.driver = await async_playwright().start()
        self.context = await self.driver.chromium.launch_persistent_context(
            user_data_dir=str(ROOT/'profile'), executable_path='/usr/bin/google-chrome',
            headless=False, chromium_sandbox=True, viewport={'width':640,'height':720},
            args=['--disable-dev-shm-usage','--no-first-run','--no-default-browser-check',
                  '--autoplay-policy=user-gesture-required'])
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        for other in self.context.pages:
            if other != self.page:await other.close()
        await self.page.goto(HOME_URL, wait_until='domcontentloaded', timeout=45000)

    async def candidate(self):
        cookies = await self.context.cookies(['https://www.xiaohongshu.com'])
        # Only cookies applicable to this origin are sent to the existing worker.
        pairs = {}
        for item in cookies:
            if item.get('path','/') != '/':continue
            if item.get('expires',-1)>0 and item['expires']<=time.time():continue
            name, value = item['name'], item['value']
            if any(c in name+value for c in '\r\n;'):continue
            pairs[name] = value
        if not pairs.get('web_session'):return {'cookie':'','updated_at':time.time()}
        return {'cookie':'; '.join(name+'='+value for name,value in pairs.items()),'updated_at':time.time()}

    async def call(self, path, data=None):
        async with self.lock:
            if path == '/status':
                return {'available':True,'running':self.page is not None}, 'application/json'
            if path == '/cancel':
                await self.close()
                return {'ok':True}, 'application/json'
            if path == '/credentials':
                saved = ROOT/'session.json'
                try:record = json.loads(saved.read_text(encoding='utf-8'))
                except (OSError, ValueError):record = {}
                if not record.get('cookie'):raise RuntimeError('login_confirmation_required')
                if self.login_deadline:
                    return record, 'application/json'
                if record.get('cookie') and time.time()-record.get('updated_at',0)<4*3600:
                    return record, 'application/json'
                if self.refresh_cache and time.time()-self.refresh_cache['updated_at']<4*3600:
                    return self.refresh_cache, 'application/json'
                await self.open()
                await self.page.reload(wait_until='domcontentloaded',timeout=45000)
                await asyncio.sleep(2)
                record = await self.candidate()
                # Refresh is only a candidate: the app must validate its account.
                # Only explicit /accept can replace the persisted accepted login.
                if record['cookie']:self.refresh_cache=record
                if not self.login_deadline:await self.close()
                return record, 'application/json'
            if path == '/start':
                self.refresh_cache = None
                await self.open()
                self.login_deadline = time.monotonic()+300
                # The platform may show the login modal automatically. Otherwise
                # only the fixed login control is clicked, never a user URL.
                modal = self.page.locator('.login-container, .login-modal').first
                if not await modal.is_visible():
                    login = self.page.get_by_text('登录',exact=True).first
                    if await login.is_visible():await login.click(timeout=5000)
                return {'ok':True,'expires_in':300}, 'application/json'
            if path == '/frame':
                if not self.page or time.monotonic()>self.login_deadline:
                    raise RuntimeError('login_expired')
                self.last_used = time.monotonic()
                modal = self.page.locator('.login-container, .login-modal').first
                target = modal if await modal.is_visible() else self.page
                return await target.screenshot(type='png',timeout=5000), 'image/png'
            if path == '/candidate':
                if not self.page or time.monotonic()>self.login_deadline:
                    raise RuntimeError('login_expired')
                return await self.candidate(), 'application/json'
            if path == '/accept':
                # App calls only after platform account verification and the
                # owner's explicit confirmation. No unverified login is committed.
                record = await self.candidate()
                if not record['cookie']:raise RuntimeError('login_required')
                expected = (data or {}).get('expected_hash','')
                if not isinstance(expected,str) or not hmac.compare_digest(
                    expected,hashlib.sha256(record['cookie'].encode()).hexdigest()):
                    raise RuntimeError('account_changed')
                atomic_json(ROOT/'session.json',record)
                self.refresh_cache = None
                await self.close()
                return {'ok':True}, 'application/json'
            raise RuntimeError('unsupported_operation')

    async def reap(self):
        failures = 0
        while True:
            await asyncio.sleep(10)
            async with self.lock:
                if failures:raise RuntimeError('browser_cleanup_failed')
                now = time.monotonic()
                if self.page and ((self.login_deadline and now>self.login_deadline) or
                                  (not self.login_deadline and now-self.last_used>60)):
                    try:
                        await self.close()
                        failures = 0
                    except Exception:
                        failures += 1
                        if failures >= 2:raise


async def supervise(server,browser):
    """A lost reaper must stop the unit so systemd kills the complete tree."""
    serving=asyncio.create_task(server.serve_forever())
    reaper=asyncio.create_task(browser.reap())
    try:
        done,_=await asyncio.wait({serving,reaper},return_when=asyncio.FIRST_COMPLETED)
        for task in done:task.result()
        raise RuntimeError('browser_supervisor_stopped')
    finally:
        serving.cancel();reaper.cancel()
        await asyncio.gather(serving,reaper,return_exceptions=True)


async def serve():
    os.umask(0o077)
    browser = LoginBrowser()
    allowed = {'/status','/start','/cancel','/frame','/candidate','/accept','/credentials'}

    async def handle(reader, writer):
        status = 200
        try:
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
            if len(header)>4096:raise ValueError()
            method, path, version = header.split(b'\r\n',1)[0].decode('ascii').split(' ')
            if method!='POST' or path not in allowed or version!='HTTP/1.1':raise ValueError()
            headers = dict(line.lower().split(b':',1) for line in header.split(b'\r\n')[1:] if line)
            if b'transfer-encoding' in headers:raise ValueError()
            length=int(headers.get(b'content-length',b'0'))
            if not 0<=length<=512:raise ValueError()
            raw=await asyncio.wait_for(reader.readexactly(length),5)
            data=json.loads(raw) if raw else {}
            if not isinstance(data,dict):raise ValueError()
            result, content_type = await asyncio.wait_for(browser.call(path,data), 85)
            body = result if isinstance(result,bytes) else json.dumps(result).encode()
        except Exception:
            status, content_type, body = 503,'application/json',b'{"error":"browser_unavailable"}'
        try:
            writer.write(f'HTTP/1.1 {status} Result\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n'.encode()+body)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    SOCKET.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(handle, path=str(SOCKET), limit=4097)
    SOCKET.chmod(0o660)
    try:
        async with server:await supervise(server,browser)
    finally:
        await browser.close()


if __name__=='__main__':asyncio.run(serve())
