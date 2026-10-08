"""ElevenLabs provider; MP3 output shares the existing TTS queues and cache."""
import asyncio
import re
import urllib.request

import httpx

from config import SETTINGS, get_key

VOICE_PREFIX = "elevenlabs:"
MODELS = {"eleven_v4", "eleven_v3"}
API = "https://api.elevenlabs.io"


def client():
    # Windows desktop proxies may live in Internet Settings rather than env vars.
    proxy = urllib.request.getproxies().get("https")
    return httpx.AsyncClient(timeout=90, proxy=proxy or None)


def voice_for(sender):
    return str(SETTINGS.get(f"elevenlabs_{sender}_voice_id") or "").strip()


async def request_audio(text: str, voice_id: str) -> bytes:
    key = get_key("elevenlabs")
    if not key or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", voice_id):
        raise ValueError("请先在设置中配置 ElevenLabs Key 和音色")
    model = SETTINGS.get("elevenlabs_tts_model") or "eleven_v4"
    if model not in MODELS:
        raise ValueError("ElevenLabs 模型无效")
    try:
        async with client() as conn:
            response = await conn.post(
                f"{API}/v1/text-to-speech/{voice_id}",
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": key},
                json={"text": text, "model_id": model},
            )
        if response.status_code != 200:
            raise RuntimeError(f"ElevenLabs HTTP {response.status_code}，请检查 Key、额度和音色权限")
        if not response.content or not response.headers.get("content-type", "").startswith(("audio/", "application/octet-stream")):
            raise RuntimeError("ElevenLabs 未返回有效音频")
        return response.content
    except httpx.HTTPError:
        # A lost response can still be billed. Never automatically retry.
        raise RuntimeError("ElevenLabs 连接失败或超时，请先检查官方用量再重试") from None


async def list_voices() -> list[dict]:
    key = get_key("elevenlabs")
    if not key:
        return []
    voices, tokens = {}, set()
    params = {"page_size": 100, "include_total_count": "false"}
    try:
        async with asyncio.timeout(35):
            async with client() as conn:
                while True:
                    response = await conn.get(f"{API}/v2/voices", headers={"xi-api-key": key}, params=params)
                    if response.status_code != 200:
                        raise RuntimeError(f"ElevenLabs 音色列表 HTTP {response.status_code}")
                    data = response.json()
                    for voice in data.get("voices", []):
                        voice_id = voice.get("voice_id")
                        if voice_id:
                            voices[voice_id] = {"uri": VOICE_PREFIX + voice_id,
                                "customName": voice.get("name") or voice_id, "provider": "elevenlabs"}
                    if not data.get("has_more"):
                        return list(voices.values())
                    token = data.get("next_page_token")
                    if not token or token in tokens:
                        raise RuntimeError("ElevenLabs 音色分页不完整，请重新加载")
                    tokens.add(token)
                    params["next_page_token"] = token
    except (httpx.HTTPError, TimeoutError, ValueError):
        raise RuntimeError("ElevenLabs 音色列表暂时不可用，请检查网络和 Key 权限") from None
