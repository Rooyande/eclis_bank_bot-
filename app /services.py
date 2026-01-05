import time
import uuid
import json
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ROOT_ADMINS
from app.db import engine, SessionLocal, Base
from app.models import (
    User, Account, Role, Ledger, RegisterCode,
    BANK_ACC_ID, COURT_ACC_ID
)

def now_ts() -> int:
    return int(time.time())


# ---------- DB INIT / BOOTSTRAP ----------
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        # bank account
        await _create_account_if_missing(
            session,
            acc_id=BANK_ACC_ID,
            acc_type="BANK",
            owner_tg_id=None,
            name="Central Bank",
            balance=0,
        )

        # court account
        await _create_account_if_missing(
            session,
            acc_id=COURT_ACC_ID,
            acc_type="COURT",
            owner_tg_id=None,
            name="Court",
            balance=0,
        )

        # root admins
        for tg_id in ROOT_ADMINS:
            await _grant_role_if_missing(session, tg_id=tg_id, role="ROOT", scope=None)

        await session.commit()


async def _create_account_if_missing(
    session: AsyncSession,
    acc_id: str,
    acc_type: str,
    owner_tg_id: int | None,
    name: str,
    balance: int = 0,
):
    row = await session.scalar(select(Account).where(Account.id == acc_id))
    if row:
        return

    session.add(Account(
        id=acc_id,
        type=acc_type,
        owner_tg_id=owner_tg_id,
        name=name,
        balance=balance,
        created_at=now_ts()
    ))


async def _grant_role_if_missing(session: AsyncSession, tg_id: int, role: str, scope: str | None):
    row = await session.scalar(
        select(Role).where(Role.tg_id == tg_id, Role.role == role, Role.scope.is_(scope))
    )
    if row:
        return

    session.add(Role(
        tg_id=tg_id,
        role=role,
        scope=scope,
        created_at=now_ts()
    ))


# ---------- USERS ----------
async def ensure_user(session: AsyncSession, tg_id: int, username: str | None):
    u = await session.scalar(select(User).where(User.tg_id == tg_id))
    if not u:
        session.add(User(
            tg_id=tg_id,
            username=username or "",
            created_at=now_ts()
        ))

    acc_id = f"U-{tg_id}"
    acc = await session.scalar(select(Account).where(Account.id == acc_id))
    if not acc:
        session.add(Account(
            id=acc_id,
            type="USER",
            owner_tg_id=tg_id,
            name=username or str(tg_id),
            balance=0,
            created_at=now_ts()
        ))

    return acc_id


# ---------- RBAC ----------
async def has_role(session: AsyncSession, tg_id: int, role: str, scope: str | None = None) -> bool:
    q = select(Role).where(Role.tg_id == tg_id, Role.role == role)
    if scope is None:
        q = q.where(Role.scope.is_(None))
    else:
        q = q.where(Role.scope == scope)
    r = await session.scalar(q)
    return r is not None


async def is_root(session: AsyncSession, tg_id: int) -> bool:
    return await has_role(session, tg_id, "ROOT", None)


async def is_bank_admin(session: AsyncSession, tg_id: int) -> bool:
    return await is_root(session, tg_id) or await has_role(session, tg_id, "BANK_ADMIN", None)


# ---------- BALANCE ----------
async def get_balance(session: AsyncSession, acc_id: str) -> int:
    acc = await session.scalar(select(Account).where(Account.id == acc_id))
    if not acc:
        raise ValueError("Account not found")
    return int(acc.balance)


# ---------- LEDGER / ATOMIC TRANSFER ----------
async def atomic_transfer(
    session: AsyncSession,
    actor_tg_id: int,
    from_acc: str,
    to_acc: str,
    amount: int,
    tx_type: str = "transfer",
    reason: str = "",
    meta: dict | None = None,
) -> str:
    if amount <= 0:
        raise ValueError("Amount must be > 0")
    if from_acc == to_acc:
        raise ValueError("from_acc and to_acc must be different")

    # lock both accounts (SELECT ... FOR UPDATE)
    from_row = await session.scalar(select(Account).where(Account.id == from_acc).with_for_update())
    to_row = await session.scalar(select(Account).where(Account.id == to_acc).with_for_update())

    if not from_row or not to_row:
        raise ValueError("Invalid account(s)")

    if int(from_row.balance) < amount:
        raise ValueError("Insufficient funds")

    from_row.balance -= amount
    to_row.balance += amount

    txid = str(uuid.uuid4())
    session.add(Ledger(
        txid=txid,
        actor_tg_id=actor_tg_id,
        from_acc=from_acc,
        to_acc=to_acc,
        amount=amount,
        tx_type=tx_type,
        status="ok",
        reason=reason,
        meta=json.dumps(meta or {}, ensure_ascii=False),
        created_at=now_ts()
    ))

    return txid


def format_receipt(
    txid: str,
    tx_type: str,
    from_acc: str,
    to_acc: str,
    amount: int,
    actor_tg_id: int,
    reason: str = "",
) -> str:
    return (
        "Receipt\n"
        f"TXID: {txid}\n"
        f"Type: {tx_type}\n"
        f"From: {from_acc}\n"
        f"To: {to_acc}\n"
        f"Amount: {amount}\n"
        f"Actor: {actor_tg_id}\n"
        f"Reason: {reason}\n"
        f"Time: {now_ts()}\n"
    )

