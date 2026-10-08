"""Shared transcription for uploaded recordings and desktop voice wakeup."""
import asyncio
import io
import json
import re
import shutil
import subprocess
import uuid
import wave

import httpx
from websockets.asyncio.client import connect

from config import SETTINGS

OPENAI_URL = 'https://api.siliconflow.cn/v1'
OPENAI_MODEL = 'FunAudioLLM/SenseVoiceSmall'
DASHSCOPE_URL = 'wss://dashscope.aliyuncs.com/api-ws/v1/inference'
DASHSCOPE_MODEL = 'paraformer-realtime-v2'
ASR_FIELDS = ('asr_provider', 'asr_base_url', 'asr_api_key', 'asr_model')
_EMOJI_RE = re.compile(
    '[\U0001F300-\U0001FAFF\U0001F1E0-\U0001F1FF\U00002600-\U000027BF'
    '\U000024C2-\U000024FF\U0001F170-\U0001F251\U0000FE00-\U0000FE0F\U0000200D]+'
)


def normalize_asr_settings(settings: dict) -> dict:
    config = {field: str(settings.get(field) or '').strip() for field in ASR_FIELDS}
    config['asr_provider'] = config['asr_provider'] or 'openai'
    if config['asr_provider'] not in ('openai', 'dashscope'):
        raise ValueError('语音转文字接口类型无效')
    if config['asr_provider'] == 'dashscope':
        config['asr_base_url'] = config['asr_base_url'] or DASHSCOPE_URL
        config['asr_model'] = config['asr_model'] or DASHSCOPE_MODEL
    values = [config[field] for field in ASR_FIELDS[1:]]
    if any(values) and not all(values):
        raise ValueError('请完整填写语音转文字的地址、Key 和模型名；OpenAI 兼容接口三项全空时使用硅基流动')
    if config['asr_base_url']:
        url = httpx.URL(config['asr_base_url'])
        schemes = ('wss',) if config['asr_provider'] == 'dashscope' else ('http', 'https')
        if url.scheme not in schemes or not url.host or url.query or url.fragment or url.userinfo:
            raise ValueError('阿里云地址须为 WSS；OpenAI 兼容地址须为 HTTP(S)，不能包含账号、查询参数或片段')
        config['asr_base_url'] = config['asr_base_url'].rstrip('/')
    return config


def get_asr_config() -> dict:
    config = normalize_asr_settings(SETTINGS)
    if not config['asr_base_url']:
        config.update(asr_base_url=OPENAI_URL, asr_model=OPENAI_MODEL,
                      asr_api_key=str(SETTINGS.get('siliconflow_key') or '').strip())
    if not config['asr_api_key']:
        raise ValueError('请先在设置中填写语音转文字 API Key')
    return config


def _to_pcm(content: bytes) -> tuple[bytes, int]:
    """Use mono PCM WAV directly; decode browser WebM/MP4 with FFmpeg."""
    try:
        with wave.open(io.BytesIO(content), 'rb') as audio:
            if audio.getnchannels() == 1 and audio.getsampwidth() == 2:
                return audio.readframes(audio.getnframes()), audio.getframerate()
    except (wave.Error, EOFError):
        pass
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('此录音格式需要 FFmpeg 转换，请安装 FFmpeg 并加入 PATH 后重启服务')
    result = subprocess.run(
        [ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', 'pipe:0',
         '-vn', '-ac', '1', '-ar', '16000', '-f', 's16le', 'pipe:1'],
        input=content, capture_output=True, timeout=30,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    if result.returncode or not result.stdout:
        raise ValueError('录音解码失败，请重新录音或使用 WAV 音频')
    return result.stdout, 16000


async def _dashscope_transcribe(content: bytes, config: dict) -> str:
    pcm, sample_rate = await asyncio.to_thread(_to_pcm, content)
    if not pcm:
        raise ValueError('录音为空，请重新录音')
    task_id = uuid.uuid4().hex
    header = {'task_id': task_id, 'streaming': 'duplex'}
    async with connect(config['asr_base_url'],
                       additional_headers={'Authorization': f"Bearer {config['asr_api_key']}"},
                       open_timeout=10, close_timeout=2) as socket:
        await socket.send(json.dumps({
            'header': {**header, 'action': 'run-task'},
            'payload': {'task_group': 'audio', 'task': 'asr', 'function': 'recognition',
                        'model': config['asr_model'], 'input': {},
                        'parameters': {'format': 'pcm', 'sample_rate': sample_rate,
                                       'language_hints': ['zh', 'en']}},
        }))

        async def receive():
            event = json.loads(await socket.recv())
            if event.get('header', {}).get('event') == 'task-failed':
                error = event['header']
                raise RuntimeError(f"阿里云语音识别失败：{error.get('error_code', '')} {error.get('error_message', '')}")
            return event

        while (await receive()).get('header', {}).get('event') != 'task-started':
            pass

        async def send_audio():
            # Receive results concurrently so longer recordings cannot fill the receive queue.
            for offset in range(0, len(pcm), 3200):
                await socket.send(pcm[offset:offset + 3200])
                await asyncio.sleep(0)
            await socket.send(json.dumps({'header': {**header, 'action': 'finish-task'},
                                          'payload': {'input': {}}}))

        sender = asyncio.create_task(send_audio())
        sentences = []
        try:
            while True:
                event = await receive()
                kind = event.get('header', {}).get('event')
                if kind == 'task-finished':
                    await sender
                    return ''.join(sentences)
                if kind == 'result-generated':
                    sentence = event.get('payload', {}).get('output', {}).get('sentence') or {}
                    if sentence.get('sentence_end') and not sentence.get('heartbeat'):
                        sentences.append(sentence.get('text') or '')
        finally:
            if not sender.done():
                sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)


async def transcribe_audio(content: bytes, filename='audio.wav', mime='audio/wav', timeout=30) -> str:
    config = get_asr_config()
    if not content:
        raise ValueError('录音为空，请重新录音')
    try:
        async with asyncio.timeout(timeout):
            if config['asr_provider'] == 'dashscope':
                text = await _dashscope_transcribe(content, config)
            else:
                url = config['asr_base_url']
                if not url.endswith('/audio/transcriptions'):
                    if httpx.URL(url).path in ('', '/'):
                        url += '/v1'
                    url += '/audio/transcriptions'
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        url, headers={'Authorization': f"Bearer {config['asr_api_key']}"},
                        files={'file': (filename, content, mime)},
                        data={'model': config['asr_model'], 'language': 'zh'}, timeout=timeout,
                    )
                    response.raise_for_status()
                    text = response.json().get('text') or ''
            return _EMOJI_RE.sub('', text).strip()
    except TimeoutError:
        raise RuntimeError('语音识别超时，请稍后重试') from None
