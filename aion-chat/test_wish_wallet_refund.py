import importlib.util
from pathlib import Path
import sqlite3

import pytest

spec = importlib.util.spec_from_file_location('refund_wish_wallet', Path(__file__).resolve().parents[1] / 'tools/refund_wish_wallet.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def book(tmp_path):
    path = tmp_path / 'book.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE bookkeeping (id TEXT PRIMARY KEY,record_type TEXT,amount REAL,description TEXT,created_at REAL)')
        db.executemany('INSERT INTO bookkeeping VALUES (?,?,?,?,0)',[
            ('seed', 'wallet_user',40,'seed'), ('bad', 'wallet_ai',-20,'TEST_SEAT通过许愿池转账给用户 20元'),
            ('seat','seat_wallet_user:ai3',40,'seat seed')])
    return path


def test_dryrun_does_not_write_and_apply_refunds_only_once(book):
    assert module.refund(book,'bad','TEST_SEAT',20)['status'] == 'dry_run'
    with sqlite3.connect(book) as db: assert db.execute('SELECT COUNT(*) FROM bookkeeping').fetchone()[0] == 3
    assert module.refund(book,'bad','TEST_SEAT',20,True)['balance_after'] == 40
    assert module.refund(book,'bad','TEST_SEAT',20,True)['status'] == 'already_refunded'
    with sqlite3.connect(book) as db:
        assert db.execute('SELECT amount FROM bookkeeping WHERE id="bad"').fetchone()[0] == -20
        assert db.execute('SELECT amount FROM bookkeeping WHERE id="seat"').fetchone()[0] == 40
        assert db.execute('SELECT COUNT(*) FROM bookkeeping').fetchone()[0] == 4


@pytest.mark.parametrize('record,name,amount',[('seed','TEST_SEAT',20),('bad','WRONG',20),('bad','TEST_SEAT',10)])
def test_mismatched_record_is_never_changed(book,record,name,amount):
    with pytest.raises(ValueError): module.refund(book,record,name,amount,True)
    with sqlite3.connect(book) as db: assert db.execute('SELECT COUNT(*) FROM bookkeeping').fetchone()[0] == 3
