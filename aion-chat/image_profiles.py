"""Separate, persistent configurations for the two picture-model protocols."""
from urllib.parse import urlsplit

FIELDS = ('base_url', 'api_key', 'model')


def profiles_from(settings):
    saved = settings.get('image_gen_profiles') or {}
    out = {provider: {key: str((saved.get(provider) or {}).get(key) or '').strip() for key in FIELDS}
           for provider in ('openai', 'gemini')}
    legacy = {key: str(settings.get('image_gen_' + key) or '').strip() for key in FIELDS}
    provider = 'gemini' if settings.get('image_gen_provider') == 'gemini' else 'openai'
    if provider not in saved and any(legacy.values()):
        out[provider] = legacy
    return out


def validate_profiles(value):
    if not isinstance(value, dict) or set(value) - {'openai', 'gemini'}:
        raise ValueError('图片配置只能包含 OpenAI 和 Banana 两种线路')
    clean = {}
    for provider in ('openai', 'gemini'):
        raw = value.get(provider) or {}
        if not isinstance(raw, dict): raise ValueError('图片线路格式不正确')
        config = {key: str(raw.get(key) or '').strip() for key in FIELDS}
        if any(len(config[k]) > limit for k, limit in [('base_url', 2048), ('api_key', 4096), ('model', 200)]):
            raise ValueError('图片线路字段太长')
        if provider == 'openai' and any(config.values()) and not all(config.values()):
            raise ValueError('OpenAI 图片线路需要地址、图片 Key 和模型名三项；不用时三项全留空')
        if config['base_url']:
            try:
                url = urlsplit(config['base_url'])
                valid = url.scheme in ('http', 'https') and url.hostname and not (url.username or url.password or url.query or url.fragment)
                _ = url.port
            except ValueError: valid = False
            if not valid: raise ValueError('图片 API 地址需为 HTTP(S)，不要包含账号、查询参数或片段')
        clean[provider] = config
    return clean
