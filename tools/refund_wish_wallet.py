"""Refund one confirmed wish-pool debit from the wrong wallet; dry-run by default.

Keeps the original ledger row. Requires its exact id, expected amount, and original
seat name; a deterministic refund id prevents a second refund. Run as the app user.
"""
import argparse
import json
import math
import sqlite3
import time


def refund(db_path, record_id, actor_name, amount, apply=False):
    if not actor_name.strip() or not math.isfinite(amount) or amount <= 0:
        raise ValueError('需要明确原转账者和正金额')
    con = sqlite3.connect(db_path, timeout=10)
    try:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT record_type,amount,description FROM bookkeeping WHERE id=?', (record_id,)).fetchone()
        if not row or row[0] != 'wallet_ai' or round(row[1]*100) != -round(amount*100) or not row[2].startswith(actor_name+'通过许愿池转账给'):
            raise ValueError('原记录与已核实的误扣不一致，未修改')
        refund_id = 'refund_wish_' + record_id
        previous = con.execute('SELECT record_type,amount FROM bookkeeping WHERE id=?', (refund_id,)).fetchone()
        if previous:
            if previous != ('wallet_user', amount): raise ValueError('纠错记录不一致')
            con.rollback()
            return {'status': 'already_refunded', 'amount': amount}
        before = con.execute("SELECT COALESCE(SUM(amount),0) FROM bookkeeping WHERE record_type IN ('wallet_user','wallet_ai')").fetchone()[0]
        if apply:
            con.execute('INSERT INTO bookkeeping (id,record_type,amount,description,created_at) VALUES (?,?,?,?,?)',
                        (refund_id, 'wallet_user', amount, f'系统纠错：退回许愿池误扣的{amount:g}元（原转账者：{actor_name}；原记录：{record_id}）', time.time()))
            con.commit()
        else: con.rollback()
        return {'status': 'refunded' if apply else 'dry_run', 'amount': amount, 'balance_before': before,
                'balance_after': round(before+amount,2), 'other_wallets_changed': False}
    finally: con.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--record-id', required=True)
    parser.add_argument('--actor-name', required=True)
    parser.add_argument('--amount', required=True, type=float)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(refund(args.db, args.record_id, args.actor_name, args.amount, args.apply),ensure_ascii=False))
