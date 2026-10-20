"""
座位 3～6（狗蛋等）的钱包：每位各自一本账，和小克、Lucien 的钱包互不相干。
账记在 bookkeeping 表里，record_type 是 seat_wallet_user:<座位>（小猫转进来 / 扣除）和
seat_wallet_ai:<座位>（TA 转给小猫，金额为负）。
"""

import time

from fastapi import APIRouter, HTTPException

from database import get_db

router = APIRouter()

SEATS = ("ai3", "ai4", "ai5", "ai6")


def _types(actor: str) -> tuple[str, str]:
    return f"seat_wallet_user:{actor}", f"seat_wallet_ai:{actor}"


def _check(actor: str) -> str:
    if actor not in SEATS:
        raise HTTPException(404, "没有这个座位")
    return actor


async def _balance(db, actor: str) -> float:
    cur = await db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM bookkeeping WHERE record_type IN (?, ?)", _types(actor))
    row = await cur.fetchone()
    return float(row[0] or 0)


async def get_seat_balance(actor: str) -> float:
    async with get_db() as db:
        return await _balance(db, actor)


async def _broadcast(actor: str) -> None:
    from ws import manager
    await manager.broadcast({"type": "seat_wallet_update", "data": {"actor": actor}})


async def credit_from_user(actor: str, amount: float, description: str) -> None:
    """小猫给 TA 转账（负数是扣除，和小克 / Lucien 钱包一致）。"""
    now = time.time()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO bookkeeping (id, record_type, amount, description, created_at) VALUES (?,?,?,?,?)",
            (f"swt_{time.time_ns()}", _types(actor)[0], amount, description, now))
        await db.commit()
    await _broadcast(actor)


async def pay_user(actor: str, amount: float, description: str) -> bool:
    """TA 给小猫转账：余额够才扣，查余额和扣款在同一个写事务里；不够返回 False。"""
    if amount <= 0:
        return False
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        if await _balance(db, actor) < amount:
            await db.commit()
            return False
        await db.execute(
            "INSERT INTO bookkeeping (id, record_type, amount, description, created_at) VALUES (?,?,?,?,?)",
            (f"swt_{time.time_ns()}", _types(actor)[1], -amount, description, time.time()))
        await db.commit()
    await _broadcast(actor)
    return True


@router.get("/api/seat-wallet/{actor}/balance")
async def seat_balance(actor: str):
    return {"balance": await get_seat_balance(_check(actor))}


@router.get("/api/seat-wallet/{actor}/transactions")
async def seat_transactions(actor: str, limit: int = 50, offset: int = 0):
    _check(actor)
    limit = max(1, min(int(limit), 200))
    async with get_db() as db:
        db.row_factory = __import__("aiosqlite").Row
        cur = await db.execute(
            "SELECT * FROM bookkeeping WHERE record_type IN (?, ?) ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*_types(actor), limit, max(0, int(offset))))
        return [dict(r) for r in await cur.fetchall()]
