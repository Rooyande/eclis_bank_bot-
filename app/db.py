from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Tuple

import aiosqlite

from app.config import settings


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def _ensure_data_dir():
    db_path = settings.DB_PATH
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)


@dataclass(frozen=True)
class Account:
    id: int
    owner_tg_id: int
    kind: str           # "personal" | "business" | ...
    label: str          # display name like "Personal", "Shop #1"
    is_active: int      # 0/1
    created_at: str


async def init_db() -> None:
    """
    Creates DB schema if not exists.
    Source of truth is ledger table (transactions). Balances can be computed later.
    """
    await _ensure_data_dir()

    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA foreign_keys=ON;")

        # Owners: 1 telegram user -> can own multiple accounts
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS owners (
                tg_user_id INTEGER PRIMARY KEY,
                active_account_id INTEGER,
                created_at TEXT NOT NULL
            );
            """
        )

        # Accounts: multi per owner
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_tg_id INTEGER NOT NULL,
                kind TEXT NOT NULL,           -- personal / business / ...
                label TEXT NOT NULL,          -- user friendly label
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,

                FOREIGN KEY (owner_tg_id) REFERENCES owners(tg_user_id) ON DELETE CASCADE
            );
            """
        )

        await db.execute("CREATE INDEX IF NOT EXISTS idx_accounts_owner ON accounts(owner_tg_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_accounts_owner_kind ON accounts(owner_tg_id, kind);")

        # Admin roles (owner/admin control will be added later)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS admins (
                tg_user_id INTEGER PRIMARY KEY,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            """
        )

        # Transactions ledger (receipt_no is numeric string, unique)
        # from_account_id/to_account_id nullable to support pool/system transactions later.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_no TEXT NOT NULL UNIQUE,
                ts_utc TEXT NOT NULL,

                from_account_id INTEGER,
                to_account_id INTEGER,

                amount INTEGER NOT NULL CHECK(amount > 0),
                status TEXT NOT NULL,           -- PENDING / SUCCESS / FAILED / FORCED
                description TEXT,

                created_by_tg_id INTEGER NOT NULL, -- who initiated (user/admin)
                forced INTEGER NOT NULL DEFAULT 0,

                FOREIGN KEY (from_account_id) REFERENCES accounts(id) ON DELETE SET NULL,
                FOREIGN KEY (to_account_id) REFERENCES accounts(id) ON DELETE SET NULL
            );
            """
        )

        await db.execute("CREATE INDEX IF NOT EXISTS idx_tx_ts ON transactions(ts_utc);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tx_from ON transactions(from_account_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tx_to ON transactions(to_account_id);")

        await db.commit()


async def get_or_create_owner(tg_user_id: int) -> None:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON;")
        cur = await db.execute("SELECT tg_user_id FROM owners WHERE tg_user_id = ?;", (tg_user_id,))
        row = await cur.fetchone()
        if row:
            return

        await db.execute(
            "INSERT INTO owners(tg_user_id, active_account_id, created_at) VALUES (?, NULL, ?);",
            (tg_user_id, _utc_now_iso()),
        )
        await db.commit()


async def create_account(tg_user_id: int, kind: str, label: str, set_active: bool = True) -> int:
    """
    Creates an account for owner and optionally makes it active.
    Returns new account_id.
    """
    kind = kind.strip().lower()
    if not kind:
        raise ValueError("kind is required")

    label = label.strip()
    if not label:
        raise ValueError("label is required")

    await get_or_create_owner(tg_user_id)

    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON;")
        cur = await db.execute(
            """
            INSERT INTO accounts(owner_tg_id, kind, label, is_active, created_at)
            VALUES (?, ?, ?, 1, ?);
            """,
            (tg_user_id, kind, label, _utc_now_iso()),
        )
        account_id = cur.lastrowid

        if set_active:
            await db.execute(
                "UPDATE owners SET active_account_id = ? WHERE tg_user_id = ?;",
                (account_id, tg_user_id),
            )

        await db.commit()
        return int(account_id)


async def list_accounts(tg_user_id: int) -> Tuple[Optional[int], List[Account]]:
    """
    Returns (active_account_id, accounts[])
    """
    await get_or_create_owner(tg_user_id)

    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON;")

        cur = await db.execute("SELECT active_account_id FROM owners WHERE tg_user_id = ?;", (tg_user_id,))
        row = await cur.fetchone()
        active_id = row[0] if row else None

        cur = await db.execute(
            """
            SELECT id, owner_tg_id, kind, label, is_active, created_at
            FROM accounts
            WHERE owner_tg_id = ?
            ORDER BY id ASC;
            """,
            (tg_user_id,),
        )
        rows = await cur.fetchall()

        accounts = [
            Account(
                id=r[0],
                owner_tg_id=r[1],
                kind=r[2],
                label=r[3],
                is_active=r[4],
                created_at=r[5],
            )
            for r in rows
        ]
        return active_id, accounts


async def set_active_account(tg_user_id: int, account_id: int) -> None:
    """
    Sets active account if it belongs to the user and is_active=1
    """
    await get_or_create_owner(tg_user_id)

    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON;")

        cur = await db.execute(
            """
            SELECT id FROM accounts
            WHERE id = ? AND owner_tg_id = ? AND is_active = 1
            LIMIT 1;
            """,
            (account_id, tg_user_id),
        )
        row = await cur.fetchone()
        if not row:
            raise ValueError("account not found or not accessible")

        await db.execute(
            "UPDATE owners SET active_account_id = ? WHERE tg_user_id = ?;",
            (account_id, tg_user_id),
        )
        await db.commit()


async def get_active_account(tg_user_id: int) -> Optional[Account]:
    """
    Returns active account object, or None if user has no accounts yet.
    """
    active_id, accounts = await list_accounts(tg_user_id)
    if not active_id:
        return None
    for acc in accounts:
        if acc.id == active_id:
            return acc
    return None
