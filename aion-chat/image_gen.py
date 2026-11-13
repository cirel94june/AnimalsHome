"""
AI 生图模块：可配置 OpenAI 兼容接口，未配置时沿用 Gemini
支持 SELFIE（带参考图）和 DRAW（纯文本）两种模式
"""

import asyncio, base64, re, mimetypes
from contextvars import ContextVar
from pathlib import Path

import httpx

from config import SETTINGS, get_key, PUBLIC_DIR
from album import save_generated_image

# 参考图位置（用于 SELFIE 模式）
REFERENCE_IMAGE_PATH = PUBLIC_DIR / "生图锚点.jpg"
SECONDARY_REFERENCE_IMAGE_PATH = PUBLIC_DIR / "2号机生图锚点.jpg"
IMAGE_GEN_MODEL = "gemini-3.1-flash-lite-image"
IMAGE_GEN_TIMEOUT = 300  # 生图超时秒数（gpt-image 这类图片接口常要一两分钟）
_failure = ContextVar('image_generation_failure', default='')


def failure_reason() -> str:
    return _failure.get() or '这次没有生成出图片，请稍后再试。'


def _fail(reason: str):
    _failure.set(reason)


def _selfie_reference_path(source_identity: str = "") -> Path:
    """每位 AI 自己的自拍参考照（服务器 data/selfie/<id>.jpg）。

    没放就不带参考图——不再退回仓库里原作者那张「生图锚点」，免得画成别人的脸。
    """
    from public_overrides import SELFIE_DIR, selfie_reference
    return selfie_reference(source_identity) or (SELFIE_DIR / "__none__.jpg")


def _openai_image_source(data: dict) -> str | None:
    """兼容中转站 image_urls、结构化图片内容及 Markdown 图片链接。"""
    def extract(value):
        if isinstance(value, list):
            return next((source for item in value if (source := extract(item))), None)
        if isinstance(value, dict):
            for key in ("image_urls", "images", "image_url", "url", "content", "text"):
                source = extract(value.get(key))
                if source:
                    return source
        if isinstance(value, str):
            value = value.strip()
            if value.startswith(("https://", "http://", "data:image/")) and not re.search(r"\s", value):
                return value
            match = re.search(r"!\[[^\]]*\]\(\s*<?([^\s>]+?)>?(?:\s+\"[^\"]*\")?\s*\)", value)
            if match:
                return match.group(1)
        return None

    # OpenAI 图片接口：{"data":[{"b64_json":…}] 或 [{"url":…}]}
    for item in data.get("data") or [] if isinstance(data.get("data"), list) else []:
        if isinstance(item, dict) and item.get("b64_json"):
            return "data:image/png;base64," + str(item["b64_json"])
    choices = data.get("choices") or []
    return (extract(data.get("data")) or extract(choices[0].get("message", {}) if choices else {})
            or extract(data.get("image_urls")))


def _images_api_base(url: str, model: str) -> str:
    """走 OpenAI 图片接口时返回接口前缀（到 /v1），走聊天接口返回空串。"""
    clean = url.rstrip("/")
    for tail in ("/images/generations", "/images/edits"):
        if clean.endswith(tail):
            return clean[: -len(tail)]
    if clean.endswith("/chat/completions") or not re.search(r"gpt-image|dall-e", model, re.I):
        return ""
    return clean if clean.endswith("/v1") else clean + "/v1"


async def _post_openai_images(client, api_base: str, api_key: str, model: str, prompt: str, images: list[tuple[str, bytes, str]]):
    """有参考图走 edits（multipart），没有走 generations。"""
    headers = {"Authorization": f"Bearer {api_key}"}
    if images:
        files = [("image[]", item) for item in images]
        return await client.post(f"{api_base}/images/edits", headers=headers, files=files,
                                 data={"model": model, "prompt": prompt, "n": "1"})
    return await client.post(f"{api_base}/images/generations", headers=headers,
                             json={"model": model, "prompt": prompt, "n": 1})


async def _generate_openai_image(prompt: str, is_selfie: bool, source_identity: str,
                                 base_url: str, api_key: str, model: str, references=None) -> str | None:
    try:
        content = [{"type": "text", "text": prompt}]
        for ref in references or []:
            path = Path(ref['path'])
            mime = mimetypes.guess_type(path.name)[0] or 'image/jpeg'
            encoded = base64.b64encode(path.read_bytes()).decode('ascii')
            content.extend([{'type': 'text', 'text': f"下面的参考照片对应人物：{ref['name']}。保持此人的面部特征，不与其他角色混淆。"},
                            {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{encoded}'}}])
        reference_bytes = None
        if is_selfie:
            reference = _selfie_reference_path(source_identity)
            if reference.exists():
                reference_bytes = reference.read_bytes()
                encoded = base64.b64encode(reference_bytes).decode("ascii")
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
            else:
                print(f"[image_gen] 参考图不存在: {reference}，降级为 DRAW 模式")

        url = base_url.rstrip("/")
        api_base = _images_api_base(url, model)
        if not api_base and not url.endswith("/chat/completions"):
            url += "/chat/completions" if url.endswith("/v1") else "/v1/chat/completions"
        async with httpx.AsyncClient(timeout=IMAGE_GEN_TIMEOUT) as client:
            print(f"[image_gen] 使用自定义生图模型: {model}")
            if api_base:
                images = []
                for ref in references or []:
                    path = Path(ref['path'])
                    images.append((path.name, path.read_bytes(), mimetypes.guess_type(path.name)[0] or 'image/jpeg'))
                if reference_bytes is not None:
                    images.append(("selfie.jpg", reference_bytes, "image/jpeg"))
                response = await _post_openai_images(client, api_base, api_key, model, prompt, images)
            else:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": model, "stream": False, "messages": [{"role": "user", "content": content}]},
                )
            response.raise_for_status()
            source = _openai_image_source(response.json())
            if not source:
                _fail('OpenAI 线路没有返回图片，请确认填的是能生成图片的模型。')
                print("[image_gen] 自定义接口响应中未找到图片")
                return None
            if source.startswith("data:image/"):
                header, encoded = source.split(",", 1)
                if ";base64" not in header:
                    raise ValueError("图片数据不是 Base64 格式")
                image_bytes = base64.b64decode(encoded, validate=True)
            elif source.startswith(("https://", "http://")):
                # 图片通常位于独立 CDN，不向下载地址传递中转站的 Key。
                image_response = await client.get(source, follow_redirects=True)
                image_response.raise_for_status()
                image_bytes = image_response.content
            else:
                raise ValueError("不支持的图片地址格式")

        return await save_generated_image(image_bytes, prompt=prompt, model=model,
                                          actor=source_identity, kind="selfie" if is_selfie else "draw",
                                          reference_bytes=reference_bytes)
    except httpx.HTTPStatusError as exc:
        _fail(f'OpenAI 图片服务这次返回 {exc.response.status_code}，请检查图片 Key、模型名和可用额度。')
        print(f"[image_gen] 自定义生图请求失败（HTTP {exc.response.status_code}），未切换至 Gemini")
    except Exception as exc:
        # 不打印请求 URL、响应正文等可能包含凭据的内容。
        print(f"[image_gen] 自定义生图失败（{type(exc).__name__}），未切换至 Gemini")
    return None


def _code_model(source_identity: str) -> str:
    """代码画图用这位 AI 平时聊天的模型。"""
    from memory_hub_jobs import own_model
    return own_model(source_identity or 'aion')


async def _generate_code_image(prompt: str, is_selfie: bool, source_identity: str) -> str | None:
    """模型写画画清单，家里画成 PNG。清单只是数据，不执行。"""
    import image_code
    from ai_providers import CLI_STATUS_PREFIX, stream_ai
    from model_json import extract_json_object
    from contextlib import aclosing
    from text_only import TEXT_ONLY
    model_key = _code_model(source_identity)
    if not model_key:
        _fail('代码画图需要先在这位 AI 的名片里选好聊天模型。')
        return None
    meta: dict = {}
    text = ""
    token = TEXT_ONLY.set(True)
    try:
        ask = f"{image_code.instructions()}\n\n要画的内容：{prompt}"
        async with asyncio.timeout(IMAGE_GEN_TIMEOUT):
            async with aclosing(stream_ai([{"role": "user", "content": ask}], model_key, meta)) as chunks:
                async for chunk in chunks:
                    if isinstance(chunk, str) and not chunk.startswith(CLI_STATUS_PREFIX):
                        text += chunk
                        if len(text) > 180000:
                            _fail('代码画图的内容太长，这次已停止，请让 AI 画简单一点。')
                            return None
        if meta.get("provider_error"):
            _fail('这位 AI 的聊天模型这次报错，代码画图没能完成。')
            print("[image_gen] 代码画图：模型报错")
            return None
        data = await asyncio.to_thread(image_code.render, extract_json_object(text))
    except Exception as exc:
        _fail('代码画图这次没完成，模型需要给出完整的画画清单，请稍后重试。')
        print(f"[image_gen] 代码画图失败（{type(exc).__name__}）")
        return None
    finally:
        TEXT_ONLY.reset(token)
    if not data:
        _fail('AI 这次没有写出有效的画画清单，换个简单的画面再试。')
        print("[image_gen] 代码画图：模型没有给出能画的清单")
        return None
    return await save_generated_image(data, prompt=prompt, model=f"代码画图 · {model_key}", actor=source_identity,
                                      kind="selfie" if is_selfie else "draw")


async def generate_image(prompt: str, is_selfie: bool = False, source_identity: str = "", *, references=None) -> str | None:
    """
    调用设置中的生图模型，未配置时使用 Gemini；保存到相册并返回 uploads 相对路径。
    is_selfie=True 时自动附带参考图。
    失败返回 None。
    """
    _failure.set('')
    requested = re.match(r'^\s*(code|代码|banana|gemini|openai)\s*[|｜]\s*([\s\S]+)$', prompt, re.I)
    explicit = ''
    if requested:
        explicit = {'banana':'gemini', '代码':'code'}.get(requested[1].lower(), requested[1].lower())
        prompt = requested[2].strip()
    custom = [(SETTINGS.get(key) or "").strip() for key in
              ("image_gen_base_url", "image_gen_api_key", "image_gen_model")]
    provider = explicit or (SETTINGS.get("image_gen_provider") or "").strip()
    if provider == 'auto':
        provider = 'code'  # 旧模型没写方式前缀时的兼容；新提示词要求 AI 自己选可用方式并注明前缀。
    if provider in ('openai', 'gemini'):
        from image_profiles import resolve_profile
        try:
            profile = resolve_profile(SETTINGS,provider)
        except ValueError as error:
            _fail(str(error))
            return None
        custom = [profile[key] for key in ('base_url', 'api_key', 'model')]
    if provider == "code":
        return await _generate_code_image(prompt, is_selfie, source_identity)
    if provider != "gemini" and any(custom):
        if not all(custom):
            _fail('OpenAI 图片线路还没填完整：请在设置的「生图模型 → OpenAI」填写地址、图片 Key 和模型名。')
            print("[image_gen] 请完整填写生图 API 地址、Key 和模型名，或全部清空以使用 Gemini")
            return None
        if references:
            return await _generate_openai_image(prompt, is_selfie, source_identity, *custom, references=references)
        return await _generate_openai_image(prompt, is_selfie, source_identity, *custom)

    if provider == 'openai' and not any(custom):
        _fail('OpenAI 图片线路还没配置，请先在设置的「生图模型 → OpenAI」填写三项。')
        return None
    # Gemini（Banana）：选了这一路时，生图页填的 Key、模型、地址优先；地址留空就是谷歌官方
    gemini_base, own_key, gemini_model = custom if provider == "gemini" else ("", "", "")
    gemini_model = gemini_model or IMAGE_GEN_MODEL
    api_key = own_key or get_key("gemini")
    if not api_key:
        _fail('Banana 图片 Key 还没填。请在设置的「生图模型 → Banana」填写；代码画图不需要图片 Key。')
        print("[image_gen] 没有 Gemini API Key，无法生图")
        return None

    # 构建请求内容
    parts = [{"text": prompt}]
    for ref in references or []:
        path = Path(ref['path'])
        parts.extend([{'text': f"参考照片对应人物：{ref['name']}。保持面部特征。"},
                      {'inlineData': {'mimeType': mimetypes.guess_type(path.name)[0] or 'image/jpeg',
                                      'data': base64.b64encode(path.read_bytes()).decode('ascii')}}])

    # SELFIE 模式：附带参考图
    ref_bytes = None
    if is_selfie:
        reference_image_path = _selfie_reference_path(source_identity)
        if reference_image_path.exists():
            ref_bytes = reference_image_path.read_bytes()
            ref_b64 = base64.b64encode(ref_bytes).decode("utf-8")
            parts.append({
                "inlineData": {
                    "mimeType": "image/jpeg",
                    "data": ref_b64
                }
            })
            print(f"[image_gen] SELFIE 模式，已附带参考图: {reference_image_path}")
        else:
            print(f"[image_gen] 参考图不存在: {reference_image_path}，降级为 DRAW 模式")

    root = gemini_base.rstrip("/") or "https://generativelanguage.googleapis.com"
    if not root.endswith(("/v1beta", "/v1")):
        root += "/v1beta"
    url = f"{root}/models/{gemini_model}:generateContent"

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseModalities": ["IMAGE", "TEXT"],
        },
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=IMAGE_GEN_TIMEOUT) as client:
            print(f"[image_gen] 开始生图... prompt: {prompt[:80]}")
            resp = await client.post(url, json=payload, headers={'x-goog-api-key': api_key})
            resp.raise_for_status()
            data = resp.json()

            # 解析响应，提取图片
            candidates = data.get("candidates", [])
            if not candidates:
                error_msg = data.get("error", {}).get("message", "未知错误")
                print(f"[image_gen] API 返回空 candidates: {str(error_msg)[:200]}")
                return None

            content_parts = candidates[0].get("content", {}).get("parts", [])
            image_data = None

            for part in content_parts:
                inline = part.get("inlineData")
                if inline and inline.get("mimeType", "").startswith("image/"):
                    image_data = inline["data"]
                    break

            if not image_data:
                print("[image_gen] 响应中未找到图片数据")
                return None

            return await save_generated_image(base64.b64decode(image_data), prompt=prompt,
                                              model=gemini_model, actor=source_identity,
                                              kind="selfie" if is_selfie else "draw", reference_bytes=ref_bytes)

    except httpx.HTTPStatusError as e:
        _fail(f'Banana 图片服务这次返回 {e.response.status_code}，请检查图片 Key、模型名和可用额度。')
        print(f"[image_gen] API 请求失败 ({e.response.status_code})")  # 不打印正文：可能带地址和 Key
        return None
    except Exception as e:
        print(f"[image_gen] 生图异常: {type(e).__name__}")
        return None
