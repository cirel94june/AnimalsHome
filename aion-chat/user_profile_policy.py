"""Lifecycle and bounded presentation of the shared profile (no model or I/O)."""
from datetime import datetime
import math
import re
import time

CATEGORIES = {'profile': '长期画像', 'recent': '近期重点与待办'}
KINDS = {'context': '普通近况', 'project': '项目', 'task': '待办'}
STATUSES = {'active': '当前关注', 'stale': '久未确认', 'unknown': '结果未确认',
            'done': '已完成', 'cancelled': '已取消', 'retired': '已淡出'}
PHASES = {'planned': '计划', 'ongoing': '进行中', 'finished': '已结束', 'unknown': '未确认'}
PROMPT_LIMIT = 1200
DAY = 86400
LEGACY_CATEGORIES = {'basic', 'habits', 'preferences', 'companionship', 'agreements'}
SUBJECT_RE = re.compile(r'(?<!其他)(?<!其它)(?<!每个)(?<!每位)(?<!所有)(?<!多)用户'
                        r'(?!画像|体验|界面|账号|账户|权限|认证|登录|注册|管理|数据|设置|配置|消息|名称|姓名)')


def personalize_text(text, user_name):
    """Name the profile subject while preserving product terms and other people."""
    name = user_name.strip() or '你'
    # Preserve an already configured name even if it contains the word 用户.
    return name.join(SUBJECT_RE.sub(lambda _: name, part) for part in text.split(name))


def personalize_document(document, user_name):
    """Cosmetic cleanup only: do not advance confirmation times or change locks."""
    count = 0
    for entry in document['entries'].values():
        if entry.get('deleted'):
            continue
        changed = False
        for field in ('content', 'topic'):
            text = entry.get(field, '')
            renamed = personalize_text(text, user_name)
            if renamed != text:
                entry[field] = renamed
                changed = True
        count += changed
    state = document.get('current_state')
    state_changed = False
    if state:
        renamed = personalize_text(state['text'], user_name)
        state_changed = renamed != state['text']
        state['text'] = renamed
    return count, state_changed


def remove_manual_protection(document):
    """Keep deletion barriers, but every visible fact and activity can evolve."""
    changed = False
    values = [e for e in document['entries'].values() if not e.get('deleted')]
    if document.get('current_state'):
        values.append(document['current_state'])
    for value in values:
        if value.get('locked'):
            value['locked'] = False
            changed = True
    return changed


def unfinished_task(entry):
    return entry.get('kind') == 'task' and entry.get('status', 'active') not in ('done', 'cancelled', 'retired')


def category(value):
    if value in LEGACY_CATEGORIES:
        return 'profile'
    if value not in CATEGORIES:
        raise ValueError('画像分类无效')
    return value


def timestamp(value):
    if value in (None, ''):
        return None
    try:
        result = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else datetime.fromisoformat(value).timestamp()
    except (ValueError, TypeError, OverflowError):
        raise ValueError('时间请使用有效日期或时间戳') from None
    if not math.isfinite(result):
        raise ValueError('时间无效')
    return result


def normalize(entry):
    result = dict(entry)
    result['category'] = category(entry.get('category'))
    result.setdefault('topic', '')
    result.setdefault('kind', 'context')
    result.setdefault('status', 'active')
    result.setdefault('sustained', False)
    return result


def lifecycle(entry, now):
    status = entry.get('status', 'active')
    if entry.get('deleted'):
        return 'retired'
    if status in ('done', 'cancelled', 'retired'):
        return status
    if entry.get('category') == 'recent' and entry.get('kind') != 'task' and entry.get('expires_at') and now >= entry['expires_at']:
        return 'retired'
    if entry.get('category') != 'recent':
        return status
    age = max(0, now - entry.get('source_ts', now))
    kind = entry.get('kind', 'context')
    if kind == 'task':
        # Passing a due time does not establish completion or cancellation.
        due = entry.get('due_at')
        return 'unknown' if (due and due < now) or age >= 30*DAY else status
    if kind == 'project':
        warm, cold = (60, 180) if entry.get('sustained') else (14, 30)
        return 'retired' if age >= cold*DAY else 'stale' if age >= warm*DAY else status
    return 'retired' if now >= (entry.get('expires_at') or entry.get('source_ts', now) + 3*DAY) else status


def maintain(document, now=None):
    now = time.time() if now is None else now
    changed = False
    for key, entry in list(document['entries'].items()):
        if entry.get('deleted'):
            continue
        status = lifecycle(entry, now)
        if status != entry.get('status', 'active'):
            entry['status'] = status
            entry['retired_at'] = now if status == 'retired' else entry.get('retired_at')
            changed = True
        # Source chat remains the history. Do not grow a second archival fact store.
        retired_at = entry.get('retired_at') or entry.get('updated_at', now)
        if status in ('retired', 'done', 'cancelled') and now-retired_at >= 30*DAY:
            del document['entries'][key]
            changed = True
    return changed


def ranked(entries, now):
    active = [e for e in entries if not e.get('deleted') and lifecycle(e, now) not in ('done', 'cancelled', 'retired')]
    return sorted(active, key=lambda e: (lifecycle(e, now) == 'stale', e.get('kind') != 'task',
                                       e.get('due_at') or e.get('available_at') or float('inf'),
                                       -e.get('source_ts', 0)))


def fact_line(entry, now):
    status = lifecycle(entry, now)
    tag = entry.get('topic', '')
    if entry['category'] == 'recent':
        ts = entry.get('source_ts', 0)
        tag = '/'.join(filter(None, (tag, datetime.fromtimestamp(ts).strftime('%m-%d') if ts else '时间未确认',
                                   '结果未确认' if status == 'unknown' else '久未确认' if status == 'stale' else '')))
    times = []
    for field, label in (('available_at', '可开始'), ('due_at', '截止')):
        if entry.get(field):
            times.append(label + datetime.fromtimestamp(entry[field]).strftime('%m-%d %H:%M'))
    suffix = ('（' + '；'.join(times) + '）') if times else ''
    return '- ' + (tag + '：' if tag else '') + entry['content'] + suffix


def render_context(document, user_name='你', now=None):
    """Return the exact <=1200-character prompt and the entries it includes."""
    now = time.time() if now is None else now
    all_entries = list(document['entries'].values())
    items = ranked(all_entries, now)
    state = document.get('current_state')
    live_state = state and state.get('expires_at', 0) > now and state.get('phase') != 'finished'
    if not items and not live_state:
        return '', set()
    prefix = f'【{user_name[:40]}的共享画像】\n资料是背景，本轮表达优先。结果未确认不等于仍在做；久未确认不主动提起，不反复追问旧事项。\n'
    chunks = {'profile': [], 'recent': []}
    selected = set()
    state_text = ''
    if live_state:
        when = datetime.fromtimestamp(state['event_at']).strftime('%m-%d %H:%M')
        state_text = f'【当前状态】{when} {PHASES[state["phase"]]}：{state["text"]}\n'
        # Activity text is deliberately short; prevent arbitrary manual text taking the whole prompt.
        state_text = state_text[:100]
    reserve = len(prefix) + len(state_text) + len('【长期画像】\n【近期重点与待办】\n')
    limits = {'profile': 500, 'recent': 600}
    available = PROMPT_LIMIT-reserve
    deferred = []
    used = {'profile': 0, 'recent': 0}
    for entry in items:
        cat = category(entry['category'])
        line = fact_line(entry, now) + '\n'
        if (used[cat] + len(line) <= limits[cat] or entry.get('kind') == 'task') and len(line) <= available:
            chunks[cat].append(line)
            selected.add(entry['key'])
            used[cat] += len(line)
            available -= len(line)
        else:
            deferred.append((entry, cat, line))
    # Transfer spare space across sections, keeping full facts instead of chopping deadlines.
    for entry, cat, line in deferred:
        if len(line) <= available:
            chunks[cat].append(line)
            selected.add(entry['key'])
            available -= len(line)
    result = prefix
    for cat, title in CATEGORIES.items():
        if chunks[cat]:
            result += f'【{title}】\n' + ''.join(chunks[cat])
    result += state_text
    return result.rstrip(), selected
