import json
from pathlib import Path
import pytest
from codex_auth import prepare_auth_copy


def put(path,account,date,token='TEST'):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'auth_mode':'chatgpt','tokens':{'account_id':account,'access_token':token},'last_refresh':date}),encoding='utf-8')


def test_newer_same_account_login_beats_old_canonical_and_mtime(tmp_path):
    source,dest,peer=[tmp_path/x/'auth.json' for x in ['source','repair','chat']]
    put(peer,'A','2026-11-16T01:00:00Z','FRESH')
    put(source,'A','2026-10-22T01:00:00Z','STALE')
    prepare_auth_copy(source,dest,[peer])
    assert json.loads(dest.read_text())['tokens']['access_token']=='FRESH'


def test_current_fresh_login_is_not_overwritten(tmp_path):
    source,dest=tmp_path/'source.json',tmp_path/'dest.json'
    put(source,'A','2026-10-22T01:00:00Z');put(dest,'A','2026-11-16T01:00:00Z','KEEP')
    before=dest.read_bytes();prepare_auth_copy(source,dest)
    assert dest.read_bytes()==before


def test_explicit_account_change_does_not_borrow_old_account(tmp_path):
    source,dest,peer=[tmp_path/x/'auth.json' for x in ['source','repair','chat']]
    put(source,'NEW','2026-10-22T01:00:00Z','NEW_LOGIN')
    put(dest,'OLD','2026-11-16T01:00:00Z','OLD_LOGIN');put(peer,'OTHER','2027-01-01T01:00:00Z','OTHER_LOGIN')
    prepare_auth_copy(source,dest,[peer])
    assert json.loads(dest.read_text())['tokens']['access_token']=='NEW_LOGIN'


def test_missing_source_does_not_create_login_from_peer(tmp_path):
    source,dest,peer=[tmp_path/x/'auth.json' for x in ['source','repair','chat']]
    put(peer,'A','2026-11-16T01:00:00Z')
    prepare_auth_copy(source,dest,[peer]);assert not dest.exists()


def test_corrupt_optional_peer_is_ignored(tmp_path):
    source,dest,peer=[tmp_path/x for x in ['source.json','dest.json','peer.json']]
    put(source,'A','2026-11-16T01:00:00Z');peer.write_text('bad json')
    prepare_auth_copy(source,dest,[peer]);assert dest.read_bytes()==source.read_bytes()
