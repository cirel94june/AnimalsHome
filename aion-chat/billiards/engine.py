import asyncio
import json
import shutil
from pathlib import Path

RUNNER = Path(__file__).with_name('engine.cjs')


def available() -> bool:
    return bool(shutil.which('node') and RUNNER.is_file())


async def execute(**request) -> dict:
    node = shutil.which('node')
    if not node:
        raise ValueError('台球室需要 Node.js；其他玩法可以照常使用')
    process = await asyncio.create_subprocess_exec(
        node, str(RUNNER), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=0x08000000 if __import__('os').name == 'nt' else 0,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(json.dumps(request).encode()), 35)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    try:
        result = json.loads(output)
    except ValueError:
        raise ValueError('台球算法暂时不可用，请稍后继续') from None
    if result.get('error') or process.returncode:
        raise ValueError(result.get('error') or '台球算法执行失败')
    return result
