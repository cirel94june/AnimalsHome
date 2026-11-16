"""Deterministic auth-file race probes using only synthetic local credentials."""
import builtins
import json
from pathlib import Path
import shutil

import pytest

import codex_auth


def put(path,account,stamp,token):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'auth_mode':'chatgpt','tokens':{'account_id':account,'access_token':token,'refresh_token':'REFRESH_'+token},'last_refresh':stamp}),encoding='utf-8')


def test_review_validated_peer_cannot_switch_accounts_before_copy(tmp_path,monkeypatch):
    source,dest,peer=[tmp_path/name/'auth.json' for name in ['source','dest','peer']]
    put(source,'ACCOUNT_A','2026-10-22T00:00:00Z','CANONICAL_A')
    put(peer,'ACCOUNT_A','2026-11-16T00:00:00Z','PEER_A')
    original_identity=codex_auth._identity
    def identity(value):
        selected_identity=original_identity(value)
        if isinstance(value.get('tokens'),dict) and value['tokens'].get('access_token')=='PEER_A':
            # Another CLI relogs this isolated home after the helper read its JSON.
            put(peer,'ACCOUNT_B','2026-11-17T00:00:00Z','OTHER_ACCOUNT_B')
        return selected_identity
    monkeypatch.setattr(codex_auth,'_identity',identity)
    codex_auth.prepare_auth_copy(source,dest,[peer])
    value=json.loads(dest.read_text(encoding='utf-8'))
    assert value['tokens']['account_id']=='ACCOUNT_A', 'copy must write the validated snapshot, not reread a changed path'


def test_review_destination_is_never_visible_as_truncated_auth(tmp_path,monkeypatch):
    # Production is Linux; disable Windows CopyFile2 so CPython's Linux-style
    # copyfile destination-open path is exercised deterministically on this PC.
    if hasattr(shutil,'_winapi'):
        monkeypatch.delattr(shutil._winapi,'CopyFile2',raising=False)
    source,dest=tmp_path/'canonical.json',tmp_path/'destination.json'
    put(source,'ACCOUNT_A','2026-11-16T00:00:00Z','NEW_A')
    put(dest,'ACCOUNT_A','2026-10-22T00:00:00Z','OLD_A')
    original_open=builtins.open
    observed=[]
    def watch_open(file,mode='r',*args,**kwargs):
        stream=original_open(file,mode,*args,**kwargs)
        if isinstance(file,(str,Path)) and Path(file).resolve()==dest.resolve() and 'w' in mode:
            # An independent reader can open the destination immediately after O_TRUNC.
            observed.append(dest.read_bytes())
        return stream
    monkeypatch.setattr(builtins,'open',watch_open)
    codex_auth.prepare_auth_copy(source,dest)
    assert all(payload and json.loads(payload)['tokens']['account_id']=='ACCOUNT_A' for payload in observed), 'destination auth must be atomically replaced, never truncated in place'
    assert json.loads(dest.read_text(encoding='utf-8'))['tokens']['access_token']=='NEW_A'


@pytest.mark.parametrize('access',[None,{},123])
def test_review_newer_malformed_peer_cannot_replace_valid_credentials(tmp_path,access):
    source,dest,peer=[tmp_path/name/'auth.json' for name in ['source','dest','peer']]
    put(source,'ACCOUNT_A','2026-10-22T00:00:00Z','VALID_A')
    put(peer,'ACCOUNT_A','2026-11-16T00:00:00Z','TEMP')
    malformed=json.loads(peer.read_text(encoding='utf-8'))
    malformed['tokens']['access_token']=access
    peer.write_text(json.dumps(malformed),encoding='utf-8')
    codex_auth.prepare_auth_copy(source,dest,[peer])
    assert json.loads(dest.read_text(encoding='utf-8'))['tokens']['access_token']=='VALID_A'
