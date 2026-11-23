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
        'cache_creation_reported_calls': 0, 'cache_creation_tokens': 0,
        'reasoning_reported_calls': 0, 'reasoning_tokens': 0,
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
    write_candidates = [raw.get(key) for key in (
        'cache_creation_input_tokens', 'cache_write_input_tokens') if key in raw]
    creation = raw.get('cache_creation')
    if isinstance(creation, dict):
        write_candidates.extend(creation.get(key) for key in (
            'ephemeral_5m_input_tokens', 'ephemeral_1h_input_tokens') if key in creation)
    for key in ('prompt_tokens_details', 'input_tokens_details'):
        detail = raw.get(key)
        if isinstance(detail, dict):
            write_candidates.extend(detail.get(child) for child in (
                'cache_creation_tokens', 'cache_write_tokens') if child in detail)
    write_reported = bool(write_candidates) and all(_number(value) is not None for value in write_candidates)
    read_reported = bool(candidates) and all(_number(value) is not None for value in candidates)
    if read_reported and read <= prompt:
        receipt['cache_reported_calls'] += 1
        receipt['cache_read_tokens'] += read
    if write_reported:
        receipt['cache_creation_reported_calls'] += 1
        receipt['cache_creation_tokens'] += write
    details = raw.get('completion_tokens_details') or {}
    reasoning = _number(details.get('reasoning_tokens')) if isinstance(details, dict) else None
    if reasoning is None:
        reasoning = _number(raw.get('reasoning_output_tokens'))
    if reasoning is not None:
        receipt['reasoning_reported_calls'] += 1
        receipt['reasoning_tokens'] += reasoning


def attachments(meta):
    receipt = (meta or {}).get('_reply_usage')
    if not receipt:
        return []
    # Explicit allowlist: no raw usage, model credentials, prompts or reasoning.
    keys = ('calls', 'reported_calls', 'cache_reported_calls', 'output_reported_calls', 'input_tokens',
            'output_tokens', 'cache_read_tokens', 'cache_creation_reported_calls',
            'cache_creation_tokens', 'reasoning_reported_calls', 'reasoning_tokens')
    item = {'type': 'reply_usage', **{key: receipt[key] for key in keys}}
    trace = (meta or {}).get('_cache_request_shape')
    if isinstance(trace, dict):
        allowed = (
            'provider', 'model', 'scope', 'conversation_id', 'route_id', 'context_limit', 'messages',
            'fixed_messages', 'fixed_bytes', 'fixed_id', 'request_bytes', 'approx_tokens',
            'same_fixed_prefix', 'shared_prefix_messages', 'marked_messages', 'options_id',
        )
        item['cache_debug'] = {key: trace[key] for key in allowed if key in trace}
    sources = (meta or {}).get('usage_sources')
    if isinstance(sources, dict):
        item['usage_sources'] = {
            str(key): (str(value) if value is not None else None)
            for key, value in sources.items()
        }
    return [item]
