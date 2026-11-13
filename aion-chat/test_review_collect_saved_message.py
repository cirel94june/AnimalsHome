"""Independent review of the exact-source legacy collection CLI and dry run."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import html_works
from test_chat_artifacts import art


def test_review_cli_defaults_to_dryrun_then_collects_once_without_editing_chat(art, monkeypatch, capsys):
    _, path, _ = art
    spec = importlib.util.spec_from_file_location('review_collect_legacy', Path(__file__).resolve().parents[1] / 'tools/collect_legacy_chat_work.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with sqlite3.connect(path) as db:
        original = db.execute('SELECT * FROM chatroom_messages ORDER BY id').fetchall()
    args = ['collect_legacy_chat_work.py', '--kind', 'room', '--message-id', 'seat']
    monkeypatch.setattr(sys, 'argv', args)
    asyncio.run(module.main())
    dry = json.loads(capsys.readouterr().out)
    assert dry['dry_run'] is True and dry['actor'] == 'ai3'
    assert not html_works.DB_PATH.exists(), 'dry run must not create the works database'
    monkeypatch.setattr(sys, 'argv', args + ['--apply'])
    asyncio.run(module.main())
    one = json.loads(capsys.readouterr().out)
    asyncio.run(module.main())
    two = json.loads(capsys.readouterr().out)
    assert one['work_id'] == two['work_id']
    assert len(html_works.list_works()) == 1
    assert html_works.recent_count('ai3') == 0
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT * FROM chatroom_messages ORDER BY id').fetchall() == original
