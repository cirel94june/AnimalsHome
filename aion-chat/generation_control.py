"""Ownership and explicit cancellation for one user-triggered chat turn.

Disconnecting a page still allows a reply to finish. Only an explicit stop
cancels the owned task tree, including work spawned after the first reply.
"""
import asyncio
import contextvars
import functools
import inspect
import os
import time
import uuid
from collections import OrderedDict

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from reply_timing import ReplyTiming


_current = contextvars.ContextVar('chat_generation', default=None)
_active = {}
_stopped = OrderedDict()


def current_generation():
    return _current.get()


def _remember_stop(key):
    _stopped[key] = time.monotonic()
    _stopped.move_to_end(key)
    while _stopped and (len(_stopped) > 1024 or next(iter(_stopped.values())) < time.monotonic() - 600):
        _stopped.popitem(last=False)


class Generation:
    def __init__(self, surface, target, generation_id, save_partial=None):
        self.timing = ReplyTiming()
        self.surface, self.target, self.id = surface, target, generation_id
        self.key = (surface, target, generation_id)
        self.cancelled = self.key in _stopped and _stopped[self.key] > time.monotonic() - 600
        self.tasks = set()
        self.on_cancel = []
        self.streams = []
        self.partials = {}
        self.message_ids = set()
        self.queues = []
        self.save_partial = save_partial
        self.stop_task = None
        _active[self.key] = self

    def start(self, coroutine):
        token = _current.set(self)
        try:
            task = asyncio.create_task(coroutine)
        finally:
            _current.reset(token)
        self.tasks.add(task)
        task.add_done_callback(self._finished)
        if self.cancelled:
            task.cancel()
        return task

    def _finished(self, task):
        self.tasks.discard(task)
        if self.cancelled and self.stop_task is None:
            self.stop()  # An early stop may have arrived before this POST.
        if not self.tasks and not self.cancelled and _active.get(self.key) is self:
            _active.pop(self.key, None)

    def stop(self):
        if self.stop_task is not None:
            return self.stop_task
        self.cancelled = True
        _remember_stop(self.key)
        for cleanup in self.on_cancel:
            cleanup()
        for queue in self.queues:
            queue.stop()
        for task in list(self.tasks):
            if not task.done() and not task.cancelling():
                task.cancel()
        # Cleanup runs outside the cancelled turn: it may only save partial text.
        token = _current.set(None)
        try:
            self.stop_task = asyncio.create_task(self._finish_stop())
        finally:
            _current.reset(token)
        return self.stop_task

    async def _finish_stop(self):
        try:
            while self.tasks:
                await asyncio.gather(*list(self.tasks), return_exceptions=True)
                await asyncio.sleep(0)
            for stream in reversed(self.streams):
                await stream.aclose()
            messages = await self.save_partial(self) if self.save_partial else []
            return {'ok': True, 'stopped': True, 'generation_id': self.id,
                    'message_ids': list(self.message_ids), 'messages': messages}
        finally:
            if _active.get(self.key) is self:
                _active.pop(self.key, None)


def spawn_generation_task(coroutine):
    scope = current_generation()
    return scope.start(coroutine) if scope else asyncio.create_task(coroutine)


def own_stream(stream):
    scope = current_generation()
    if scope and hasattr(stream, 'aclose'):
        scope.streams.append(stream)
    return stream


async def terminate_process_tree(process):
    if process.returncode is not None:
        return
    if os.name == 'nt' and getattr(process, 'pid', None):
        killer = await asyncio.create_subprocess_exec(
            'taskkill', '/PID', str(process.pid), '/T', '/F',
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        await killer.wait()
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await process.wait()


def own_process(process):
    class ProcessCleanup:
        async def aclose(self):
            await terminate_process_tree(process)
    own_stream(ProcessCleanup())
    return process


def generation_status(surface, target, generation_id):
    scope = _active.get((surface, target, generation_id))
    return {'active': scope is not None, 'stopping': bool(scope and scope.cancelled),
            'message_ids': list(scope.message_ids) if scope else []}


def generation_event(data):
    scope = current_generation()
    if not scope:
        return data
    if scope.cancelled:
        raise asyncio.CancelledError()
    return {**data, 'generation_id': scope.id, 'generation_target': scope.target,
            'generation_surface': scope.surface}


class GenerationQueue(asyncio.Queue):
    """Remember only visible reply text; never replay commands during stop."""
    def __init__(self):
        super().__init__()
        self.scope = current_generation()
        self.speaker_ids = {}
        if self.scope:
            self.scope.queues.append(self)

    def put_nowait(self, item):
        scope = self.scope
        if scope:
            kind = item.get('type', '')
            if scope.cancelled:
                return  # stop() has already released the SSE reader.
            speaker = kind.split('_')[0] if kind.startswith(('aion_', 'connor_')) else 'assistant'
            if kind in ('start', 'aion_start', 'connor_start'):
                msg_id = item['id']
                self.speaker_ids[speaker] = msg_id
                scope.message_ids.add(msg_id)
                scope.partials[msg_id] = {'id': msg_id, 'sender': speaker, 'content': ''}
            msg_id = self.speaker_ids.get(speaker)
            if msg_id:
                partial = scope.partials[msg_id]
                if kind in ('chunk', 'aion_chunk', 'connor_chunk'):
                    partial['content'] += item.get('content', '')
                elif kind in ('replace', 'snapshot'):
                    partial['content'] = item.get('content', '')
                elif kind in ('stream_reset', 'aion_reset', 'connor_reset'):
                    partial['content'] = ''
                phase = None
                if kind in ('start', 'aion_start', 'connor_start'):
                    phase = 'context_ready'
                elif kind in ('chunk', 'aion_chunk', 'connor_chunk', 'replace', 'snapshot') and item.get('content'):
                    phase = 'first_visible_text'
                if phase:
                    report = scope.timing.report(phase, msg_id, scope)
                    if report:
                        super().put_nowait(report)
            item = {**item, 'generation_id': scope.id}
        super().put_nowait(item)

    def stop(self):
        while not self.empty():
            self.get_nowait()
        super().put_nowait({'type': 'stopped', 'generation_id': self.scope.id})
        super().put_nowait({'type': 'done'})


async def cancel_generation(surface, target, generation_id=None):
    if generation_id:
        key = (surface, target, generation_id)
        _remember_stop(key)  # Handles stop overtaking the original POST.
        scopes = [_active[key]] if key in _active else []
    else:
        scopes = [s for s in list(_active.values()) if s.surface == surface and s.target == target]
    results = []
    for scope in scopes:
        task = scope.stop()
        try:
            results.append(await asyncio.wait_for(asyncio.shield(task), 5))
        except TimeoutError:
            results.append({'ok': True, 'stopped': False, 'generation_id': scope.id,
                            'message_ids': list(scope.message_ids), 'messages': []})
    return {'ok': True, 'stopped': all(r['stopped'] for r in results),
            'generation_id': generation_id, 'message_ids': [m for r in results for m in r['message_ids']],
            'messages': [m for r in results for m in r['messages']]}


def cancellable(surface, save_partial):
    """Register before prompt preparation, without changing direct Python callers."""
    def decorate(function):
        signature = inspect.signature(function)

        @functools.wraps(function)
        async def wrapped(*args, **kwargs):
            request = kwargs.pop('_generation_request', None)
            if request is None:
                return await function(*args, **kwargs)
            target = (request.path_params.get('conv_id') or request.path_params.get('room_id')
                      or request.headers.get('X-Generation-Target', ''))
            if request.path_params.get('msg_id'):
                from database import get_db
                table, field = ('messages', 'conv_id') if surface == 'private' else ('chatroom_messages', 'room_id')
                async with get_db() as db:
                    cursor = await db.execute(f'SELECT {field} FROM {table} WHERE id=?', (request.path_params['msg_id'],))
                    row = await cursor.fetchone()
                if not row:
                    raise HTTPException(404, 'Message not found')
                target = row[0]
            generation_id = request.headers.get('X-Generation-Id') or uuid.uuid4().hex
            if not target or len(target) > 200 or len(generation_id) > 128:
                raise HTTPException(400, 'Invalid generation target or id')
            if (surface, target, generation_id) in _active:
                raise HTTPException(409, 'Generation already active')
            scope = Generation(surface, target, generation_id, save_partial)
            scope.expressive_autoplay = function.__name__ in {
                'send_message', 'edit_resend_message', 'regenerate_message',
                'edit_resend_chatroom_message', 'regenerate_chatroom_message', 'reply_once',
            }
            try:
                return await scope.start(function(*args, **kwargs))
            except asyncio.CancelledError:
                if not scope.cancelled:
                    raise
                return StreamingResponse(iter(['data: {"type":"stopped"}\n\n']), media_type='text/event-stream')

        wrapped.__signature__ = signature.replace(parameters=[*signature.parameters.values(),
            inspect.Parameter('_generation_request', inspect.Parameter.KEYWORD_ONLY, default=None, annotation=Request)])
        return wrapped
    return decorate
