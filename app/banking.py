from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Tuple

import aiosqlite

from app.config import settings
from app.receipt.generator import generate_receipt


# System account IDs will be reserved later via DB seed.
# For now we use constant logical keys.
SYSTEM_POOL_KEY = "__MAIN_POOL__"


@dataclass(frozen=True)
class TxRow:
    receipt_no: str
    ts_utc: str
    from_account_id: Optional[int]
    to_account_id: Optional[int]
    amount: int
    status: str
    description: str
    created_by_tg_id: int
    forced: int


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def get_balance(account_id: int) -> int:
    """
    Ledger-based balance:
    IN  = sum(amount) where to_account_id = account_id and status in SUCCESS/FORCED
    OUT = sum(amount) where from_account_id = account_id and status in SUCCESS/FORCED
    """
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN to_account_id = ? THEN amount ELSE 0 END), 0) -
              COALESCE(SUM(CASE WHEN from_account_id = ? THEN amount ELSE 0 END), 0)
            FROM transactions
            WHERE status IN ('SUCCESS', 'FORCED')
              AND (from_account_id = ? OR to_account_id = ?);
            """,
            (account_id, account_id, account_id, account_id),
        )
        row = await cur.fetchone()
        return int(row[0] or 0)


async def get_last_7_days(account_id: int, limit: int = 50) -> List[TxRow]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT receipt_no, ts_utc, from_account_id, to_account_id, amount, status,
                   COALESCE(description,''), created_by_tg_id, forced
            FROM transactions
            WHERE (from_account_id = ? OR to_account_id = ?)
              AND ts_utc >= ?
            ORDER BY ts_utc DESC
            LIMIT ?;
            """,
            (account_id, account_id, cutoff, limit),
        )
        rows = await cur.fetchall()

    return [
        TxRow(
            receipt_no=r[0],
            ts_utc=r[1],
            from_account_id=r[2],
            to_account_id=r[3],
            amount=int(r[4]),
            status=r[5],
            description=r[6],
            created_by_tg_id=int(r[7]),
            forced=int(r[8]),
        )
        for r in rows
    ]


async def transfer(
    *,
    from_account_id: int,
    to_account_id: int,
    amount: int,
    description: str,
    created_by_tg_id: int,
    forced: bool = False,
) -> tuple[str, bytes]:
    """
    Atomic-ish transfer with balance check (unless forced).
    Produces: (receipt_no, receipt_png_bytes)

    NOTE: SQLite concurrency is OK for small bots; we use BEGIN IMMEDIATE to lock writes.
    """
    if amount <= 0:
        raise ValueError("amount must be > 0")

    description = (description or "").strip()
    if not description:
        raise ValueError("description is required")

    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON;")
        await db.execute("BEGIN IMMEDIATE;")  # lock for consistent balance + insert

        # Ensure accounts exist & active
        cur = await db.execute(
            "SELECT id, label, kind FROM accounts WHERE id = ? AND is_active = 1 LIMIT 1;",
            (from_account_id,),
        )
        from_row = await cur.fetchone()
        if not from_row:
            await db.execute("ROLLBACK;")
            raise ValueError("sender account not found")

        cur = await db.execute(
            "SELECT id, label, kind FROM accounts WHERE id = ? AND is_active = 1 LIMIT 1;",
            (to_account_id,),
        )
        to_row = await cur.fetchone()
        if not to_row:
            await db.execute("ROLLBACK;")
            raise ValueError("receiver account not found")

        # Balance check (ledger-based) unless forced
        if not forced:
            cur = await db.execute(
                """
                SELECT
                  COALESCE(SUM(CASE WHEN to_account_id = ? THEN amount ELSE 0 END), 0) -
                  COALESCE(SUM(CASE WHEN from_account_id = ? THEN amount ELSE 0 END), 0)
                FROM transactions
                WHERE status IN ('SUCCESS', 'FORCED')
                  AND (from_account_id = ? OR to_account_id = ?);
                """,
                (from_account_id, from_account_id, from_account_id, from_account_id),
            )
            bal_row = await cur.fetchone()
            balance = int(bal_row[0] or 0)
            if balance < amount:
                await db.execute("ROLLBACK;")
                raise ValueError("insufficient funds")

        # Make receipt (needs display strings)
        sender_display = f"{from_row[1]} ({from_row[2]}) [ID:{from_account_id}]"
        receiver_display = f"{to_row[1]} ({to_row[2]}) [ID:{to_account_id}]"

        status = "FORCED" if forced else "SUCCESS"

        receipt_no, image = generate_receipt(
            sender_account=sender_display,
            receiver_account=receiver_display,
            amount=amount,
            status=status,
            description=description,
        )

        # Insert ledger row
        await db.execute(
            """
            INSERT INTO transactions (
                receipt_no, ts_utc,
                from_account_id, to_account_id,
                amount, status, description,
                created_by_tg_id, forced
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                receipt_no,
                _utc_now_iso(),
                from_account_id,
                to_account_id,
                amount,
                status,
                description,
                created_by_tg_id,
                1 if forced else 0,
            ),
        )

        await db.commit()

    # Convert image to PNG bytes (outside transaction)
    from io import BytesIO
    bio = BytesIO()
    bio.name = f"receipt_{receipt_no}.png"
    image.save(bio, format="PNG")
    return receipt_no, bio.getvalue()
