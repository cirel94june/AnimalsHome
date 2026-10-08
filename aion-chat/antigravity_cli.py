"""Official agy subprocess transport and a minimal companion profile."""

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path


AGENT_NAME = 'home-companion'
CHAT_WORKSPACE = Path(__file__).parent / 'data' / 'antigravity-login'
PROFILE_SOURCE = Path(__file__).parent / 'prompts' / 'antigravity_companion.md'
CLI_SETTINGS = Path.home() / '.gemini' / 'antigravity-cli' / 'settings.json'
CHAT_DENIALS = ('write_file(*)', 'command(*)', 'unsandboxed(*)', 'mcp(*)', 'execute_url(*)')
_requests = asyncio.Semaphore(1)
_catalog_lock = asyncio.Lock()
_catalog = []
_catalog_time = 0.0
_ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


class AntigravityError(RuntimeError):
    pass


def require_chat_permissions(settings_path=CLI_SETTINGS):
    """Refuse to launch unless agy's official policy forbids modifying host files."""
    try:
        settings = json.loads(Path(settings_path).read_text(encoding='utf-8-sig'))
        permissions = settings.get('permissions') if isinstance(settings, dict) else None
        denied = permissions.get('deny', []) if isinstance(permissions, dict) else []
        if settings.get('toolPermission') == 'strict' and isinstance(denied, list) and all(
            rule in denied for rule in CHAT_DENIALS
        ):
            return
    except (OSError, ValueError, AttributeError):
        pass
    raise AntigravityError('AGY 禁写/禁执行权限配置缺失，已停止调用；请恢复官方 CLI settings.json 中的严格权限与拒绝规则')


def find_binary():
    binary = shutil.which('agy') or shutil.which('agy.exe')
    if binary:
        return binary
    local = os.environ.get('LOCALAPPDATA')
    candidate = Path(local) / 'agy/bin/agy.exe' if local else None
    return str(candidate) if candidate and candidate.is_file() else None


def prepare_chat_workspace(workspace=CHAT_WORKSPACE):
    workspace = Path(workspace)
    text = PROFILE_SOURCE.read_text(encoding='utf-8')
    target = workspace / '.agents' / 'agents' / f'{AGENT_NAME}.md'
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or target.read_text(encoding='utf-8') != text:
        target.write_text(text, encoding='utf-8')
    return workspace


def build_chat_command(binary, model, timeout='10m'):
    command = [binary, '--agent', AGENT_NAME,
               '--input-format', 'stream-json', '--output-format', 'stream-json',
               '--disable-slash-commands', '--print-timeout', timeout]
    if model:
        command += ['--model', model]
    return command


def _timeout_seconds(value):
    match = re.fullmatch(r'(\d+)(ms|s|m|h)?', value)
    if not match:
        return 600
    return max(1, int(match[1]) * {'ms': .001, 's': 1, 'm': 60, 'h': 3600, None: 1}[match[2]])


def _process_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def parse_model_catalog(text):
    """Read model slugs from the official catalog, without inventing model IDs."""
    text = _ANSI.sub('', text)
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    result = []
    if isinstance(data, (dict, list)):
        rows = data.get('models', []) if isinstance(data, dict) else data
        for row in rows:
            if not isinstance(row, dict) or row.get('available') is False:
                continue
            slug = row.get('slug') or row.get('id') or row.get('model')
            if isinstance(slug, str) and slug.strip():
                result.append({'model': slug.strip(), 'name': row.get('name') or row.get('label') or row.get('display_name') or slug})
    else:
        # agy models prints a human-readable slug/name table.
        for line in text.splitlines():
            line = line.strip().strip('│|').strip()
            match = re.match(r'^([a-z][a-z0-9._/-]*-[a-z0-9._/-]+)\s+(.+)$', line)
            if match:
                result.append({'model': match[1], 'name': match[2].strip().strip('│|').strip()})
                continue
            match = re.match(r'^(.+?)\s{2,}([a-z][a-z0-9._/-]*-[a-z0-9._/-]+)\s*$', line)
            if match:
                result.append({'model': match[2], 'name': match[1].strip().strip('│|').strip()})
    return list({row['model']: row for row in result}.values())


async def discover_models(force=False):
    global _catalog, _catalog_time
    async with _catalog_lock:
        if not force and time.monotonic() - _catalog_time < 300:
            return list(_catalog)
        binary = find_binary()
        if not binary:
            return []
        process = await asyncio.create_subprocess_exec(
            binary, 'models', stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            **_process_options(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
            if process.returncode:
                raise AntigravityError(stderr.decode('utf-8', errors='replace')[-1000:] or '无法读取 AGY 模型列表')
            rows = parse_model_catalog(stdout.decode('utf-8', errors='replace'))
            if not rows:
                raise AntigravityError('AGY 未返回可识别的模型列表，请先完成官方 CLI 登录')
            _catalog, _catalog_time = rows, time.monotonic()
            return list(rows)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


async def _read_stderr(reader):
    tail = b''
    while chunk := await reader.read(4096):
        tail = (tail + chunk)[-4096:]
    return tail.decode('utf-8', errors='replace').strip()


async def stream_chat(binary, prompt, model, workspace=CHAT_WORKSPACE, timeout='10m'):
    """Yield visible deltas and usage; never recover replies from private databases."""
    from generation_control import own_process, terminate_process_tree

    async with _requests:
        require_chat_permissions()
        workspace = prepare_chat_workspace(workspace)
        process = own_process(await asyncio.create_subprocess_exec(
            *build_chat_command(binary, model, timeout), cwd=str(workspace),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=8 * 1024 * 1024,
            env={**os.environ, 'NO_COLOR': '1'}, **_process_options(),
        ))
        stderr_task = asyncio.create_task(_read_stderr(process.stderr))
        visible = ''
        final = None
        initialized = False
        try:
            async with asyncio.timeout(_timeout_seconds(timeout) + 30):
                payload = {'event': 'user', 'message': {'content': prompt}}
                process.stdin.write((json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8'))
                await process.stdin.drain()
                process.stdin.close()
                await process.stdin.wait_closed()
                async for line in process.stdout:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError as exc:
                        raise AntigravityError('AGY 输出不是官方 stream-json 格式，请检查版本和登录状态') from exc
                    kind = event.get('event')
                    if kind == 'init':
                        config = event.get('init', {})
                        if config.get('agent') != AGENT_NAME:
                            raise AntigravityError('AGY 陪伴配置未生效，已停止本次调用')
                        initialized = True
                        # 1.2.16 advertises the global tool registry here, even with
                        # excludeDefaultComponents enabled; it is not the agent's toolset.
                        yield {'kind': 'activity'}
                    elif kind == 'step_update':
                        step = event.get('step_update', {})
                        if step.get('step_type') == 'tool' or step.get('tool_info') or step.get('subagent_info'):
                            raise AntigravityError('AGY 陪伴模式出现原生工具调用，已停止：' + step.get('tool_name', 'unknown'))
                        if step.get('step_type') == 'agent_response' and initialized:
                            text = step.get('text_delta') or ''
                            if text:
                                visible += text
                                yield {'kind': 'text', 'text': text}
                        else:
                            yield {'kind': 'activity'}
                    elif kind == 'result':
                        final = event.get('result', {})
                        if final.get('status') != 'SUCCESS':
                            raise AntigravityError(final.get('error') or f"AGY 调用结束：{final.get('status', 'UNKNOWN')}")
                        if not initialized:
                            raise AntigravityError('AGY 缺少初始化事件，无法确认陪伴模式')
                        response = final.get('response') or ''
                        if response.startswith(visible):
                            tail = response[len(visible):]
                            if tail:
                                visible += tail
                                yield {'kind': 'text', 'text': tail}
                        elif response != visible:
                            raise AntigravityError('AGY 流式正文与最终回复不一致')
                await process.wait()
                stderr = await stderr_task
                if process.returncode or final is None or not visible:
                    raise AntigravityError(stderr or 'AGY 未收到完整回复，请检查登录、额度和网络')
                yield {'kind': 'completed', 'usage': final.get('usage', {}),
                       'conversation_id': final.get('conversation_id', '')}
        except TimeoutError as exc:
            raise AntigravityError('AGY 回复超时，请稍后重试') from exc
        finally:
            await terminate_process_tree(process)
            if not stderr_task.done():
                stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
