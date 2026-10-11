"""
语音唤醒路由：开关控制 + 状态查询 + AI说话通知 + 远程ASR
"""

from fastapi import APIRouter, UploadFile, File
from pydantic import BaseModel
from voice import voice
from asr import transcribe_audio

router = APIRouter()


class VoiceToggle(BaseModel):
    enabled: bool
    wake_word: str = "老公"


class AISpeakingNotify(BaseModel):
    speaking: bool


@router.get("/api/voice/status")
async def voice_status():
    return {
        "enabled": voice.enabled,
        "in_call": voice.in_call,
        "ai_speaking": voice.ai_speaking,
        "wake_word": voice.wake_word,
    }


@router.post("/api/voice/toggle")
async def voice_toggle(body: VoiceToggle):
    if body.enabled:
        voice.start(body.wake_word)
    else:
        voice.stop()
    return {"ok": True, "enabled": voice.enabled}


@router.post("/api/voice/ai-speaking")
async def voice_ai_speaking(body: AISpeakingNotify):
    """前端通知：AI TTS 播放状态"""
    voice.notify_ai_speaking(body.speaking)
    return {"ok": True}


@router.post("/api/voice/cam-check-start")
async def voice_cam_check_start():
    """前端通知：AI 触发了 CAM_CHECK"""
    voice.notify_cam_check_start()
    return {"ok": True}


@router.post("/api/voice/remote-asr")
async def remote_asr(file: UploadFile = File(...)):
    """远程 ASR：手机端录音使用设置中选择的识别接口。"""
    content = await file.read()
    print(f"[RemoteASR] Received {len(content)} bytes, filename={file.filename}")
    try:
        text = await transcribe_audio(content, file.filename or 'audio.wav', file.content_type or 'audio/wav', timeout=15)
        return {"text": text}
    except Exception as e:
        print(f"[RemoteASR] Error: {e}")
        return {"text": "", "error": str(e)}


@router.post("/api/voice/transcribe")
async def transcribe_voice_message(file: UploadFile = File(...)):
    """语音消息转写：与通话、本机唤醒共用 ASR 配置。"""
    content = await file.read()
    mime = file.content_type or "audio/webm"
    print(f"[VoiceTranscribe] Received {len(content)} bytes, mime={mime}")
    try:
        text = await transcribe_audio(content, file.filename or 'voice.webm', mime)
        return {"text": text}
    except Exception as e:
        print(f"[VoiceTranscribe] Error: {e}")
        return {"text": "", "error": str(e)}
