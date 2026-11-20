"""Independent synthetic restart-hint failure and scope boundary probes."""
import json
import os

import cache_window_hints as hints
import context_builder


def isolate(tmp_path,monkeypatch):
    monkeypatch.setattr(hints,'PATH',tmp_path/'isolated-hints.json')
    monkeypatch.setattr(hints,'_HINTS',None)
    monkeypatch.setattr(context_builder,'_WINDOW_STARTS',{})


def test_deeply_corrupt_hint_file_cannot_break_chat(tmp_path,monkeypatch):
    isolate(tmp_path,monkeypatch)
    hints.PATH.write_text('['*2000+'0'+']'*2000,encoding='utf-8')
    rows=[{'source':'private','id':str(n),'created_at':n} for n in range(7)]
    result=context_builder._stable_window(rows,5,('synthetic',5))
    assert result==rows[-5:]


def test_restarted_hint_cannot_restore_unfetched_rows_or_cross_scope(tmp_path,monkeypatch):
    isolate(tmp_path,monkeypatch)
    rows=[{'source':'private','id':str(n),'created_at':n} for n in range(17)]
    scope=('seat','ai3','synthetic-chat',False,5)
    context_builder._stable_window(rows[:5],5,scope)
    context_builder._WINDOW_STARTS.clear()
    hints._HINTS=None
    # Saved head exists only in the old data; only 15 authorized rows are fetched.
    assert context_builder._stable_window(rows[2:],5,scope)==rows[-5:]
    context_builder._WINDOW_STARTS.clear()
    assert context_builder._stable_window(rows[2:9],5,('seat','ai4','synthetic-chat',False,5))==rows[4:9]


def test_hints_are_bounded_and_unchanged_head_does_not_rewrite(tmp_path,monkeypatch):
    isolate(tmp_path,monkeypatch)
    real_replace=hints.os.replace
    replaced=[]
    def replace(*args):
        replaced.append(args)
        return real_replace(*args)
    monkeypatch.setattr(hints.os,'replace',replace)
    for n in range(205):hints.remember(('synthetic',n),('private',str(n),n))
    assert len(json.loads(hints.PATH.read_text(encoding='utf-8')))==200
    before=len(replaced)
    hints.remember(('synthetic',204),('private','204',204))
    assert len(replaced)==before
    assert list(tmp_path.glob('.cache-window-*'))==[]
    if os.name!='nt':assert hints.PATH.stat().st_mode & 0o777==0o600
