"""Separate, persistent configurations for the two picture-model protocols."""
from urllib.parse import urlsplit

FIELDS = ('base_url', 'api_key', 'model')


def profiles_from(settings):
    saved = settings.get('image_gen_profiles') or {}
    out = {provider: {key: str((saved.get(provider) or {}).get(key) or '').strip() for key in FIELDS}
           for provider in ('openai', 'gemini')}
    for provider in out:
        if (saved.get(provider) or {}).get('route_id'):
            out[provider]['route_id'] = str(saved[provider]['route_id'])
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
        route_id = str(raw.get('route_id') or '').strip()
        if route_id:
            if len(route_id) > 200 or not config['model']:
                raise ValueError('请从中转站选择一个图片模型')
            config['route_id'] = route_id
        if any(len(config[k]) > limit for k, limit in [('base_url', 2048), ('api_key', 4096), ('model', 200)]):
            raise ValueError('图片线路字段太长')
        if provider == 'openai' and not route_id and any(config.values()) and not all(config.values()):
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


def resolve_profile(settings, provider):
    """只在服务端读取绑定线路的凭据；线路消失就报错，不借用别的 Key。"""
    profile = profiles_from(settings)[provider]
    if not profile.get('route_id'):
        return profile
    route = next((item for item in settings.get('custom_model_routes') or []
                  if item.get('id') == profile['route_id']), None)
    if not route:
        raise ValueError('图片绑定的中转站已不存在，请重新选择')
    base = str(route.get('base_url') or '').strip().rstrip('/')
    key = str(route.get('api_key') or '').strip()
    if not base or not key:
        raise ValueError('图片绑定的中转站缺少地址或 Key，请在原中转站里补齐')
    if provider == 'gemini':
        if base.endswith('/chat/completions'): base = base[:-len('/chat/completions')]
        if base.endswith('/v1'): base = base[:-3]
    return {**profile, 'base_url':base, 'api_key':key}


def available_modes(settings):
    modes = ['code']
    for provider in ('gemini','openai'):
        try: profile = resolve_profile(settings,provider)
        except ValueError: continue
        if provider == 'gemini' and (profile['api_key'] or settings.get('gemini_key')):
            modes.append('banana')
        elif provider == 'openai' and all(profile.get(key) for key in FIELDS):
            modes.append('openai')
    return modes
