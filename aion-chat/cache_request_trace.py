"""Bounded, process-local diagnostics of actual relay payloads; no prompt storage."""
import hashlib
import json
import secrets
import threading

_SALT = secrets.token_bytes(32)
_LOCK = threading.Lock()
_PREVIOUS: dict[str, dict] = {}
_FIELDS = (
    'prompt_tokens', 'completion_tokens', 'total_tokens', 'input_tokens', 'output_tokens',
    'reasoning_output_tokens',
    'cached_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens',
    'cache_write_input_tokens',
    'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens',
    'cachedContentTokenCount', 'promptTokenCount', 'candidatesTokenCount',
)
_DETAILS = {
    'prompt_tokens_details': ('cached_tokens', 'cache_creation_tokens', 'cache_write_tokens'),
    'input_tokens_details': ('cached_tokens', 'cache_creation_tokens', 'cache_write_tokens'),
    'cache_creation': ('ephemeral_5m_input_tokens', 'ephemeral_1h_input_tokens'),
}


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _id(value):
    return hashlib.sha256(_SALT + _encoded(value)).hexdigest()[:20]


CONTEXT_SECTIONS = ("persona", "user_profile", "abilities", "memory", "live",
                    "history", "other")


def context_sections(messages, live_heading):
    """Measure pre-provider text by constructor category, never retain text.
    
    These are exact text character/UTF-8 byte counts, NOT token counts;
    provider wrappers, images, tool schemas and native thread history are excluded.
    Unknown formats stay in 'other'.
    """
    counts = {key: {"chars": 0, "bytes": 0} for key in CONTEXT_SECTIONS}
    in_history = False
    previous = "other"
    def add(key, text):
        counts[key]["chars"] += len(text)
        counts[key]["bytes"] += len(text.encode("utf-8", errors="replace"))
    for m in messages:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if isinstance(content, str):
            value = content
        elif isinstance(content, list):
            value = "".join(p["text"] for p in content if isinstance(p, dict)
                            and isinstance(p.get("text"), str))
        else:
            value = ""
        tag = m.get("_context")
        if tag == "history" or value.startswith(("[历史记录说明]", "[聊天记录说明]")):
            in_history = True
        if tag in ("memory", "memory_ack"):
            key = "memory"
        elif tag in ("live", "live_ack"):
            key = "live"
        elif tag in ("abilities", "persona", "user_profile"):
            key = tag
        elif in_history:
            key = "history"
        elif value.startswith(("[系统设定 - 你的角色设定]", "[你的角色设定]")) or (
                value.startswith("[系统设定 - ") and value.split("\n", 1)[0].endswith("人设]")):
            key = "persona"
        elif value.startswith("[") and value.split("\n", 1)[0].endswith("信息]"):
            key = "user_profile"
        elif m.get("role") == "system":
            # Arbitrary system messages need not be persona.
            key = "other"
        elif m.get("role") == "assistant" and previous in ("persona", "user_profile", "abilities"):
            key = previous
        else:
            key = "other"
        if key == "abilities" and live_heading and live_heading in value:
            cut = value.index(live_heading)
            add("abilities", value[:cut])
            add("live", value[cut:])
        else:
            add(key, value)
        previous = key
    return counts


def record_context_sections(meta, messages):
    if meta is None:
        return
    meta.pop("_context_sections", None)
    meta.pop("codex_prompt_chars", None)
    meta.pop("codex_prompt_bytes", None)
    try:
        from config import LIVE_STATE_HEADING
        meta["_context_sections"] = context_sections(messages, LIVE_STATE_HEADING)
    except Exception:
        pass


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
    encoded_messages = _encoded(messages)
    total_chars = sum(len(str(message.get('content') or '')) for message in messages)
    shape = {
        'messages': len(messages), 'marked_messages': marked,
        'fixed_messages': header,
        'fixed_bytes': len(_encoded(fixed)) if fixed is not None else None,
        'fixed_id': _id(fixed) if fixed is not None else None,
        'request_bytes': len(encoded_messages),
        'approx_tokens': max(0, round(total_chars / 3)),
        'message_ids': [_id(m) for m in messages[:200]],
        'message_ids_truncated': len(messages) > 200,
        'options_id': _id({k: v for k, v in payload.items() if k != 'messages'}),
    }
    return shape


def bind(meta, *, provider: str, model: str, scope: str, identifier: str, context_limit: int,
         route_identity: str = ""):
    """Attach non-content request identity before generation; IDs are salted hashes."""
    if meta is None:
        return
    try:
        meta['_cache_debug_context'] = {
            'provider': str(provider or ''),
            'model': str(model or ''),
            'scope': str(scope or ''),
            'conversation_id': _id(str(identifier or '')),
            'route_id': _id(str(route_identity or f'{provider}:{model}')),
            'context_limit': max(0, int(context_limit or 0)),
        }
    except Exception:
        pass


def seed(meta, *, scope: str, identifier: str, context_limit: int):
    """Keep route context only until the provider/model has been resolved."""
    if meta is not None:
        meta['_cache_debug_seed'] = {
            'scope': str(scope or ''), 'identifier': str(identifier or ''),
            'context_limit': max(0, int(context_limit or 0)),
        }


def bind_route(meta, *, provider: str, model: str, route_identity: str = ""):
    if meta is None:
        return
    seed_data = meta.pop('_cache_debug_seed', {})
    bind(
        meta,
        provider=provider,
        model=model,
        scope=seed_data.get('scope', 'unknown'),
        identifier=seed_data.get('identifier', ''),
        context_limit=seed_data.get('context_limit', 0),
        route_identity=route_identity,
    )


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


def start(meta, payload):
    """Diagnostics must never interrupt a successful model response."""
    if meta is None:
        return
    meta.pop('_cache_request_shape', None)
    try:
        shape = {**payload_shape(payload), 'usage': {}}
        context = dict(meta.get('_cache_debug_context') or {})
        route_key = _id({
            'provider': context.get('provider'), 'model': context.get('model'),
            'scope': context.get('scope'), 'conversation_id': context.get('conversation_id'),
            'route_id': context.get('route_id'),
        })
        with _LOCK:
            previous = _PREVIOUS.get(route_key)
            current_ids = shape.get('message_ids') or []
            previous_ids = (previous or {}).get('message_ids') or []
            shared = 0
            for before, now in zip(previous_ids, current_ids):
                if before != now:
                    break
                shared += 1
            shape['same_fixed_prefix'] = (
                previous is not None and previous.get('fixed_id') == shape.get('fixed_id')
                and shape.get('fixed_id') is not None
            )
            shape['shared_prefix_messages'] = shared
            _PREVIOUS[route_key] = {
                'fixed_id': shape.get('fixed_id'), 'message_ids': current_ids,
            }
            if len(_PREVIOUS) > 500:
                _PREVIOUS.pop(next(iter(_PREVIOUS)))
        meta['_cache_request_shape'] = {**context, **shape}
    except Exception:
        pass


def observe_usage(meta, raw):
    if meta is None or '_cache_request_shape' not in meta:
        return
    try:
        meta['_cache_request_shape']['usage'] = numeric_usage(raw)
    except Exception:
        pass
