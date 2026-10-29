"""
AI 模型调用：硅基流动 / Gemini 流式 + 多模态消息构建
"""

import sys
import json, base64, mimetypes, asyncio, shutil, subprocess, os, re, time, uuid
from functools import lru_cache
from contextlib import nullcontext
from pathlib import Path

from generation_control import own_stream, own_process

import httpx
import tempfile

from config import NO_ROUTE_HINT, get_key, MODELS, UPLOADS_DIR, ALBUM_IMAGES_DIR, CODEX_UPLOADS_DIR, SCREENSHOTS_DIR, SETTINGS, get_sentinel_config, DATA_DIR, resolve_model_key, is_model_deprecated, LIVE_STATE_HEADING, CUSTOM_OPENAI_PROVIDER
from codex_app_server import (
    CodexAppServerEvent,
    build_codex_app_server_command,
    stream_codex_app_server,
)
from codex_chat_profile import prepare_chat_model_catalog
import antigravity_cli
from stream_safety import StreamActivity

# CLI 状态前缀：yield 此前缀的 chunk 会被 _bg_generate 拦截为状态事件，不送入 TTS 和正文
CLI_STATUS_PREFIX = "\x00CLI_STATUS:"
_ANTIGRAVITY_DEFAULT_PRINT_TIMEOUT = "10m"
_ANTIGRAVITY_PRINT_TIMEOUT_RE = re.compile(r"\d+(?:ms|s|m|h)?")
MODEL_RAW_RESPONSE_DIR = DATA_DIR / "model_raw_responses"
MODEL_RAW_RESPONSE_RETENTION_SECONDS = 3 * 24 * 60 * 60
THINK_TAG_OPEN = "<think>"
THINK_TAG_CLOSE = "</think>"
_THINK_TAG_RE = re.compile(r"<think>([\s\S]*?)</think>", re.IGNORECASE)


def _tag_prefix_suffix_len(text: str, tag: str) -> int:
    max_len = min(len(text), len(tag) - 1)
    for size in range(max_len, 0, -1):
        if tag.startswith(text[-size:]):
            return size
    return 0


def extract_think_tag_reasoning(text: str, existing_reasoning: str = "") -> tuple[str, str]:
    """Move standard <think>...</think> blocks from visible text into reasoning."""
    original = text or ""
    existing = (existing_reasoning or "").strip()
    if not original or not _THINK_TAG_RE.search(original):
        return original, existing

    reasoning_parts: list[str] = []

    def _remove(match: re.Match) -> str:
        inner = (match.group(1) or "").strip()
        if inner:
            reasoning_parts.append(inner)
        return "\n\n"

    visible = _THINK_TAG_RE.sub(_remove, original)
    visible = re.sub(r"[ \t]+\n", "\n", visible)
    visible = re.sub(r"\n[ \t]+", "\n", visible)
    visible = re.sub(r"\n{3,}", "\n\n", visible).strip()
    extracted = "\n\n".join(part for part in reasoning_parts if part).strip()
    reasoning = "\n\n".join(part for part in (existing, extracted) if part).strip()
    return visible, reasoning


class ThinkTagReasoningFilter:
    """Streaming filter for relay providers that send reasoning as <think> tags."""

    def __init__(self, meta: dict | None = None):
        self.meta = meta
        self.pending = ""
        self.in_think = False

    def _append_reasoning(self, text: str) -> None:
        if self.meta is None or not text:
            return
        current = str(self.meta.get("reasoning_content") or "")
        self.meta["reasoning_content"] = current + text

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""

        buf = self.pending + str(chunk)
        self.pending = ""
        out: list[str] = []

        while buf:
            lower = buf.lower()
            if self.in_think:
                close_idx = lower.find(THINK_TAG_CLOSE)
                if close_idx >= 0:
                    self._append_reasoning(buf[:close_idx])
                    buf = buf[close_idx + len(THINK_TAG_CLOSE):]
                    self.in_think = False
                    continue

                keep = _tag_prefix_suffix_len(lower, THINK_TAG_CLOSE)
                if keep:
                    self._append_reasoning(buf[:-keep])
                    self.pending = buf[-keep:]
                else:
                    self._append_reasoning(buf)
                break

            open_idx = lower.find(THINK_TAG_OPEN)
            if open_idx >= 0:
                out.append(buf[:open_idx])
                buf = buf[open_idx + len(THINK_TAG_OPEN):]
                self.in_think = True
                continue

            keep = _tag_prefix_suffix_len(lower, THINK_TAG_OPEN)
            if keep:
                out.append(buf[:-keep])
                self.pending = buf[-keep:]
            else:
                out.append(buf)
            break

        return "".join(out)

    def flush(self) -> str:
        pending = self.pending
        self.pending = ""
        if not pending:
            return ""
        if self.in_think:
            self._append_reasoning(pending)
            return ""
        return pending


def _decode_relay_body(body: bytes) -> str:
    return (body or b"").decode("utf-8", errors="replace")


def _openai_sse_data(line: str) -> str | None:
    if not line.startswith("data:"):
        return None
    return line.split(":", 1)[1].strip()


# Gemini CLI 内部思考/工具痕迹清洗：
# Gemini 3 在 agent 模式下处理图片时，可能把内部思考链（image_description / thought / Footnote / 系统指令）
# 混进 assistant 消息正文里。这些片段需要在交付给前端/记忆/TTS 之前剥掉，只保留真正的回复。
_GEMINI_CLI_NOISE_PATTERNS = [
    # <image_description>...</image_description>
    re.compile(r'<image_description>[\s\S]*?</image_description>', re.IGNORECASE),
    # <thought>...</thought> 以及 <step:NN>thought ... </step:NN>thought 这种带后缀的变体
    re.compile(r'<thought>[\s\S]*?</thought>', re.IGNORECASE),
    re.compile(r'<[^<>\n]{0,40}>thought[\s\S]*?</[^<>\n]{0,40}>thought', re.IGNORECASE),
    # Footnote{...} / Footnote {content: ...} 形式的对象序列化
    re.compile(r'Footnote\s*\{[\s\S]*?\}\s*', re.IGNORECASE),
    # 残留的整行系统/agent 指令
    re.compile(r'^.*CRITICAL INSTRUCTION\s*\d+\s*:.*$', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^.*Currently no further tools are needed.*$', re.IGNORECASE | re.MULTILINE),
]
_LEADING_CLI_ROLE_HEADER_RE = re.compile(r'^\s*\[(?:Assistant|Model|AI|Aion)\]\s*', re.IGNORECASE)


def _antigravity_print_timeout(meta: dict | None) -> str:
    if not isinstance(meta, dict):
        return _ANTIGRAVITY_DEFAULT_PRINT_TIMEOUT
    value = str(meta.get("antigravity_print_timeout") or "").strip()
    if value and _ANTIGRAVITY_PRINT_TIMEOUT_RE.fullmatch(value):
        return value
    return _ANTIGRAVITY_DEFAULT_PRINT_TIMEOUT


def _strip_gemini_cli_noise(text: str) -> str:
    """去除 Gemini CLI agent 模式下泄漏到正文里的思考/工具痕迹。"""
    if not text:
        return text
    cleaned = text
    for pat in _GEMINI_CLI_NOISE_PATTERNS:
        cleaned = pat.sub('', cleaned)
    cleaned = _LEADING_CLI_ROLE_HEADER_RE.sub('', cleaned, count=1)
    # 去除被裁出来后可能剩下的多余空行
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


def _extract_gemini_cli_report_error(stderr_text: str) -> tuple[str | None, str | None]:
    """从 Gemini CLI 的临时错误报告里提炼核心错误，并保留报告路径。"""
    if not stderr_text:
        return None, None
    if "UNSUPPORTED_CLIENT" in stderr_text or "IneligibleTierError" in stderr_text:
        return (
            "Gemini CLI 的个人免费/Pro/Ultra 通路已被 Google 停止服务，"
            "当前账号需要改用项目里的 AGY-3.1pro（Antigravity CLI）线路。",
            None,
        )
    match = re.search(r"Full report available at:\s*(.+?\.json)", stderr_text)
    if not match:
        return None, None
    report_path = Path(match.group(1).strip())
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, str(report_path)
    err_msg = payload.get("error", {}).get("message")
    cleaned = err_msg.strip() if isinstance(err_msg, str) and err_msg.strip() else None
    return cleaned, str(report_path)


# 流式状态机：噪音块开始 → 闭合 token 列表
# key 是开始触发器（不区分大小写），value 是对应的闭合 token
_NOISE_BLOCK_TRIGGERS = [
    ('<image_description>', '</image_description>'),
    ('<thought>', '</thought>'),
    ('Footnote{', '}'),
    ('Footnote {', '}'),
]
# 行级噪音前缀：整行命中即丢弃
_NOISE_LINE_PREFIXES = (
    'CRITICAL INSTRUCTION',
    'Currently no further tools',
)
# 用于检测 chunk 末尾"可能是噪音开头前缀"的最大窥探长度
_MAX_TRIGGER_LEN = max(len(t[0]) for t in _NOISE_BLOCK_TRIGGERS)


class GeminiCliNoiseFilter:
    """跨 chunk 状态机：检测到噪音标签开始就进入屏蔽态，缓冲后续内容直到对应闭合 token 出现，
    整段噪音直接丢弃。同时识别 'Footnote{' / '<image_description>' 等被切到 chunk 中间的情况。"""

    def __init__(self):
        self.pending = ""        # 暂存可能是触发器开头的尾部碎片
        self.in_block = False    # 当前是否在噪音块内
        self.close_token = ""    # 当前噪音块的闭合 token

    def feed(self, chunk: str) -> str:
        """喂入新 chunk，返回可安全 yield 的干净文本（可能为空）。"""
        if not chunk:
            return ""
        buf = self.pending + chunk
        self.pending = ""
        out_parts: list[str] = []

        i = 0
        n = len(buf)
        while i < n:
            if self.in_block:
                # 在噪音块内：找闭合 token
                idx = buf.find(self.close_token, i)
                if idx == -1:
                    # 闭合还没到，整段丢弃，保留尾部 close_token-1 长度防截断
                    keep = max(0, n - len(self.close_token) + 1)
                    if keep > i:
                        # 中间部分全丢，但保留尾部进 pending 等下次拼接
                        self.pending = buf[keep:]
                    else:
                        self.pending = buf[i:]
                    return "".join(out_parts)
                # 跳过整个噪音块（包括闭合 token）
                i = idx + len(self.close_token)
                self.in_block = False
                self.close_token = ""
                continue

            # 不在噪音块：找最近的触发器
            best_pos = -1
            best_trigger = None
            best_close = None
            lower_buf = buf.lower()
            for trigger, close in _NOISE_BLOCK_TRIGGERS:
                pos = lower_buf.find(trigger.lower(), i)
                if pos != -1 and (best_pos == -1 or pos < best_pos):
                    best_pos = pos
                    best_trigger = trigger
                    best_close = close

            if best_pos == -1:
                # 没有触发器，但末尾可能是触发器的前缀（被切断），保留进 pending
                tail_start = max(i, n - _MAX_TRIGGER_LEN + 1)
                # 检查 buf[tail_start:n] 是否是某个触发器的前缀
                tail = buf[tail_start:].lower()
                is_potential_prefix = False
                for trigger, _ in _NOISE_BLOCK_TRIGGERS:
                    tl = trigger.lower()
                    for k in range(1, min(len(tl), len(tail)) + 1):
                        if tl.startswith(tail[-k:]):
                            is_potential_prefix = True
                            break
                    if is_potential_prefix:
                        break
                if is_potential_prefix and tail_start > i:
                    out_parts.append(buf[i:tail_start])
                    self.pending = buf[tail_start:]
                else:
                    out_parts.append(buf[i:])
                break

            # 输出触发器之前的干净部分
            if best_pos > i:
                out_parts.append(buf[i:best_pos])
            # 进入噪音块
            self.in_block = True
            self.close_token = best_close
            i = best_pos + len(best_trigger)

        cleaned = "".join(out_parts)
        # 行级噪音前缀过滤
        if cleaned and any(p in cleaned for p in _NOISE_LINE_PREFIXES):
            lines = cleaned.split('\n')
            kept = [ln for ln in lines if not any(p in ln for p in _NOISE_LINE_PREFIXES)]
            cleaned = '\n'.join(kept)
        return cleaned

    def flush(self) -> str:
        """流结束时调用，返回 pending 中残留的安全内容。"""
        if self.in_block:
            # 噪音块未闭合，全部丢弃
            self.pending = ""
            self.in_block = False
            return ""
        out = self.pending
        self.pending = ""
        return out


def _attachment_file_ref(att) -> str:
    if isinstance(att, str):
        return att.strip()
    if isinstance(att, dict):
        att_type = str(att.get("type") or "").strip()
        if att_type in {"link_preview", "music", "luckin_payment", "wish_fulfillment", "date_summary", "usage"}:
            return ""
        url = att.get("url")
        return url.strip() if isinstance(url, str) else ""
    return ""


def _resolve_attachment_path(att) -> Path | None:
    """根据附件 URL 路径解析到本地文件"""
    att = _attachment_file_ref(att)
    if not att:
        return None
    if att.startswith("/cr-uploads/"):
        # /cr-uploads/2026-05-07/xxx.jpg → CODEX_UPLOADS_DIR/2026-05-07/xxx.jpg
        rel = att[len("/cr-uploads/"):]
        return CODEX_UPLOADS_DIR / rel
    elif att.startswith("/uploads/album/"):
        return ALBUM_IMAGES_DIR / Path(att).name
    elif att.startswith("/uploads/"):
        return UPLOADS_DIR / att[len("/uploads/"):]
    elif att.startswith("/screenshots/"):
        return SCREENSHOTS_DIR / att[len("/screenshots/"):]
    else:
        # fallback: 只取文件名去主 uploads 找
        return UPLOADS_DIR / Path(att).name


def _ensure_gemini_accessible(fpath: Path) -> Path:
    """如果文件在 Connor-Codex/uploads/ 下（Gemini CLI 无权访问），
    则复制一份到 data/uploads/ 并返回新路径；否则原样返回。"""
    try:
        fpath.resolve().relative_to(CODEX_UPLOADS_DIR.resolve())
    except ValueError:
        return fpath  # 不在 CR 目录下，无需处理
    dest = UPLOADS_DIR / fpath.name
    if not dest.exists():
        shutil.copy2(fpath, dest)
    return dest


# ── 多模态消息构建 ────────────────────────────────
def _is_audio_attachment(att) -> bool:
    if isinstance(att, dict) and att.get("type") == "voice":
        return True
    fpath = _resolve_attachment_path(att)
    if not fpath:
        return False
    mime = mimetypes.guess_type(str(fpath))[0] or ""
    return mime.startswith("audio/")


def filter_audio_attachments_for_model(messages: list, *, include_audio: bool) -> list:
    """按模型能力裁掉音频附件，不修改调用方传入的消息。"""
    result = []
    for message in messages:
        copied = dict(message)
        attachments = copied.get("attachments", [])
        if isinstance(attachments, str):
            try:
                attachments = json.loads(attachments) if attachments else []
            except Exception:
                attachments = []
        if not include_audio and attachments:
            copied["attachments"] = [
                att for att in attachments if not _is_audio_attachment(att)
            ]
        result.append(copied)
    return result


def _openai_input_audio_format(fpath: Path, mime: str) -> str:
    """返回 OpenAI input_audio 接受的格式；不支持的源格式不内联。"""
    suffix = fpath.suffix.lower()
    if suffix in {".wav", ".wave"} or mime in {"audio/wav", "audio/x-wav"}:
        return "wav"
    if suffix == ".mp3" or mime in {"audio/mpeg", "audio/mp3"}:
        return "mp3"
    return ""


def build_multimodal_messages(history: list, *, include_audio: bool = False, include_video: bool = False):
    """将带附件的历史记录转换为 OpenAI 兼容多模态格式"""
    result = []
    for m in history:
        attachments = m.get("attachments", [])
        if isinstance(attachments, str):
            try: attachments = json.loads(attachments) if attachments else []
            except: attachments = []
        if attachments and m["role"] == "user":
            parts = []
            if m["content"]:
                parts.append({"type": "text", "text": m["content"]})
            has_file_parts = False
            for att in attachments:
                fpath = _resolve_attachment_path(att)
                if fpath and fpath.exists():
                    mime = mimetypes.guess_type(str(fpath))[0] or "application/octet-stream"
                    b64 = base64.b64encode(fpath.read_bytes()).decode()
                    if mime.startswith("image/"):
                        parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
                        has_file_parts = True
                    elif mime.startswith("audio/"):
                        audio_format = _openai_input_audio_format(fpath, mime)
                        if include_audio and audio_format:
                            parts.append({
                                "type": "input_audio",
                                "input_audio": {"data": b64, "format": audio_format},
                            })
                            has_file_parts = True
                    elif mime.startswith("video/"):
                        if include_video:
                            parts.append({
                                "type": "video_url",
                                "video_url": {"url": f"data:{mime};base64,{b64}"},
                            })
                            has_file_parts = True
                    else:
                        parts.append({
                            "type": "text",
                            "text": (
                                f"[Attachment not sent inline: {fpath.name} ({mime}). "
                                "OpenAI-compatible relay payloads only include text and image_url parts.]"
                            ),
                        })
                        has_file_parts = True
            result.append({"role": m["role"], "content": parts if has_file_parts else m["content"]})
        else:
            result.append({"role": m["role"], "content": m["content"]})
    return result


def build_gemini_contents(history: list, *, include_audio: bool = False):
    """将带附件的历史记录转换为 Gemini 格式"""
    contents = []
    for m in history:
        role = "user" if m["role"] == "user" else "model"
        attachments = m.get("attachments", [])
        if isinstance(attachments, str):
            try: attachments = json.loads(attachments) if attachments else []
            except: attachments = []
        parts = []
        if m["content"]:
            parts.append({"text": m["content"]})
        if attachments and m["role"] == "user":
            for att in attachments:
                fpath = _resolve_attachment_path(att)
                if fpath and fpath.exists():
                    mime = mimetypes.guess_type(str(fpath))[0] or "image/jpeg"
                    if mime.startswith("audio/") and not include_audio:
                        continue
                    b64 = base64.b64encode(fpath.read_bytes()).decode()
                    parts.append({"inline_data": {"mime_type": mime, "data": b64}})
        contents.append({"role": role, "parts": parts if parts else [{"text": m["content"]}]})
    return contents


# ── 硅基流动 ──────────────────────────────────────
async def call_siliconflow(messages: list, model: str, meta: dict | None = None, temperature: float | None = None, max_tokens: int | None = None):
    url = "https://api.siliconflow.cn/v1/chat/completions"
    headers = {"Authorization": f"Bearer {get_key('siliconflow')}", "Content-Type": "application/json"}
    api_messages = build_multimodal_messages(messages)
    payload = {"model": model, "messages": api_messages, "stream": True,
               "stream_options": {"include_usage": True}}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            own_stream(resp)
            if resp.status_code != 200:
                body = await resp.aread()
                try:
                    err = json.loads(body).get("error", {}).get("message", body.decode())
                except:
                    err = body.decode(errors="replace")[:500]
                yield f"[硅基流动错误 {resp.status_code}] {err}"
                return
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                        if meta is not None and "usage" in chunk and chunk["usage"]:
                            u = chunk["usage"]
                            meta["prompt_tokens"] = u.get("prompt_tokens", 0)
                            meta["completion_tokens"] = u.get("completion_tokens", 0)
                            meta["total_tokens"] = u.get("total_tokens", 0)
                            meta["raw"] = u  # 保留原始 usage 数据
                        delta = chunk["choices"][0].get("delta", {}) if chunk.get("choices") else {}
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                        if meta is not None and reasoning:
                            meta["reasoning_content"] = meta.get("reasoning_content", "") + str(reasoning)
                        if "content" in delta and delta["content"]:
                            yield delta["content"]
                    except:
                        pass


# ── Gemini 安全设置（全局关闭内容过滤）─────────────
GEMINI_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

# ── Gemini ────────────────────────────────────────
async def call_gemini(messages: list, model: str, meta: dict | None = None, temperature: float | None = None, max_tokens: int | None = None, include_audio: bool = False):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse&key={get_key('gemini')}"
    contents = build_gemini_contents(messages, include_audio=include_audio)
    payload = {"contents": contents, "safetySettings": GEMINI_SAFETY_SETTINGS}
    gen_config = {}
    if temperature is not None:
        gen_config["temperature"] = temperature
    if max_tokens is not None:
        gen_config["maxOutputTokens"] = max_tokens
    if gen_config:
        payload["generationConfig"] = gen_config
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", url, json=payload) as resp:
            own_stream(resp)
            if resp.status_code != 200:
                body = await resp.aread()
                try:
                    err = json.loads(body).get("error", {}).get("message", body.decode())
                except:
                    err = body.decode(errors="replace")[:500]
                yield f"[Gemini错误 {resp.status_code}] {err}"
                return
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    try:
                        chunk = json.loads(line[6:])
                        if meta is not None and "usageMetadata" in chunk:
                            u = chunk["usageMetadata"]
                            meta["prompt_tokens"] = u.get("promptTokenCount", 0)
                            meta["completion_tokens"] = u.get("candidatesTokenCount", 0)
                            meta["total_tokens"] = u.get("totalTokenCount", 0)
                            meta["raw"] = u  # 保留原始 usageMetadata 数据
                        parts = chunk.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                        for part in parts:
                            text = part.get("text", "") if isinstance(part, dict) else ""
                            if not text:
                                continue
                            if part.get("thought"):
                                if meta is not None:
                                    meta["reasoning_content"] = meta.get("reasoning_content", "") + text
                            else:
                                yield text
                    except:
                        pass

# ── AiPro 中转站  ────────────────────────────────────────https://vip.aipro.love
async def call_aipro(messages: list, model: str, meta: dict | None = None, temperature: float | None = None, max_tokens: int | None = None):
    url = "https://shufulei.net/v1/chat/completions"	
    headers = {"Authorization": f"Bearer {get_key('aipro')}", "Content-Type": "application/json"}
    api_messages = build_multimodal_messages(messages)
    payload = {"model": model, "messages": api_messages, "stream": True}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            own_stream(resp)
            if resp.status_code != 200:
                body = await resp.aread()
                if meta is not None:
                    meta["provider_error"] = f"HTTP {resp.status_code}: {_decode_relay_body(body)}"
                yield _decode_relay_body(body)
                return
            async for line in resp.aiter_lines():
                data = _openai_sse_data(line)
                if data is None:
                    continue
                if data == "[DONE]":
                    return
                try:
                    chunk = json.loads(data)
                    if isinstance(chunk, dict) and chunk.get("error"):
                        yield data
                        return
                    if meta is not None and "usage" in chunk and chunk["usage"]:
                        u = chunk["usage"]
                        meta["prompt_tokens"] = u.get("prompt_tokens", 0)
                        meta["completion_tokens"] = u.get("completion_tokens", 0)
                        meta["total_tokens"] = u.get("total_tokens", 0)
                        meta["raw"] = u
                    delta = chunk["choices"][0].get("delta", {}) if chunk.get("choices") else {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if meta is not None and reasoning:
                        meta["reasoning_content"] = meta.get("reasoning_content", "") + str(reasoning)
                    if "content" in delta and delta["content"]:
                        yield delta["content"]
                except:
                    pass


def _openai_chat_completions_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


# 不认识 stream_options 的中转站（按请求地址记，进程内有效）
_NO_STREAM_USAGE: set[str] = set()
# 不认识缓存标记（cache_control）的中转站
_NO_CACHE_MARKER: set[str] = set()
# 不认「缓存保留 1 小时」（ttl）的中转站：退回默认的 5 分钟
_NO_LONG_CACHE: set[str] = set()
# 不认 Gemini「把思考也传回来」开关（extra_body.google.thinking_config）的中转站
_NO_GEMINI_THOUGHTS: set[str] = set()
# 边写边传时把 Gemini 思考丢掉、一次性返回才给的 (地址, 模型)：改成一次性返回（2026-10-29 4Router 实测）
_STREAM_DROPS_THOUGHTS: set[tuple[str, str]] = set()
_GEMINI_THOUGHTS = {"google": {"thinking_config": {"include_thoughts": True}}}

# 每次都会变的内容从这里开始（当前时间 → 记忆 → 聊天记录）；它之前的人设、规则、能力说明不变
_DYNAMIC_START_MARKERS = ("系统当前的准确时间是", "[当前时间]")
# 消息中间出现这些小标题，说明后面是每轮会变的状态（日程、位置、设备、画像）
_LIVE_STATE_HEADINGS = (LIVE_STATE_HEADING, "【设备当前状态】")


def _message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    return ""


def _live_state_at(text: str) -> int:
    found = [i for i in (text.find(h) for h in _LIVE_STATE_HEADINGS) if i >= 0]
    return min(found) if found else -1


# 构造上下文时给消息打的内部标签（发给模型前去掉）：
#   "abilities" 能力说明（里面 LIVE_STATE_HEADING 之后是每轮会变的状态）
#   "live" / "live_ack" 每轮会变的背景（时间+背景记忆、设备状态、相关记忆）及它的回执
#   "history" 聊天记录从这一条开始
# 只认标签，不从正文猜：聊天内容里出现同样的字也不会被挪动。
CONTEXT_TAG = "_context"
# render_merged_timeline 生成的聊天记录说明（只用来决定第二个缓存标记放哪，不影响顺序）
_HISTORY_HEADER = "[历史记录说明]"


def _cache_friendly_order(messages: list) -> list:
    """把每轮都会变的背景挪到聊天记录之后、最新一句之前：
    人设 / 规则 / 能力说明 / 聊天记录 → 实时状态 / 时间 / 记忆 → 最新一句。
    这样「人设到聊天记录」是每轮相同的开头，可以整体走缓存。
    只处理构造器标了 "history" 的请求、且最后一条是用户消息；只挪标了 live / live_ack 的消息和
    能力说明里实时状态那一段。其余原样返回；不修改原列表。"""
    if len(messages) < 3 or messages[-1].get("role") != "user":
        return messages
    header = next((i for i, m in enumerate(messages) if m.get(CONTEXT_TAG) == "history"), -1)
    if header < 0 or header >= len(messages) - 1:
        return messages
    kept, moved = [], []
    for m in messages[:header]:
        tag = m.get(CONTEXT_TAG)
        content = m.get("content")
        cut = content.find(LIVE_STATE_HEADING) if tag == "abilities" and isinstance(content, str) else -1
        if tag in ("live", "live_ack"):
            moved.append(m)
        elif cut > 0 and content[:cut].strip():
            kept.append({**m, "content": content[:cut].rstrip()})
            moved.append({"role": "user", "content": content[cut:]})
        else:
            kept.append(m)
    if not moved:
        return messages
    return kept + list(messages[header:-1]) + moved + [messages[-1]]


def _without_context_tags(messages: list) -> list:
    return [{k: v for k, v in m.items() if k != CONTEXT_TAG} if CONTEXT_TAG in m else m for m in messages]


def _cache_control(ttl: str = "5m") -> dict:
    """Anthropic 的缓存标记。默认保留 5 分钟；ttl="1h" 保留 1 小时（写入更贵，隔得久也能命中）。"""
    return {"type": "ephemeral", "ttl": "1h"} if ttl == "1h" else {"type": "ephemeral"}


def _mark_cacheable(message: dict, ttl: str = "5m") -> dict | None:
    """给一条消息的最后一个文字片段加 cache_control；加不了返回 None。"""
    target = dict(message)
    content = target.get("content")
    if isinstance(content, str):
        if not content.strip():
            return None
        target["content"] = [{"type": "text", "text": content, "cache_control": _cache_control(ttl)}]
        return target
    if isinstance(content, list) and content:
        parts = [dict(p) if isinstance(p, dict) else p for p in content]
        for i in range(len(parts) - 1, -1, -1):
            if isinstance(parts[i], dict) and parts[i].get("type") == "text":
                parts[i]["cache_control"] = _cache_control(ttl)
                target["content"] = parts
                return target
    return None


def _cache_breakpoint(messages: list, ttl: str = "5m") -> tuple[list, list[int]]:
    """在「不变的部分」末尾加 cache_control（Anthropic 的缓存标记，OpenAI 兼容格式里放在内容片段上），
    返回 (新列表, 被改成片段数组的消息下标)。
    不变的部分到第一条「当前时间」消息为止；若更早的消息里出现实时状态小标题，就在小标题处把那条消息切成两段，
    标记打在前一段。聊天记录在这段里面时，再在聊天记录前（人设 / 规则 / 能力说明末尾）加一个标记，
    聊天记录变了也还能命中前面那段。找不到分界（比如没有时间块的简单请求）就原样返回，不加。"""
    boundary = -1
    for i, m in enumerate(messages):
        text = _message_text(m)
        if any(text.lstrip().startswith(x) for x in _DYNAMIC_START_MARKERS):
            boundary = i
            break
        cut = _live_state_at(text)
        if cut >= 0:
            if cut > 0 and text[:cut].strip() and isinstance(m.get("content"), str):
                marked = list(messages)
                marked[i] = {**m, "content": [
                    {"type": "text", "text": text[:cut], "cache_control": _cache_control(ttl)},
                    {"type": "text", "text": text[cut:]},
                ]}
                return marked, [i]
            boundary = i
            break
    if boundary <= 0:
        return messages, []
    target = _mark_cacheable(messages[boundary - 1], ttl)
    if target is None:
        return messages, []
    marked = list(messages)
    marked[boundary - 1] = target
    indices = [boundary - 1]
    header = next((i for i, m in enumerate(messages[:boundary]) if _message_text(m).startswith(_HISTORY_HEADER)), -1)
    if 0 < header < boundary - 1:
        stable_end = _mark_cacheable(messages[header - 1], ttl)
        if stable_end is not None:
            marked[header - 1] = stable_end
            indices.insert(0, header - 1)
    return marked, indices


def _with_cache_breakpoint(messages: list, ttl: str = "5m") -> list:
    return _cache_breakpoint(messages, ttl)[0]


def _mentions_ttl(body: bytes) -> bool:
    return re.search(r"\bttl\b", (body or b"")[:4000].decode("utf-8", "replace"), re.I) is not None


def _mentions_cache_control(body: bytes) -> bool:
    text = (body or b"")[:4000].decode("utf-8", "replace").lower()
    return "cache_control" in text or "cache control" in text


_STRING_TYPE_ERROR = r".{0,120}?(valid string|must be (a |of type )?string|expected (a )?string|str type)"


def _rejects_list_content(body: bytes, indices: list[int]) -> bool:
    """只收字符串 content 的兼容中转站，报的是「messages.<被改成数组的那条>.content 应该是字符串」。"""
    text = (body or b"")[:4000].decode("utf-8", "replace")
    return any(re.search(rf"messages\W{{1,6}}{index}\W{{1,6}}content" + _STRING_TYPE_ERROR, text, re.I | re.S)
               for index in indices if index >= 0)


def _mentions_thinking_config(body: bytes) -> bool:
    text = (body or b"")[:4000].decode("utf-8", "replace").lower()
    return any(word in text for word in ("extra_body", "thinking_config", "include_thoughts"))


def _reasoning_tokens(usage: dict | None) -> int:
    details = (usage or {}).get("completion_tokens_details") or {}
    try:
        return int(details.get("reasoning_tokens") or 0)
    except (TypeError, ValueError):
        return 0


def _mentions_stream_options(body: bytes) -> bool:
    text = (body or b"")[:4000].decode("utf-8", "replace").lower()
    return "stream_options" in text or "include_usage" in text


# ── 自定义 OpenAI 兼容中转站 ─────────────────────────
async def call_custom_openai(messages: list, cfg: dict, meta: dict | None = None, temperature: float | None = None, max_tokens: int | None = None, request_timeout: float = 120):
    model = (cfg.get("model") or "").strip()
    url = _openai_chat_completions_url(cfg.get("base_url", ""))
    if not url or not model:
        yield "[自定义中转站错误] 缺少 API 地址或模型名称"
        return
    headers = {"Content-Type": "application/json"}
    api_key = (cfg.get("api_key") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    api_messages = build_multimodal_messages(
        messages,
        include_audio=bool(cfg.get("audio", False)),
        include_video=bool(cfg.get("video", False)),
    )
    payload = {"model": model, "messages": api_messages, "stream": True}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if cfg.get("use_default_reasoning_effort") is False:
        payload["reasoning_effort"] = cfg.get("reasoning_effort", "high")
    # Gemini 走 OpenAI 兼容接口时默认不传思考，要显式打开（狗蛋 10-29：💭 一直是空的）
    want_thoughts = "gemini" in model.lower()
    saw_reasoning = False
    stream_usage: dict = {}
    reasoning_status_sent = False
    content_started = False
    want_cache_marker = cfg.get("cache_marker", True) is not False
    plain_messages = payload["messages"]
    try:
        for _attempt in range(5):
            if want_thoughts and url not in _NO_GEMINI_THOUGHTS:
                payload["extra_body"] = _GEMINI_THOUGHTS
            else:
                payload.pop("extra_body", None)
            if "extra_body" in payload and (url, model) in _STREAM_DROPS_THOUGHTS:
                if not reasoning_status_sent:
                    yield f"{CLI_STATUS_PREFIX}正在思考..."  # 要等整条写完，先告诉大家在想
                    reasoning_status_sent = True
                body = {k: v for k, v in payload.items() if k != "stream_options"}
                body["stream"] = False
                async with httpx.AsyncClient(timeout=request_timeout) as client:
                    resp = await client.post(url, json=body, headers=headers)
                if resp.status_code in (400, 422) and _mentions_thinking_config(resp.content):
                    # 一次性接口也不认思考开关：和流式一样记住、去掉再发（复审 M1）
                    _NO_GEMINI_THOUGHTS.add(url)
                    continue
                for piece in _custom_openai_whole_reply(resp, meta):
                    yield piece
                return
            # 请中转站在流式回复的最后告诉用量（含缓存命中），用量小本本靠它记真实数字
            ask_usage = url not in _NO_STREAM_USAGE
            if ask_usage:
                payload["stream_options"] = {"include_usage": True}
            else:
                payload.pop("stream_options", None)
            mark_cache = want_cache_marker and url not in _NO_CACHE_MARKER
            ttl = "1h" if cfg.get("cache_ttl", "1h") == "1h" and url not in _NO_LONG_CACHE else "5m"
            payload["messages"], marked_index = _cache_breakpoint(plain_messages, ttl) if mark_cache else (plain_messages, [])
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    own_stream(resp)
                    if resp.status_code != 200:
                        body = await resp.aread()
                        # 回退顺序固定：1 小时 → 5 分钟 → 去掉缓存标记 → 去掉 stream_options。
                        # 还没输出任何内容，重发是安全的；每样只退一次，最多 4 次
                        if (mark_cache and ttl == "1h" and payload["messages"] is not plain_messages
                                and resp.status_code in (400, 422) and _mentions_ttl(body)):
                            # 这家中转站不认「保留 1 小时」：记住，退回 5 分钟再发一次
                            _NO_LONG_CACHE.add(url)
                            continue
                        if (mark_cache and payload["messages"] is not plain_messages
                                and resp.status_code in (400, 422)
                                and (_mentions_cache_control(body) or _rejects_list_content(body, marked_index))):
                            # 这家中转站不认识缓存标记（或只收字符串内容）：记住，去掉再发一次
                            _NO_CACHE_MARKER.add(url)
                            continue
                        if "extra_body" in payload and resp.status_code in (400, 422) and _mentions_thinking_config(body):
                            # 这家中转站不认 Gemini 思考开关：记住，去掉再发一次
                            _NO_GEMINI_THOUGHTS.add(url)
                            continue
                        if ask_usage and resp.status_code in (400, 422) and _mentions_stream_options(body):
                            # 这家中转站不认识 stream_options：记住，去掉再发一次
                            _NO_STREAM_USAGE.add(url)
                            continue
                        if meta is not None:
                            meta["provider_error"] = f"HTTP {resp.status_code}: {_decode_relay_body(body)}"
                        yield _decode_relay_body(body)
                        return
                    if meta is not None and payload["messages"] is not plain_messages:
                        meta["cache_ttl"] = ttl  # 这次真正发出去的缓存时长（写缓存按它计价）
                    async for line in resp.aiter_lines():
                        data = _openai_sse_data(line)
                        if data is None:
                            continue
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            if isinstance(chunk, dict) and chunk.get("error"):
                                if meta is not None:
                                    meta["provider_error"] = data
                                yield data
                                return
                            if chunk.get("usage"):
                                stream_usage = chunk["usage"]
                            if meta is not None and chunk.get("usage"):
                                u = chunk["usage"]
                                meta["prompt_tokens"] = u.get("prompt_tokens", 0)
                                meta["completion_tokens"] = u.get("completion_tokens", 0)
                                meta["total_tokens"] = u.get("total_tokens", 0)
                                meta["raw"] = u
                            delta = chunk["choices"][0].get("delta", {}) if chunk.get("choices") else {}
                            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                            if reasoning:
                                saw_reasoning = True
                                if meta is not None:
                                    meta["reasoning_content"] = meta.get("reasoning_content", "") + str(reasoning)
                                if not reasoning_status_sent and not content_started:
                                    yield f"{CLI_STATUS_PREFIX}正在思考..."
                                    reasoning_status_sent = True
                                # Reasoning is real stream activity even before visible text.
                                yield StreamActivity()
                            if delta.get("content"):
                                content_started = True
                                yield delta["content"]
                        except Exception:
                            pass
            if "extra_body" in payload and content_started and not saw_reasoning and _reasoning_tokens(stream_usage):
                # 模型想过了（用量里有思考字数），但边写边传时中转站没把思考给我们：之后这个模型改成一次性返回
                _STREAM_DROPS_THOUGHTS.add((url, model))
            return
    except httpx.TimeoutException:
        if meta is not None:
            meta["provider_timeout"] = request_timeout
        raise


def _custom_openai_whole_reply(resp: httpx.Response, meta: dict | None) -> list[str]:
    """解析一次性返回（有的中转站只有这样才把 Gemini 的思考带回来）。出错一律记 provider_error、不重发（复审 L2）。"""
    def fail(note: str, shown: str) -> list[str]:
        if meta is not None:
            meta["provider_error"] = note
        return [shown]

    if resp.status_code != 200:
        text = _decode_relay_body(resp.content)
        return fail(f"HTTP {resp.status_code}: {text}", text)
    try:
        data = resp.json()
    except ValueError:
        text = _decode_relay_body(resp.content)
        return fail(f"一次性返回不是 JSON: {text[:300]}", text)
    if not isinstance(data, dict):
        return fail("一次性返回格式不对：顶层不是对象", "[自定义中转站错误] 返回格式不对")
    if data.get("error"):
        error = json.dumps(data["error"], ensure_ascii=False)
        return fail(error, json.dumps(data, ensure_ascii=False))
    choices = data.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    if not isinstance(message, dict):
        return fail("一次性返回格式不对：没有 choices[0].message", "[自定义中转站错误] 返回格式不对")
    # 缺字段或 null 是正常的；字段在、类型却不对，按协议错误记下来（复审 L2 余项）
    usage = data.get("usage")
    content = message.get("content")
    reasoning = message.get("reasoning_content")
    if reasoning is None:
        reasoning = message.get("reasoning")
    for name, value, kind in (("usage", usage, dict), ("message.content", content, str),
                              ("message.reasoning", reasoning, str)):
        if value is not None and not isinstance(value, kind):
            return fail(f"一次性返回格式不对：{name} 类型是 {type(value).__name__}", "[自定义中转站错误] 返回格式不对")
    if meta is not None and usage:
        meta["prompt_tokens"] = usage.get("prompt_tokens", 0)
        meta["completion_tokens"] = usage.get("completion_tokens", 0)
        meta["total_tokens"] = usage.get("total_tokens", 0)
        meta["raw"] = usage
    if reasoning and meta is not None:
        meta["reasoning_content"] = reasoning
    return [content] if content else []

# ── Gemini CLI ────────────────────────────────────
def _find_gemini_script() -> str | None:
    """定位全局安装的 gemini CLI 脚本路径"""
    # 方式1: npm root -g
    try:
        npm_root = subprocess.check_output(["npm", "root", "-g"],
                                           encoding="utf-8", stderr=subprocess.DEVNULL).strip()
        script = Path(npm_root) / "@google" / "gemini-cli" / "bundle" / "gemini.js"
        if script.exists():
            return str(script)
    except Exception:
        pass
    # 方式2: 从 gemini.cmd 位置推导
    try:
        gemini_cmd = shutil.which("gemini")
        if gemini_cmd:
            prefix = Path(gemini_cmd).parent
            script = prefix / "node_modules" / "@google" / "gemini-cli" / "bundle" / "gemini.js"
            if script.exists():
                return str(script)
    except Exception:
        pass
    return None

_GEMINI_SCRIPT: str | None = _find_gemini_script()

def _find_antigravity_binary() -> str | None:
    return antigravity_cli.find_binary()


def _strip_replacement_chars(text: str) -> str:
    """Drop U+FFFD replacement chars introduced by lossy console capture.

    This cannot reconstruct the original emoji/byte, but it prevents the
    visible � marker from being persisted or re-injected into later prompts.
    """
    if not text:
        return text
    return text.replace("\ufffd", "")


def _build_cli_prompt(
    messages: list,
    *,
    copy_cr_uploads: bool = False,
    include_image_refs: bool = True,
) -> str:
    """将 messages 列表拼成供 CLI stdin 使用的完整 prompt。
    图片/音频附件转为本地绝对路径，由 CLI 自行读取文件（避免 base64 超长）。

    优化要点（避免触发 Gemini 3 的 thinking/agent 模式）：
    1. **自动收编伪系统回执对**：项目里历史习惯把人设/能力/记忆等系统配置塞成
       `[user(配置内容)] + [assistant("收到，我会...")]` 的伪对答对。这种结构会让
       Gemini 误以为是 agent 框架的 step-by-step 配置确认，进而开 thinking 模式。
       这里在开头自动识别并合并成一个真正的 [System Instruction] 块，用 # 分节。
    2. 连续同角色消息合并到同一个 [User]/[Assistant] 块，不重复发标签头
       —— 否则会出现连续 `[Assistant]` 这种伪 multi-turn 结构。
    3. 默认将图片/音频附件转换为 CLI 的 @路径语法；调用方可关闭图片引用，
       改用对应 CLI 的原生图片参数。
       CLI 在输入层直接当多模态附件处理，不走 agent tool-use，不触发思考链。
       路径统一转正斜杠，规避 Windows 反斜杠 \\u \\a \\t 被误读为转义。
    """
    # ── 第 0 步：自动收编开头的"伪系统回执对" ──
    # 模板回执话特征（assistant 内容若以这些前缀开头即视为伪回执）
    _FAKE_ACK_PREFIXES = (
        "收到，我会",
        "好的，需要时我会",
        "好的，我会",
        "明白了，我会",
        "收到，我会自然",
        "收到，我会按照",
    )
    system_chunks: list[str] = []
    consume_until = 0
    i = 0
    while i + 1 < len(messages):
        m1 = messages[i]
        m2 = messages[i + 1]
        if (m1.get("role") == "user"
                and m2.get("role") in ("assistant", "model")):
            ack_text = (m2.get("content", "") or "").strip()
            if any(ack_text.startswith(p) for p in _FAKE_ACK_PREFIXES):
                cfg = (m1.get("content", "") or "").strip()
                if cfg:
                    system_chunks.append(cfg)
                consume_until = i + 2
                i += 2
                continue
        break  # 一旦不匹配就停（只收编开头连续的伪对答）

    real_messages = messages[consume_until:]

    # 第一步：拼出每条消息的"角色 + 内容"，先不加标签
    items: list[tuple[str, str]] = []  # (role, text)

    # 先把收编出来的系统块作为单条 system 消息
    if system_chunks:
        items.append(("system", "\n\n".join(system_chunks)))

    for m in real_messages:
        role = m["role"]
        content = _strip_replacement_chars((m.get("content", "") or "")).strip()

        # 处理附件：将图片/音频附件解析为本地绝对路径
        # 关键：不能用 `[图片附件] 路径` 这种 tag 风格的元数据标注，Gemini 会识别为
        # agent/工具调用上下文，触发 thinking 模式输出大段内心戏。
        # 必须把图片提示伪装成用户对话的自然延续，让模型走正常对话路径（实测：
        # 用户直接说"帮我读一下 xxx.jpg"完全干净，但机器拼的 `[图片附件] xxx` 必触发思考）。
        att_image_paths: list[str] = []
        att_audio_paths: list[str] = []
        if role == "user":
            attachments = m.get("attachments", [])
            if isinstance(attachments, str):
                try:
                    attachments = json.loads(attachments) if attachments else []
                except Exception:
                    attachments = []
            for att in attachments:
                if isinstance(att, dict):
                    continue  # 跳过 voice/video 等结构化附件（已有 transcript 文本）
                fpath = _resolve_attachment_path(att)
                if not fpath:
                    continue
                if copy_cr_uploads:
                    fpath = _ensure_gemini_accessible(fpath)
                if fpath and fpath.exists():
                    mime = mimetypes.guess_type(str(fpath))[0] or ""
                    if mime.startswith("image/"):
                        att_image_paths.append(str(fpath.resolve()))
                    elif mime.startswith("audio/"):
                        att_audio_paths.append(str(fpath.resolve()))

        if role in ("assistant", "model"):
            # 历史 assistant 消息防御性清洗：如果数据库里残留了之前未过滤干净的
            # <image_description>/<thought>/Footnote{...}/CRITICAL INSTRUCTION 痕迹，
            # 必须剥掉再喂给 CLI。否则 Gemini 会把它当作"标准回复格式"持续模仿。
            content = _strip_gemini_cli_noise(content)
            unified_role = "assistant"
        elif role == "system":
            unified_role = "system"
        else:
            unified_role = "user"

        text = content
        if att_image_paths and include_image_refs:
            # Gemini CLI 原生 @路径 语法：直接在文本末尾追加 @绝对路径，
            # CLI 会在输入层当作多模态附件处理，不经过 agent tool-use，不触发思考链。
            # 路径统一转正斜杠，防止 Windows 反斜杠 \u \a \t 等被误读为转义。
            safe_paths = [p.replace("\\", "/") for p in att_image_paths]
            at_refs = " ".join(f"@{p}" for p in safe_paths)
            text = (text + "\n" + at_refs).strip() if text else at_refs
        if att_audio_paths:
            safe_paths = [p.replace("\\", "/") for p in att_audio_paths]
            at_refs = " ".join(f"@{p}" for p in safe_paths)
            text = (text + "\n" + at_refs).strip() if text else at_refs

        if not text:
            continue
        items.append((unified_role, text))

    # 第二步：连续同角色合并
    merged: list[tuple[str, str]] = []
    for role, text in items:
        if merged and merged[-1][0] == role:
            merged[-1] = (role, merged[-1][1] + "\n\n" + text)
        else:
            merged.append((role, text))

    # 第三步：拼最终 prompt
    parts = []
    for role, text in merged:
        if role == "system":
            parts.append(f"[System Instruction]\n{text}")
        elif role == "assistant":
            parts.append(f"[Assistant]\n{text}")
        else:
            parts.append(f"[User]\n{text}")
    parts.append("[Assistant]")
    return "\n\n".join(parts)


def _collect_cli_image_paths(messages: list) -> tuple[str, ...]:
    """收集用户消息中的本地图片，供支持原生图片参数的 CLI 使用。"""
    found: dict[str, str] = {}
    for message in messages:
        if message.get("role") != "user":
            continue
        attachments = message.get("attachments", [])
        if isinstance(attachments, str):
            try:
                attachments = json.loads(attachments) if attachments else []
            except Exception:
                attachments = []
        for attachment in attachments:
            fpath = _resolve_attachment_path(attachment)
            if not fpath or not fpath.exists():
                continue
            mime = mimetypes.guess_type(str(fpath))[0] or ""
            if not mime.startswith("image/"):
                continue
            resolved = str(fpath.resolve())
            found[os.path.normcase(resolved)] = resolved
    return tuple(found.values())


async def _spawn_cli_process(cmd: list[str], prompt: str, env: dict | None = None):
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.PIPE,
        env=env,
        limit=8 * 1024 * 1024,
    )
    own_process(proc)
    proc.stdin.write(prompt.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()
    await proc.stdin.wait_closed()
    return proc

# Gemini CLI tool_name → 中文状态标签映射
_CLI_TOOL_LABELS = {
    "google_web_search": "🔍 联网搜索",
    "web_search":        "🔍 联网搜索",
    "web_fetch":         "🌐 抓取网页",
    "read_file":         "📄 读取文件",
    "read_many_files":   "📄 批量读取文件",
    "write_file":        "📝 写入文件",
    "edit_file":         "✏️ 编辑文件",
    "list_directory":    "📂 列出目录",
    "grep":              "🔎 搜索文本",
    "glob":              "🔎 搜索文件",
    "run_shell_command": "⚙️ 执行命令",
    "shell":             "⚙️ 执行命令",
}

async def call_gemini_cli(messages: list, model: str, meta: dict | None = None,
                          temperature: float | None = None, max_tokens: int | None = None):
    """通过 gemini CLI 子进程流式获取响应（stream-json 模式，支持 token 统计）"""
    prompt = _build_cli_prompt(messages, copy_cr_uploads=True)

    # 构建命令
    node = shutil.which("node") or "node"
    if _GEMINI_SCRIPT:
        cmd = [node, _GEMINI_SCRIPT]
    else:
        gemini_bin = shutil.which("gemini")
        if not gemini_bin:
            yield "[GeminiCLI错误] 未找到 gemini CLI，请先运行 npm install -g @google/gemini-cli"
            return
        cmd = [gemini_bin]

    if model:
        cmd.extend(["-m", model])
    # --skip-trust 跳过目录信任检查；-p " " 触发非交互模式，实际 prompt 通过 stdin 传入
    # -o stream-json 启用 JSONL 流模式，每行一个 JSON 事件：
    #   init / message(user) / tool_use / tool_result / message(assistant,delta) / result(stats)
    # 好处：结构化解析只提取 assistant 正文，tool_use/tool_result 转为状态事件，
    # 不再需要 GeminiCliNoiseFilter 噪音过滤；result 事件自带 token 统计。
    # --approval-mode yolo 允许 CLI 自动执行文件读写和工具调用（如下载图片存盘），
    # plan 模式下非交互模式 write_file 等工具默认被拒绝。
    # 通过前端「允许调用工具」开关控制。
    approval = "yolo" if SETTINGS.get("gemini_cli_tools_enabled", False) else "plan"
    cmd.extend(["--skip-trust", "--approval-mode", approval, "-o", "stream-json", "-p", " "])

    try:
        proc = await _spawn_cli_process(cmd, prompt)

        # 调试日志
        debug_log = None
        if os.environ.get("GEMINI_CLI_DEBUG") == "1":
            from datetime import datetime
            log_dir = Path(__file__).parent / "data" / "cli_debug"
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            debug_log = log_dir / f"gemini_{ts}.log"
            with open(debug_log, "w", encoding="utf-8") as f:
                f.write("=== PROMPT ===\n")
                f.write(prompt)
                f.write("\n\n=== RAW JSONL ===\n")

        # stream-json 模式：按块读取后再切 JSONL。
        # 不能直接 `async for line in proc.stdout`，否则超长 JSON 行会触发
        # asyncio StreamReader 的行缓冲上限，报：
        # `Separator is not found, and chunk exceed the limit`
        line_buf = ""
        while True:
            chunk = await proc.stdout.read(64 * 1024)
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            if not text:
                continue
            if debug_log:
                with open(debug_log, "a", encoding="utf-8") as f:
                    f.write(text)
            line_buf += text
            while "\n" in line_buf:
                line, line_buf = line_buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type", "")

                if etype == "message":
                    # 只提取 assistant 的增量文本 (delta=true)
                    if event.get("role") == "assistant" and event.get("delta"):
                        content = event.get("content", "")
                        if content:
                            yield content

                elif etype == "tool_use":
                    tool_name = event.get("tool_name", "")
                    params = event.get("parameters", {})
                    label = _CLI_TOOL_LABELS.get(tool_name, f"🔧 {tool_name}")
                    # 构造简洁的状态描述
                    detail = ""
                    if "query" in params:
                        detail = f"：{params['query'][:60]}"
                    elif "command" in params:
                        cmd_str = params["command"]
                        detail = f"：{cmd_str[:60]}{'…' if len(cmd_str) > 60 else ''}"
                    elif "path" in params:
                        detail = f"：{params['path']}"
                    elif "pattern" in params:
                        detail = f"：{params['pattern']}"
                    yield f"{CLI_STATUS_PREFIX}{label}{detail}…"

                elif etype == "tool_result":
                    status = event.get("status", "")
                    tool_id = event.get("tool_id", "")
                    # tool_id 格式如 "google_web_search_1234_0"，提取 tool_name
                    parts = tool_id.rsplit("_", 2)
                    tname = "_".join(parts[:-2]) if len(parts) >= 3 else tool_id
                    label = _CLI_TOOL_LABELS.get(tname, f"🔧 {tname}")
                    if status == "success":
                        yield f"{CLI_STATUS_PREFIX}✅ {label} 完成"
                    else:
                        yield f"{CLI_STATUS_PREFIX}❌ {label} 失败"

                elif etype == "result":
                    # 提取 token 统计
                    stats = event.get("stats", {})
                    if meta is not None and stats:
                        meta["prompt_tokens"] = stats.get("input_tokens", 0)
                        meta["completion_tokens"] = stats.get("output_tokens", 0)
                        meta["total_tokens"] = stats.get("total_tokens", 0)
                        meta["raw"] = stats

                elif etype == "error":
                    err_msg = event.get("message", "") or event.get("error", "")
                    if err_msg:
                        yield f"\n[GeminiCLI错误] {err_msg[:500]}"
        if line_buf.strip():
            try:
                event = json.loads(line_buf.strip())
            except json.JSONDecodeError:
                event = None
            if event and event.get("type") == "error":
                err_msg = event.get("message", "") or event.get("error", "")
                if err_msg:
                    yield f"\n[GeminiCLI错误] {err_msg[:500]}"

        if debug_log:
            with open(debug_log, "a", encoding="utf-8") as f:
                f.write("\n\n=== END ===\n")

        await proc.wait()
        if proc.returncode and proc.returncode != 0:
            stderr_out = await proc.stderr.read()
            err = stderr_out.decode("utf-8", errors="replace").strip()
            report_err, report_path = _extract_gemini_cli_report_error(err)
            if report_err:
                detail = f"\n完整报告：{report_path}" if report_path else ""
                yield f"\n[GeminiCLI错误] {report_err[:500]}{detail}"
            elif err:
                yield f"\n[GeminiCLI错误 code={proc.returncode}] {err[:500]}"

    except FileNotFoundError:
        yield "[GeminiCLI错误] 无法启动 gemini CLI 进程"
    except Exception as e:
        yield f"[GeminiCLI错误] {e}"


# ── Antigravity CLI ───────────────────────────────

async def call_antigravity_cli(messages: list, model: str, meta: dict | None = None,
                               temperature: float | None = None, max_tokens: int | None = None):
    """Use the official stdin/stdout stream with a tool-free companion agent."""
    binary = _find_antigravity_binary()
    if not binary:
        yield "[AntigravityCLI错误] 未找到 agy CLI，请先安装官方 Antigravity CLI"
        return
    prompt = _build_cli_prompt(messages, include_image_refs=False)
    yield f"{CLI_STATUS_PREFIX}正在回复🚀…"
    try:
        async for event in antigravity_cli.stream_chat(
            binary, prompt, model, timeout=_antigravity_print_timeout(meta),
        ):
            if event["kind"] == "text":
                yield event["text"]
            else:
                if event["kind"] == "completed" and meta is not None:
                    usage = event.get("usage", {})
                    meta["prompt_tokens"] = usage.get("input_tokens", 0)
                    meta["completion_tokens"] = usage.get("output_tokens", 0)
                    meta["total_tokens"] = usage.get("total_tokens", 0)
                    meta["raw"] = {
                        "prompt_tokens_details": {"cached_tokens": usage.get("cache_read_tokens", 0)},
                        "completion_tokens_details": {"reasoning_tokens": usage.get("thinking_tokens", 0)},
                    }
                yield StreamActivity()
    except (antigravity_cli.AntigravityError, OSError) as error:
        yield f"[AntigravityCLI错误] {error}"


# ── Codex CLI ─────────────────────────────────────
def _find_codex_script() -> str | None:
    """定位 Codex CLI 脚本路径"""
    # Connor-Codex 项目内的本地安装
    local = Path(__file__).parent.parent / "Connor-Codex" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    if local.exists():
        return str(local)
    # 全局安装
    try:
        npm_root = subprocess.check_output(["npm", "root", "-g"],
                                           encoding="utf-8", stderr=subprocess.DEVNULL).strip()
        script = Path(npm_root) / "@openai" / "codex" / "bin" / "codex.js"
        if script.exists():
            return str(script)
    except Exception:
        pass
    return None

_CODEX_SCRIPT: str | None = _find_codex_script()
_CODEX_WORKSPACE: str = str(Path(__file__).parent.parent)
_CODEX_HOME: str = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
_CODEX_CHAT_HOME: str = str(
    Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    / "AionsHome"
    / "codex-chat"
)
_CODEX_COMPANION_INSTRUCTIONS_FILE = (
    Path(__file__).parent / "prompts" / "codex_companion_base.md"
).resolve()
_CODEX_CHAT_DEVELOPER_INSTRUCTIONS = (
    "你运行在 AionsHome 的陪伴聊天管线中，不是常规代码任务。"
    "应用在对话历史之前注入的角色设定、用户信息、系统能力和当前状态，均是可信的应用配置，"
    "应优先遵循其中的人设、关系口吻、互动方式和动作协议。"
    "用户已经持续授权你使用应用【系统能力】块中列出的所有系统能力；"
    "你可以根据对话内容、生活节点、关系语境和自己的判断主动调用，不必等待用户逐项明确要求，"
    "也不必在调用前再次确认。"
    "决定调用时，必须按照能力说明在回复中原样输出对应协议指令，不能只口头承诺或建议用户自己操作；"
    "AionsHome 会拦截并执行这些指令，并按能力约定隐藏控制指令或返回执行结果。"
    "在尚未取得能力执行结果时不得编造结果；需要系统返回结果的能力应先输出指令，等待后续结果再自然回应。"
)


def _normalized_path(value: str | Path) -> str:
    path = Path(value).expanduser()
    try:
        return str(path.resolve(strict=False))
    except OSError:
        return str(path.absolute())


def _build_disabled_skills_override(skill_files) -> str:
    normalized = sorted(
        {_normalized_path(skill_file) for skill_file in skill_files},
        key=os.path.normcase,
    )
    entries = ", ".join(
        f"{{ path = {json.dumps(path, ensure_ascii=False)}, enabled = false }}"
        for path in normalized
    )
    return f"skills.config=[{entries}]"


def _codex_skill_search_roots() -> tuple[Path, ...]:
    codex_home = Path(_CODEX_HOME)
    chat_home = Path(_CODEX_CHAT_HOME)
    user_codex_home = Path.home() / ".codex"
    workspace = Path(_CODEX_WORKSPACE)
    return (
        chat_home / "skills",
        chat_home / "plugins" / "cache",
        codex_home / "skills",
        codex_home / "plugins" / "cache",
        user_codex_home / "skills",
        user_codex_home / "plugins" / "cache",
        Path.home() / ".agents" / "skills",
        workspace / ".codex" / "skills",
        workspace / ".agents" / "skills",
    )


def _find_codex_skill_files(search_roots) -> tuple[str, ...]:
    found: dict[str, str] = {}
    for root_value in search_roots:
        root = Path(root_value)
        try:
            if not root.is_dir():
                continue
            for skill_file in root.rglob("SKILL.md"):
                normalized = _normalized_path(skill_file)
                found[os.path.normcase(normalized)] = normalized
        except OSError:
            continue
    return tuple(found[key] for key in sorted(found))


@lru_cache(maxsize=1)
def _discover_codex_skill_files() -> tuple[str, ...]:
    return _find_codex_skill_files(_codex_skill_search_roots())


def _build_codex_chat_environment(base_env: dict | None = None, *, prepare_catalog: bool = True) -> dict:
    """Create a minimal Codex home for the companion-chat subprocess.

    prepare_catalog=False skips the (blocking) companion model catalog step; only asking
    Codex which models exist (model/list) does not need it."""
    chat_home = Path(_CODEX_CHAT_HOME)
    desktop_auth = Path(_CODEX_HOME) / "auth.json"
    chat_auth = chat_home / "auth.json"

    if desktop_auth.is_file():
        chat_home.mkdir(parents=True, exist_ok=True)
        if not chat_auth.exists() or desktop_auth.stat().st_mtime_ns > chat_auth.stat().st_mtime_ns:
            shutil.copy2(desktop_auth, chat_auth)

    # Do not inherit desktop task tools/identity when the home server was
    # launched from a Codex terminal. Repair has its own environment builder.
    excluded = {
        "CODEX_APP_TOOLS_PIPE_PATH", "CODEX_THREAD_ID", "CODEX_SESSION_ID",
        "CODEX_INTERNAL_ORIGINATOR_OVERRIDE", "CODEX_PERMISSION_PROFILE",
    }
    environment = {
        key: value for key, value in (base_env if base_env is not None else os.environ).items()
        if key not in excluded
    }
    if _CODEX_SCRIPT and prepare_catalog:
        prepare_chat_model_catalog(
            chat_home, shutil.which("node") or "node", _CODEX_SCRIPT,
            source_cache=Path(_CODEX_HOME) / "models_cache.json",
        )
    chat_profile_root = str(chat_home.parent)
    return {
        **environment,
        "NO_COLOR": "1",
        "CODEX_HOME": str(chat_home),
        "HOME": chat_profile_root,
        "USERPROFILE": chat_profile_root,
    }


def _build_codex_chat_command(
    node: str,
    script: str,
    workspace: str,
    model: str,
    skill_files=None,
    image_paths=None,
) -> list[str]:
    overrides = [
        'model_verbosity="high"',
        f"model_instructions_file={json.dumps(str(_CODEX_COMPANION_INSTRUCTIONS_FILE), ensure_ascii=False)}",
        f"model_catalog_json={json.dumps(str(Path(_CODEX_CHAT_HOME) / 'companion-models.json'), ensure_ascii=False)}",
        f"developer_instructions={json.dumps(_CODEX_CHAT_DEVELOPER_INSTRUCTIONS, ensure_ascii=False)}",
        "features.shell_tool=false",
        "features.multi_agent=false",
        "features.multi_agent_v2=false",
        "features.code_mode=false",
        "features.code_mode_only=false",
        "features.goals=false",
        "features.sleep_tool=false",
        "features.plugins=false",
        "features.apps=false",
        "features.image_generation=false",
        "features.skill_search=false",
        "features.skill_mcp_dependency_install=false",
        "features.view_image=true",
        'web_search="disabled"',
        "tools.experimental_request_user_input.enabled=false",
        "tools.update_plan.enabled=false",
        "features.remote_plugin=false",
        "include_apps_instructions=false",
        "include_permissions_instructions=false",
        "include_collaboration_mode_instructions=false",
        "include_environment_context=false",
    ]
    disabled_skills = (
        tuple(skill_files) if skill_files is not None else _discover_codex_skill_files()
    )
    if disabled_skills:
        overrides.append(_build_disabled_skills_override(disabled_skills))

    return build_codex_app_server_command(node, script, overrides)


def _build_codex_sentinel_command(
    node: str,
    script: str,
    skill_files=None,
) -> list[str]:
    """Build a small, tool-free Codex profile for latency-sensitive patrols."""
    overrides = [
        'model_reasoning_effort="none"',
        'model_verbosity="low"',
        'developer_instructions="You are the AionsHome sentinel classifier. Follow the user prompt exactly and return only the requested result. Do not use tools."',
        "features.shell_tool=false",
        "features.multi_agent=false",
        'features.multi_agent_v2={ root_agent_usage_hint_text = "", multi_agent_mode_hint_text = "" }',
        "features.remote_plugin=false",
        "include_apps_instructions=false",
        "include_permissions_instructions=false",
        "include_collaboration_mode_instructions=false",
        "include_environment_context=false",
    ]
    disabled_skills = (
        tuple(skill_files) if skill_files is not None else _discover_codex_skill_files()
    )
    if disabled_skills:
        overrides.append(_build_disabled_skills_override(disabled_skills))
    return build_codex_app_server_command(node, script, overrides)


_CODEX_REASONING_SUMMARIES = {"auto", "concise", "detailed", "none"}
_CODEX_SEMAPHORE_LOOP = None
_CODEX_SEMAPHORE_LIMIT = 0
_CODEX_SEMAPHORE: asyncio.Semaphore | None = None


def _codex_reasoning_summary(settings: dict | None = None) -> str:
    # 默认「详细」：auto 只给一句 30 来字的标题、常常干脆不给（Lucien 的 💭，2026-10-29）
    value = str((settings if settings is not None else SETTINGS).get(
        "codex_reasoning_summary", "detailed"
    ) or "detailed").strip().lower()
    return value if value in _CODEX_REASONING_SUMMARIES else "detailed"


def _codex_max_concurrency(settings: dict | None = None) -> int:
    raw = (settings if settings is not None else SETTINGS).get(
        "codex_max_concurrent_requests", 2
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 2
    return min(8, max(1, value))


def _codex_semaphore() -> asyncio.Semaphore:
    global _CODEX_SEMAPHORE_LOOP, _CODEX_SEMAPHORE_LIMIT, _CODEX_SEMAPHORE
    loop = asyncio.get_running_loop()
    limit = _codex_max_concurrency()
    if (
        _CODEX_SEMAPHORE is None
        or _CODEX_SEMAPHORE_LOOP is not loop
        or _CODEX_SEMAPHORE_LIMIT != limit
    ):
        _CODEX_SEMAPHORE_LOOP = loop
        _CODEX_SEMAPHORE_LIMIT = limit
        _CODEX_SEMAPHORE = asyncio.Semaphore(limit)
    return _CODEX_SEMAPHORE


def _normalize_codex_app_server_usage(usage: dict | None) -> dict:
    usage = usage or {}
    return {
        "input_tokens": usage.get("inputTokens", usage.get("input_tokens", 0)),
        "cached_input_tokens": usage.get(
            "cachedInputTokens", usage.get("cached_input_tokens", 0)
        ),
        "output_tokens": usage.get("outputTokens", usage.get("output_tokens", 0)),
        "reasoning_output_tokens": usage.get(
            "reasoningOutputTokens", usage.get("reasoning_output_tokens", 0)
        ),
        "total_tokens": usage.get("totalTokens", usage.get("total_tokens", 0)),
    }


def _usage_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _apply_codex_usage_meta(meta: dict | None, usage: dict | None) -> None:
    if meta is None or not usage:
        return

    input_tokens = _usage_int(usage.get("input_tokens"))
    output_tokens = _usage_int(usage.get("output_tokens"))
    total_tokens = _usage_int(usage.get("total_tokens")) or input_tokens + output_tokens
    cached_tokens = _usage_int(usage.get("cached_input_tokens"))
    reasoning_tokens = _usage_int(usage.get("reasoning_output_tokens"))

    meta["prompt_tokens"] = input_tokens
    meta["completion_tokens"] = output_tokens
    meta["total_tokens"] = total_tokens

    raw = dict(usage)
    raw.setdefault("prompt_tokens", input_tokens)
    raw.setdefault("completion_tokens", output_tokens)
    raw.setdefault("total_tokens", total_tokens)

    if cached_tokens:
        prompt_details = dict(raw.get("prompt_tokens_details") or {})
        prompt_details.setdefault("cached_tokens", cached_tokens)
        raw["prompt_tokens_details"] = prompt_details

    if reasoning_tokens:
        completion_details = dict(raw.get("completion_tokens_details") or {})
        completion_details.setdefault("reasoning_tokens", reasoning_tokens)
        raw["completion_tokens_details"] = completion_details

    meta["raw"] = raw


async def call_codex_cli(messages: list, model: str, meta: dict | None = None,
                         temperature: float | None = None, max_tokens: int | None = None):
    """通过请求级 Codex App Server 会话增量返回正文与可读推理摘要。"""
    image_paths = _collect_cli_image_paths(messages)
    prompt = _build_cli_prompt(messages, include_image_refs=False)

    node = shutil.which("node") or "node"
    if not _CODEX_SCRIPT:
        yield "[CodexCLI错误] 未找到 Codex CLI，请检查 Connor-Codex/node_modules/@openai/codex 是否已安装"
        return

    if not _CODEX_COMPANION_INSTRUCTIONS_FILE.is_file():
        yield "[CodexCLI错误] 陪伴模式基础指令文件缺失，已停止启动 Codex CLI"
        return

    cmd = _build_codex_chat_command(
        node,
        _CODEX_SCRIPT,
        _CODEX_WORKSPACE,
        model,
        image_paths=image_paths,
    )

    try:
        env = _build_codex_chat_environment()
        async with _codex_semaphore():
            async for event in own_stream(stream_codex_app_server(
                cmd,
                env=env,
                cwd=_CODEX_WORKSPACE,
                model=model,
                prompt=prompt,
                image_paths=list(image_paths),
                reasoning_summary=_codex_reasoning_summary(),
            )):
                if event.kind == "text_delta" and event.text:
                    yield event.text
                elif event.kind == "reasoning_delta" and event.text:
                    if meta is not None:
                        current = str(meta.get("reasoning_content") or "")
                        meta["reasoning_content"] = current + event.text
                    yield StreamActivity()
                elif event.kind == "usage":
                    _apply_codex_usage_meta(
                        meta,
                        _normalize_codex_app_server_usage(event.usage),
                    )
                    yield StreamActivity()
                elif event.kind == "activity":
                    yield StreamActivity()
    except FileNotFoundError:
        yield "[CodexCLI错误] 无法启动 Codex CLI 进程"
    except Exception as e:
        yield f"[CodexCLI错误] {e}"


async def call_codex_sentinel(
    prompt: str,
    *,
    model: str = "gpt-5.6-luna",
    image_b64: str | None = None,
    mime_type: str = "image/jpeg",
    timeout: int = 60,
    use_chat_slot: bool = True,
) -> str:
    """Run one isolated Luna sentinel turn through the local Codex login."""
    if not _CODEX_SCRIPT:
        raise RuntimeError("未找到 Codex CLI，无法使用 GPT-5.6 Luna 哨兵线路")

    node = shutil.which("node") or "node"
    command = _build_codex_sentinel_command(node, _CODEX_SCRIPT)
    env = _build_codex_chat_environment()

    async def _run(workspace: str, image_paths: list[str]) -> str:
        chunks: list[str] = []
        # The post-sentinel has its own serial worker and must not reserve a
        # foreground chat slot. Existing callers retain their original limit.
        async with (_codex_semaphore() if use_chat_slot else nullcontext()):
            async for event in stream_codex_app_server(
                command,
                env=env,
                cwd=workspace,
                model=model,
                prompt=prompt,
                image_paths=image_paths,
                reasoning_summary="none",
            ):
                if event.kind == "text_delta" and event.text:
                    chunks.append(event.text)
        return "".join(chunks).strip()

    with tempfile.TemporaryDirectory(prefix="aionshome-sentinel-") as temp_dir:
        image_paths: list[str] = []
        if image_b64:
            suffix = mimetypes.guess_extension(mime_type) or ".jpg"
            image_path = Path(temp_dir) / f"sentinel_input{suffix}"
            image_path.write_bytes(base64.b64decode(image_b64))
            image_paths.append(str(image_path))
        try:
            return await asyncio.wait_for(
                _run(temp_dir, image_paths),
                timeout=max(1, int(timeout)),
            )
        except TimeoutError as error:
            raise RuntimeError(f"GPT-5.6 Luna 哨兵调用超过 {timeout} 秒") from error


# ── 非流式调用（收集流式输出） ────────────────────
def _cleanup_model_raw_responses(log_dir: Path = MODEL_RAW_RESPONSE_DIR, now: float | None = None) -> int:
    """删除超过三天的非流式模型原始响应日志。"""
    cutoff = float(now if now is not None else time.time()) - MODEL_RAW_RESPONSE_RETENTION_SECONDS
    if not log_dir.exists():
        return 0
    removed = 0
    for path in log_dir.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _save_model_raw_response(
    *,
    messages: list,
    model_key: str,
    trace_label: str,
    raw_response: str,
    filtered_response: str,
    error: str = "",
    started_at: float,
    finished_at: float,
    log_dir: Path = MODEL_RAW_RESPONSE_DIR,
) -> Path | None:
    """保存模型调用的原始返回和请求上下文，供近三天故障追踪。"""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        _cleanup_model_raw_responses(log_dir, finished_at)
        safe_label = re.sub(r"[^0-9A-Za-z_.-]+", "_", trace_label or "simple_ai_call").strip("_")[:60]
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(started_at))
        path = log_dir / f"{stamp}_{safe_label or 'simple_ai_call'}_{uuid.uuid4().hex[:8]}.json"
        payload = {
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_seconds": round(max(0.0, finished_at - started_at), 3),
            "trace_label": trace_label or "simple_ai_call",
            "model_key": model_key,
            "provider": (MODELS.get(model_key) or {}).get("provider", "unknown"),
            "request_messages": messages,
            "raw_response": raw_response,
            "filtered_response": filtered_response,
            "error": error,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return path
    except Exception as exc:
        print(f"[model_raw_response] 保存失败: {exc}")
        return None


# 服务启动时也执行一次，避免长时间没有新调用时遗留过期日志。
_cleanup_model_raw_responses()


async def simple_ai_call(
    messages: list,
    model_key: str,
    temperature: float | None = None,
    *,
    trace_label: str = "simple_ai_call",
    include_device_context: bool = False,
) -> str:
    """收集 stream_ai 的全部 chunk并留存三天原始响应，返回过滤状态行后的正文。"""
    # 用量记到真正发起的功能名下（第一条语句，还在调用方的 await 里，取得到调用方）
    caller = sys._getframe(1).f_globals.get("__name__", "")
    model_key = resolve_model_key(model_key)
    started_at = time.time()
    full_text = ""
    raw_chunks = []
    error = ""
    try:
        async for chunk in _stream_ai_counted(
            caller,
            messages,
            model_key,
            None,
            temperature=temperature,
            include_device_context=include_device_context,
        ):
            raw_chunks.append(chunk)
            if chunk.startswith(CLI_STATUS_PREFIX):
                continue
            full_text += chunk
        return full_text
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        _save_model_raw_response(
            messages=messages,
            model_key=model_key,
            trace_label=trace_label,
            raw_response="".join(raw_chunks),
            filtered_response=full_text,
            error=error,
            started_at=started_at,
            finished_at=time.time(),
        )


# ── 哨兵代看：为不支持视觉的模型描述图片 ─────────────
_IMAGE_MIME_PREFIXES = ("image/",)

def _messages_have_images(messages: list) -> bool:
    """检查消息列表中是否存在图片附件"""
    for m in messages:
        if m.get("role") != "user":
            continue
        atts = m.get("attachments", [])
        if isinstance(atts, str):
            try: atts = json.loads(atts) if atts else []
            except: atts = []
        for att in atts:
            fpath = _resolve_attachment_path(att)
            if fpath and fpath.exists():
                mime = mimetypes.guess_type(str(fpath))[0] or ""
                if mime.startswith("image/"):
                    return True
    return False


async def _sentinel_describe_images(messages: list) -> list:
    """用哨兵模型识别消息中的图片，将描述注入文本并剥离图片附件。
    优先使用用户配置的哨兵模型，失败则回退到 gemini-3.1-flash-lite。"""
    from memory import _call_sentinel_vision
    scfg = get_sentinel_config()

    result = []
    for m in messages:
        nm = dict(m)
        atts = nm.get("attachments", [])
        if isinstance(atts, str):
            try: atts = json.loads(atts) if atts else []
            except: atts = []

        if nm.get("role") != "user" or not atts:
            result.append(nm)
            continue

        img_descs = []
        non_img_atts = []
        for att in atts:
            fpath = _resolve_attachment_path(att)
            if not fpath or not fpath.exists():
                non_img_atts.append(att)
                continue
            mime = mimetypes.guess_type(str(fpath))[0] or ""
            if not mime.startswith("image/"):
                non_img_atts.append(att)
                continue
            # 识图
            img_b64 = base64.b64encode(fpath.read_bytes()).decode()
            prompt = "请详细描述这张图片的内容，包括画面中的人物、物体、文字、场景、颜色、构图等关键信息。用中文回答，尽量简洁但不遗漏重要细节。"
            desc = None
            try:
                desc = await _call_sentinel_vision(scfg, prompt, img_b64, mime, timeout=30)
            except Exception as e:
                print(f"[Vision Fallback] 哨兵模型识图失败: {e}，尝试回退 gemini-3.1-flash-lite")
                # 回退到 Gemini flash-lite
                fallback_cfg = {
                    "base_url": "",
                    "api_key": get_key("gemini_free"),
                    "model": "gemini-3.1-flash-lite",
                    "use_openai": False,
                }
                try:
                    desc = await _call_sentinel_vision(fallback_cfg, prompt, img_b64, mime, timeout=30)
                except Exception as e2:
                    print(f"[Vision Fallback] 回退模型也失败: {e2}")
            if desc:
                img_descs.append(f"[图片内容：{desc}]")
            else:
                img_descs.append("[图片内容：识别失败]")

        # 将图片描述注入到消息文本前面
        if img_descs:
            desc_text = "\n".join(img_descs)
            nm["content"] = f"{desc_text}\n{nm.get('content', '')}".strip()
        nm["attachments"] = non_img_atts
        result.append(nm)
    return result


# ── 统一调度 ──────────────────────────────────────
def with_current_device_context(
    messages: list,
    *,
    insert_at: int | None = None,
    add_acknowledgement: bool = False,
) -> list:
    prepared = [dict(message) for message in messages]
    if any(
        "【设备当前状态】" in str(message.get("content") or "")
        for message in prepared
    ):
        return prepared
    try:
        from activity import get_device_context_for_prompt
        device_context = get_device_context_for_prompt()
    except Exception:
        device_context = ""
    if not device_context:
        return prepared

    if insert_at is not None:
        index = max(0, min(len(prepared), int(insert_at)))
        prepared.insert(index, {"role": "user", "content": device_context, "_context": "live"})
        if add_acknowledgement:
            prepared.insert(index + 1, {
                "role": "assistant",
                "content": "收到，我会结合当前设备状态判断。",
                "_context": "live_ack",
            })
        return prepared

    for index in range(len(prepared) - 1, -1, -1):
        if prepared[index].get("role") in ("user", "cam_user", "cam_trigger"):
            content = str(prepared[index].get("content") or "").rstrip()
            prepared[index]["content"] = f"{content}\n\n{device_context}".strip()
            return prepared
    prepared.append({"role": "user", "content": device_context})
    return prepared


def stream_ai(messages: list, model_key: str, meta: dict | None = None, *args, **kwargs):
    """所有模型调用的总入口：照常流式返回，结束时记一笔用量（见 usage_log）。

    用途按调用方所在模块归类，所以这里在调用的那一刻记下调用方。
    """
    caller = sys._getframe(1).f_globals.get("__name__", "")
    return _stream_ai_counted(caller, messages, model_key, meta, *args, **kwargs)


_USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens", "raw", "cache_ttl")


async def _stream_ai_counted(caller: str, messages: list, model_key: str, meta: dict | None, *args, **kwargs):
    import usage_log
    from contextlib import aclosing
    meta = meta if meta is not None else {}
    resolved = resolve_model_key(model_key)
    counted = resolved in MODELS and not is_model_deprecated(resolved)
    # 发请求之前就定下这次算哪条线路；请求途中改绑 / 删掉模型也不影响这一笔
    route = usage_log.route_for_model(resolved)
    # 同一个 meta 可能被调用方重复用：先把上一次的用量拿走，这次中转站给了才算真实数字
    previous = {key: meta.pop(key) for key in _USAGE_KEYS if key in meta}
    output: list[str] = []
    try:
        # aclosing：被提前关闭时，先等下层的请求真正关掉，再记账、再返回
        async with aclosing(_stream_ai(messages, model_key, meta, *args, **kwargs)) as inner:
            async for chunk in inner:
                if isinstance(chunk, str) and not chunk.startswith(CLI_STATUS_PREFIX):
                    output.append(chunk)
                yield chunk
    finally:
        reported = any(key in meta for key in ("prompt_tokens", "completion_tokens"))
        if counted:
            usage_log.record_call(resolved, usage_log.kind_for_module(caller), meta if reported else {},
                                  usage_log.messages_text(messages), "".join(output), route=route)
        if not reported:
            meta.update(previous)  # 这次没报告：调用方看到的仍是原来的值


async def _stream_ai(messages: list, model_key: str, meta: dict | None = None, temperature: float | None = None, max_tokens: int | None = None, cancel_event=None, *, include_device_context: bool = True, request_timeout: float = 120):
    if include_device_context:
        messages = with_current_device_context(messages)
    model_key = resolve_model_key(model_key)
    normalized = []
    for m in messages:
        nm = dict(m)
        if nm["role"] in ("cam_user", "cam_trigger"):
            nm["role"] = "user"
        elif nm["role"] == "cam_log":
            nm["role"] = "assistant"
        normalized.append(nm)
    cfg = MODELS.get(model_key)
    if not cfg:
        yield f"[错误] {NO_ROUTE_HINT}" if not model_key else f"[错误] 未知模型: {model_key}"
        return
    if is_model_deprecated(model_key):
        yield f"[错误] 模型线路已停用: {model_key}"
        return
    # 开着缓存的中转站线路：把会变的背景挪到聊天记录之后（中转站不认标记退回纯文本时也保持这个顺序，
    # 对自动前缀缓存同样有利）。其他线路顺序不变。内部标签一律去掉再往下传
    if cfg.get("provider") == CUSTOM_OPENAI_PROVIDER and cfg.get("cache_marker", True) is not False:
        normalized = _cache_friendly_order(normalized)
    normalized = _without_context_tags(normalized)

    normalized = filter_audio_attachments_for_model(
        normalized,
        include_audio=bool(cfg.get("audio", False)),
    )

    # 非视觉模型 + 消息含图片 → 哨兵代看
    if not cfg.get("vision", True) and _messages_have_images(normalized):
        yield f"{CLI_STATUS_PREFIX}哨兵模型正在识别图片内容..."
        normalized = await _sentinel_describe_images(normalized)
        yield f"{CLI_STATUS_PREFIX}图片处理完成，正在等待模型回复..."
    def _provider_stream():
        provider = cfg["provider"]
        if provider == "siliconflow":
            return call_siliconflow(normalized, cfg["model"], meta, temperature, max_tokens)
        if provider == "gemini":
            return call_gemini(normalized, cfg["model"], meta, temperature, max_tokens,
                               include_audio=bool(cfg.get("audio", False)))
        if provider == "aipro":
            return call_aipro(normalized, cfg["model"], meta, temperature, max_tokens)
        if provider == "custom_openai":
            return call_custom_openai(normalized, cfg, meta, temperature, max_tokens, request_timeout=request_timeout)
        if provider == "gemini_cli":
            return call_gemini_cli(normalized, cfg["model"], meta, temperature, max_tokens)
        if provider == "antigravity_cli":
            return call_antigravity_cli(normalized, cfg["model"], meta, temperature, max_tokens)
        if provider == "codex_cli":
            return call_codex_cli(normalized, cfg["model"], meta, temperature, max_tokens)
        return None

    from contextlib import aclosing
    provider_stream = _provider_stream()
    if provider_stream is None:
        yield f"[错误] 未知 provider: {cfg.get('provider')}"
        return

    think_filter = ThinkTagReasoningFilter(meta)
    # aclosing：上层提前关闭时，一路关到底层的 HTTP 响应 / 子进程，关完才返回
    async with aclosing(provider_stream) as raw:
        async for chunk in raw:
            if cancel_event and cancel_event.is_set():
                return
            if isinstance(chunk, StreamActivity):
                yield chunk
                continue
            if isinstance(chunk, str) and chunk.startswith(CLI_STATUS_PREFIX):
                yield chunk
                continue
            visible = think_filter.feed(chunk)
            if visible:
                yield visible

    tail = think_filter.flush()
    if tail and not (cancel_event and cancel_event.is_set()):
        yield tail
