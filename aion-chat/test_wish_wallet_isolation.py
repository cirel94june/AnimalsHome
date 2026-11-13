"""Real ledger regression for the wish-pool seat debit that used the main wallet."""
import asyncio
from unittest.mock import AsyncMock

import aiosqlite
import pytest

import autonomy
import routes.wallet as wallet
import routes.connor_wallet as connor_wallet
import routes.seat_wallet as seat_wallet


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / 'wallets.db'

    async def setup():
        async with aiosqlite.connect(path) as db:
            await db.execute('CREATE TABLE bookkeeping (id TEXT PRIMARY KEY, record_type TEXT, amount REAL, description TEXT, created_at REAL)')
            for typ in ['wallet_user', 'connor_wallet_user', 'seat_wallet_user:ai3', 'seat_wallet_user:ai4']:
                await db.execute('INSERT INTO bookkeeping VALUES (?,?,100,?,0)', (typ, typ, 'seed'))
            await db.commit()
    asyncio.run(setup())
    for module in [autonomy, wallet, connor_wallet, seat_wallet]:
        monkeypatch.setattr(module, 'get_db', lambda: aiosqlite.connect(path))
    monkeypatch.setattr(autonomy, '_actor_label', lambda actor: actor)
    monkeypatch.setattr(autonomy.manager, 'broadcast', AsyncMock())
    return path


@pytest.mark.parametrize('actor', ['aion', 'connor', 'ai3', 'ai4'])
def test_wish_debits_only_the_actual_actors_wallet(ledger, actor):
    async def run():
        assert await autonomy._execute_wish_transfer(actor, 20) == (True, '')
        return [await wallet._get_balance(), await connor_wallet._get_connor_balance(),
                await seat_wallet.get_seat_balance('ai3'), await seat_wallet.get_seat_balance('ai4')]
    expected = [100.0] * 4
    expected[['aion', 'connor', 'ai3', 'ai4'].index(actor)] = 80.0
    assert asyncio.run(run()) == expected


def test_empty_seat_cannot_borrow_the_main_wallet(ledger):
    async def run():
        text, balance = await autonomy._wish_wallet_ability('ai5', '小猫')
        assert balance == 0 and '0.00元' in text
        ok, reason = await autonomy._execute_wish_transfer('ai5', 20)
        assert not ok and '余额不足' in reason
        assert await wallet._get_balance() == 100
    asyncio.run(run())


@pytest.mark.parametrize('actor', ['aion', 'connor', 'ai3'])
def test_concurrent_wish_debits_cannot_overdraw(ledger, actor):
    async def run():
        results = await asyncio.gather(*(autonomy._execute_wish_transfer(actor, 60) for _ in range(2)))
        assert sorted(ok for ok, _ in results) == [False, True]
    asyncio.run(run())


def test_unknown_identity_never_defaults_to_main_wallet(ledger):
    assert asyncio.run(autonomy._execute_wish_transfer('unknown', 20))[0] is False
    with pytest.raises(ValueError):
        asyncio.run(autonomy._wish_wallet_ability('unknown', '小猫'))
