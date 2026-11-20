import json

import cache_window_hints as hints


def test_restart_hints_contain_only_hashes_and_keep_scopes_separate(tmp_path,monkeypatch):
    monkeypatch.setattr(hints,'PATH',tmp_path/'hints.json')
    monkeypatch.setattr(hints,'_HINTS',None)
    scope=('private-actor','private-conversation',30)
    start=('private','private-message',123.0)
    hints.remember(scope,start)
    saved=hints.PATH.read_text(encoding='utf-8')
    assert 'private' not in saved
    assert len(next(iter(json.loads(saved))))==64
    hints._HINTS=None
    assert hints.find(scope,[start])==start
    assert hints.find(('other',),[start]) is None
    assert hints.find(scope,[('private','deleted',124.0)]) is None


def test_missing_or_corrupt_hints_and_failed_writes_do_not_stop_chat(tmp_path,monkeypatch):
    monkeypatch.setattr(hints,'PATH',tmp_path/'hints.json')
    monkeypatch.setattr(hints,'_HINTS',None)
    hints.PATH.write_text('{broken',encoding='utf-8')
    assert hints.find(('scope',),[]) is None
    monkeypatch.setattr(hints.os,'replace',lambda *a:(_ for _ in ()).throw(OSError('synthetic')))
    hints.remember(('scope',),('source','id',123))
    assert hints.find(('scope',),[('source','id',123)])==('source','id',123)
    assert list(tmp_path.glob('.cache-window-*'))==[]
