"""Bounded, process-local diagnostics of actual relay payloads; no prompt storage."""
import hashlib
import json
import secrets

_SALT = secrets.token_bytes(32)
_FIELDS = (
    'prompt_tokens', 'completion_tokens', 'total_tokens', 'input_tokens', 'output_tokens',
    'cached_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens',
    'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens',
    'cachedContentTokenCount', 'promptTokenCount', 'candidatesTokenCount',
)
_DETAILS = {
    'prompt_tokens_details': ('cached_tokens',),
    'input_tokens_details': ('cached_tokens',),
    'cache_creation': ('ephemeral_5m_input_tokens', 'ephemeral_1h_input_tokens'),
}


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _id(value):
    return hashlib.sha256(_SALT + _encoded(value)).hexdigest()[:20]


def payload_shape(payload):
    """Called after all transforms/fallbacks, on the payload actually accepted by HTTP."""
    messages = payload.get('messages') or []
    # Only the constructor's explicit history header identifies a fixed prefix.
    # No header => unknown, rather than incorrectly declaring the whole input static.
    header = None
    marked = []
    for i, message in enumerate(messages):
        content = message.get('content')
        parts = content if isinstance(content, list) else []
        text = content if isinstance(content, str) else ''.join(
            p.get('text', '') for p in parts if isinstance(p, dict) and isinstance(p.get('text', ''), str))
        if header is None and text.startswith(('[历史记录说明]', '[聊天记录说明]')):
            header = i
        if any(isinstance(p, dict) and 'cache_control' in p for p in parts):
            marked.append(i)
    fixed = messages[:header] if header is not None else None
    return {
        'messages': len(messages), 'marked_messages': marked,
        'fixed_messages': header,
        'fixed_bytes': len(_encoded(fixed)) if fixed is not None else None,
        'fixed_id': _id(fixed) if fixed is not None else None,
        'message_ids': [_id(m) for m in messages[:200]],
        'message_ids_truncated': len(messages) > 200,
        'options_id': _id({k: v for k, v in payload.items() if k != 'messages'}),
    }


def numeric_usage(raw):
    """Keep recognized numeric counters only; never copy arbitrary upstream fields."""
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key in _FIELDS:
        value = raw.get(key)
        if type(value) is int and 0 <= value <= 10**12:
            result[key] = value
    for parent, keys in _DETAILS.items():
        child = raw.get(parent)
        if isinstance(child, dict):
            for key in keys:
                value = child.get(key)
                if type(value) is int and 0 <= value <= 10**12:
                    result[parent + '.' + key] = value
    return result
