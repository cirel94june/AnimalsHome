import context_builder


def row(n, sender='user', source='private'):
    return {'id': str(n), 'source': source, 'sender': sender,
            'content': f'synthetic-{n}', 'created_at': n + 1, 'attachments': []}


def test_appending_new_user_does_not_relabel_previous_user(monkeypatch):
    monkeypatch.setattr(context_builder, '_timeline_display_names', lambda: ('User', 'Aion', 'Connor'))
    old = [row(1), row(2, 'connor')]
    before = context_builder.render_merged_timeline(old, 'connor')
    after = context_builder.render_merged_timeline(old + [row(3)], 'connor')
    assert before == after[:len(before)]
    assert '最新一条用户发言' in before[0]['content']


def test_source_switch_stays_stable_when_window_becomes_mixed(monkeypatch):
    monkeypatch.setattr(context_builder, '_timeline_display_names', lambda: ('User', 'Aion', 'Connor'))
    old = [row(1), row(2, 'connor')]
    before = context_builder.render_merged_timeline(old, 'connor')
    after = context_builder.render_merged_timeline(old + [row(3, source='group')], 'connor')
    assert before == after[:len(before)]
    assert '以下切换到群聊场景' in after[-1]['content']
    assert '[群聊]' in after[-1]['content']
