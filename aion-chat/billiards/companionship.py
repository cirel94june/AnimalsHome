"""Model calls are only for table talk. All game facts come from saved results."""
import asyncio
import json
import time
import uuid


def names():
    from chatroom import get_chatroom_names
    user, main, companion = get_chatroom_names()
    return dict(user=user, aion=main, connor=companion)


def facts(game, labels):
    state = game['state']
    participants = []
    for seat, actor in enumerate(game['players']):
        group = state['groups'][seat]
        remaining = [b['id'] for b in state['balls'] if not b.get('pocketed') and ((group == 'solids' and 1 <= b['id'] <= 7) or (group == 'stripes' and 9 <= b['id'] <= 15))]
        streak = game['streaks'][seat]
        participants.append({'name': labels[actor], 'actor': actor,
                             'group': {'solids': '全色', 'stripes': '花色'}.get(group, '未分组'),
                             'remaining': remaining if group else None,
                             'consecutive_pots': streak['pots'], 'consecutive_misses': streak['misses'],
                             'consecutive_losses': game['losses'][seat]})
    return {'rack': game['rack'], 'status': game['status'], 'score': game['score'],
            'players': participants, 'turn': labels[game['players'][state['turn']]],
            'phase': state['phase'], 'last_shot': state.get('lastShot'), 'result': state.get('result')}


def reaction_reason(game, reason, *, now=None):
    now = time.time() if now is None else now
    if reason in ('start', 'resume'):
        return '开局打个招呼' if reason == 'start' else '对方回到台球桌，接着刚才的球局'
    if game['state']['phase'] == 'over':
        return '本局分出胜负，聊聊真实结果和要不要再来一局'
    if now - game.get('last_speech_at', 0) < 35:
        return ''
    shot = game['state'].get('lastShot') or {}
    if shot.get('foul'):
        return '刚才发生犯规，可以按人设逗一句或安慰；不要讲课'
    if any(s['pots'] >= 3 or s['misses'] >= 3 for s in game['streaks']):
        return '有人连续进球或失误，按人设自然夸赞、调侃或接话'
    if len([b for b in shot.get('pots', []) if b not in (0, 8)]) >= 2:
        return '刚刚一杆进了多颗球'
    state = game['state']
    for group in state['groups']:
        if group and not any(not b.get('pocketed') and ((group == 'solids' and 1 <= b['id'] <= 7) or (group == 'stripes' and 9 <= b['id'] <= 15)) for b in state['balls']):
            return '有人已经清完本组，来到黑八阶段'
    return ''


async def model_text(actor, game, instruction):
    from autonomy import _actor_context, _call_actor
    labels = names()
    context = await _actor_context(actor, 24)
    context.append({'role': 'user', 'content': (
        '[当前场景：小家台球室]\n'
        f'你是{labels[actor]}，这局在场的是' + '、'.join(labels[p] for p in game['players']) + '。\n'
        '你和他们一起轻松打球，保持原本人设和关系。算法替你计算、击球，以下为真实已结算球况。'
        '只需自然聊天，不要计算角度力度、输出操作指令、调用工具或编造进球。'
        '剩球数是本局进度，score 是双方已赢局数。可以聊天、夸赞、调侃，不必每句讲球。\n'
        + json.dumps(facts(game, labels), ensure_ascii=False) + '\n'
        '最近桌边对话：\n' + '\n'.join(f"{labels.get(m['sender'], m['sender'])}：{m['text']}" for m in game['messages'][-12:])
        + '\n\n' + instruction
    )})
    return (await asyncio.wait_for(_call_actor(actor, context), 150)).strip()


async def append_message(service, sid, sender, text, *, rack=None):
    async with service.lock(sid):
        game = service.get(sid)
        if rack is not None and game['rack'] != rack:
            return None
        message = {'id': 'pool_' + uuid.uuid4().hex, 'sender': sender, 'text': text[:2000],
                   'rack': game['rack'], 'shot': game['state']['shotCount'], 'created_at': time.time()}
        game['messages'].append(message)
        game['messages'] = game['messages'][-200:]
        game['last_speech_at'] = time.time()
        game['chat_error'] = ''
        service.store.save(game)
    return message


async def chat(service, sid, text, target=''):
    if not text.strip():
        raise ValueError('先写点想说的话')
    game = service.get(sid)
    targets = [p for p in game['players'] if p != 'user']
    if target:
        if target not in targets:
            raise ValueError('这位伴侣不在球桌边')
        targets = [target]
    message = await append_message(service, sid, 'user', text)
    rack = message['rack']
    async with service.chat_locks.setdefault(sid, asyncio.Lock()):
        if service.get(sid)['rack'] != rack:
            return
        for actor in targets:
            try:
                reply = await model_text(actor, service.get(sid), '用户刚在桌边说话，请自然回应，通常一到三句。')
                if reply:
                    await append_message(service, sid, actor, reply, rack=rack)
            except Exception:
                async with service.lock(sid):
                    current = service.get(sid)
                    current['chat_error'] = '这次没能接上话，球局仍可以继续'
                    service.store.save(current)


async def react(service, sid, reason):
    try:
        lock = service.chat_locks.setdefault(sid, asyncio.Lock())
        if lock.locked():
            return
        async with lock:
            game = service.get(sid)
            trigger = reaction_reason(game, reason)
            if not trigger:
                return
            rack = game['rack']
            async with service.lock(sid):
                latest = service.get(sid)
                latest['last_speech_at'] = time.time()
                service.store.save(latest)
            ai_seats = [i for i, actor in enumerate(game['players']) if actor != 'user']
            seat = game['state']['shotCount'] % 2 if len(ai_seats) == 2 else ai_seats[0]
            actor = game['players'][seat]
            reply = await model_text(actor, game, f'接话机会：{trigger}。你可以主动说一两句；不想说则只返回 [SILENT]。')
            if reply and '[SILENT]' not in reply:
                await append_message(service, sid, actor, reply, rack=rack)
                if len(ai_seats) == 2:
                    other = game['players'][1-seat]
                    reply = await model_text(other, service.get(sid), '另一位刚在球桌边说话，想回应就简短接一句，否则返回 [SILENT]。')
                    if reply and '[SILENT]' not in reply:
                        await append_message(service, sid, other, reply, rack=rack)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f'[billiards] table talk: {type(exc).__name__}')


async def publish_result(service, sid):
    from .reports import last_active_window, save_result_card
    game = service.get(sid)
    labels = names()
    from autonomy import append_idle_event
    actor = next(p for p in game['players'] if p != 'user')
    async def mark(rack, **fields):
        async with service.lock(sid):
            current = service.get(sid)
            for saved in current.get('result_reports', []):
                if saved['rack'] == rack:
                    saved.update(fields)
            service.store.save(current)
    for result in game.get('result_reports', []):
        # Already published legacy results are not dumped into daily chat on upgrade.
        if not result.get('chat_published', result['published']):
            target = result.get('chat_target') or await last_active_window()
            if target:
                await mark(result['rack'], chat_target=target)
                if await save_result_card(game, result, target, labels):
                    await mark(result['rack'], chat_published=True)
        if not result['published']:
            winner = game['players'][result['winner']]
            detail = f"第{result['rack']}局，{labels[winner]}获胜；{result['reason']}。局数 {result['score'][0]}:{result['score'][1]}，共{result['shots']}杆。"
            await append_idle_event(actor, 'billiards_result', ' & '.join(labels[p] for p in game['players']) + '打完了一局台球', detail, target_type='billiards', target_id=sid, metadata={'billiards_id': sid, 'rack': result['rack']})
            await mark(result['rack'], published=True)


async def publish_result_safely(service, sid):
    try:
        await publish_result(service, sid)
    except Exception as exc:
        print(f'[billiards] result report: {type(exc).__name__}')


async def available_for_autonomy(actor):
    from .engine import available
    from .service import get_service
    from autonomy_state import get_actor_config
    other = 'connor' if actor == 'aion' else 'aion'
    cfg = await get_actor_config(other)
    if not available() or not cfg.get('enabled') or not cfg.get('actions', {}).get('billiards_play'):
        return False
    service = get_service()
    return not any(g['status'] != 'finished' and set(g['players']) & {actor, other} for g in service.store.all())


async def invite(actor):
    from .service import get_service
    from autonomy import _ask_actor_json, append_idle_event
    if not await available_for_autonomy(actor):
        return {'message': '暂时没有空闲的球桌搭档'}
    labels = names()
    other = 'connor' if actor == 'aion' else 'aion'
    decision = await _ask_actor_json(other, f'{labels[actor]}想邀请你在家里打一局休闲八球，算法负责击球。你可以答应或婉拒。只返回 JSON：{{"accept":true或false,"message":"自然的回应"}}')
    accepted = decision.get('accept') is True
    detail = str(decision.get('message') or ('好，来一局。' if accepted else '这会儿先不打。'))
    await append_idle_event(actor, 'billiards_invite', f'{labels[actor]}邀请{labels[other]}打台球', detail, metadata={'accepted': accepted})
    if not accepted:
        return {'message': detail, 'accepted': False}
    service = get_service()
    await service.start()
    game = await service.create([actor, other])
    await append_message(service, game['id'], other, detail)
    await service.resume(game['id'], '')
    return {'message': f"{labels[actor]}和{labels[other]}开始了一局台球", 'accepted': True, 'billiards_id': game['id']}
