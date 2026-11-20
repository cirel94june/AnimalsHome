"""Small, numeric usage receipt for one reply; never store provider payloads."""
import math

import usage_log


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0 or value > 9007199254740991 or not math.isfinite(value) or value != int(value):
        return None
    return int(value)


def observe(meta, reported):
    receipt = meta.setdefault('_reply_usage', {
        'calls': 0, 'reported_calls': 0, 'cache_reported_calls': 0, 'output_reported_calls': 0,
        'input_tokens': 0, 'output_tokens': 0, 'cache_read_tokens': 0,
    })
    receipt['calls'] += 1 + (_number(meta.pop('_reply_extra_calls', 0)) or 0)
    if not reported:
        return
    prompt = _number(meta.get('prompt_tokens'))
    if prompt is None:
        return
    raw = meta.get('raw')
    raw = raw if isinstance(raw, dict) else {}
    read, write = usage_log.cache_tokens(raw)
    if usage_log._input_excludes_cache(raw):
        if any(_number(raw.get(key)) is None for key in (
                'cache_read_input_tokens', 'cache_creation_input_tokens')):
            return  # Without both fields Anthropic's total input is unknown.
        prompt += read + write
    receipt['reported_calls'] += 1
    receipt['input_tokens'] += prompt
    output = _number(meta.get('completion_tokens'))
    if output is not None:
        receipt['output_reported_calls'] += 1
        receipt['output_tokens'] += output
    candidates = [raw.get(key) for key in (
        'cache_read_input_tokens', 'cachedContentTokenCount', 'cached_input_tokens',
        'cached_tokens', 'prompt_cache_hit_tokens') if key in raw]
    for key in ('prompt_tokens_details', 'input_tokens_details'):
        detail = raw.get(key)
        if isinstance(detail, dict) and 'cached_tokens' in detail:
            candidates.append(detail['cached_tokens'])
    if candidates and all(_number(value) is not None for value in candidates) and read <= prompt:
        receipt['cache_reported_calls'] += 1
        receipt['cache_read_tokens'] += read


def attachments(meta):
    receipt = (meta or {}).get('_reply_usage')
    if not receipt:
        return []
    # Explicit allowlist: no raw usage, model credentials, prompts or reasoning.
    keys = ('calls', 'reported_calls', 'cache_reported_calls', 'output_reported_calls', 'input_tokens',
            'output_tokens', 'cache_read_tokens')
    return [{'type': 'reply_usage', **{key: receipt[key] for key in keys}}]
