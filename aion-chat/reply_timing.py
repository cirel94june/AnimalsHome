"""Per-turn latency measurements; never record prompts, replies or credentials.

Stages may overlap or nest. Use their start offsets, not the sum of durations.
"""
import functools
import inspect
import json
import logging
import time


class ReplyTiming:
    def __init__(self):
        self.started = time.perf_counter()
        self.stages = []
        self.reported = set()

    def record(self, stage, started):
        self.stages.append({
            'stage': stage,
            'start_ms': round((started - self.started) * 1000, 2),
            'duration_ms': round((time.perf_counter() - started) * 1000, 2),
        })

    def report(self, phase, message_id, scope):
        key = (phase, message_id)
        if key in self.reported:
            return None
        self.reported.add(key)
        report = {
            'type': 'reply_timing', 'phase': phase, 'message_id': message_id,
            'generation_id': scope.id, 'surface': scope.surface,
            'elapsed_ms': round((time.perf_counter() - self.started) * 1000, 2),
            'stages': list(self.stages),
        }
        logging.getLogger('uvicorn.error').info('[reply-timing] %s', json.dumps(report))
        return report


def _current_trace():
    from generation_control import current_generation
    scope = current_generation()
    return scope.timing if scope else None


def timed(stage):
    def decorate(fn):
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def wrapped(*args, **kwargs):
                trace = _current_trace()
                started = time.perf_counter()
                try:
                    return await fn(*args, **kwargs)
                finally:
                    if trace is not None:
                        trace.record(stage, started)
        else:
            @functools.wraps(fn)
            def wrapped(*args, **kwargs):
                trace = _current_trace()
                started = time.perf_counter()
                try:
                    return fn(*args, **kwargs)
                finally:
                    if trace is not None:
                        trace.record(stage, started)
        return wrapped
    return decorate


def model_started():
    """Call when iteration actually starts, then mark the first non-status text."""
    trace = _current_trace()
    started = time.perf_counter()
    recorded = False

    def first_text(chunk):
        nonlocal recorded
        if trace is not None and chunk and not recorded:
            recorded = True
            trace.record('model_first_text', started)
    return first_text
