from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import aiosqlite

from app.config import settings
from app.admin import is_admin
from app.banking import transfer as banking_transfer


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def ensure_payroll_schema() -> None:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON;")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS business_accounts (
                account_id INTEGER PRIMARY KEY,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS business_staff (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_account_id INTEGER NOT NULL,
                staff_name TEXT NOT NULL,
                staff_tg_id INTEGER,
                staff_account_id INTEGER NOT NULL,
                monthly_salary INTEGER NOT NULL CHECK(monthly_salary > 0),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,

                FOREIGN KEY (business_account_id) REFERENCES accounts(id) ON DELETE CASCADE,
                FOREIGN KEY (staff_account_id) REFERENCES accounts(id) ON DELETE RESTRICT
            );
            """
        )

        await db.execute("CREATE INDEX IF NOT EXISTS idx_staff_business ON business_staff(business_account_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_staff_active ON business_staff(business_account_id, is_active);")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS payroll_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_account_id INTEGER NOT NULL,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                created_by_tg_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,

                UNIQUE(business_account_id, year, month),
                FOREIGN KEY (business_account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );
            """
        )

        await db.commit()


async def register_business_account(admin_tg_id: int, account_id: int) -> None:
    if not await is_admin(admin_tg_id):
        raise PermissionError("admin only")

    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON;")

        # Must exist and active
        cur = await db.execute(
            "SELECT 1 FROM accounts WHERE id = ? AND is_active = 1 LIMIT 1;",
            (account_id,),
        )
        if not await cur.fetchone():
            raise ValueError("account not found")

        await db.execute(
            """
            INSERT INTO business_accounts(account_id, is_active, created_at)
            VALUES (?, 1, ?)
            ON CONFLICT(account_id) DO UPDATE SET is_active=1;
            """,
            (account_id, _utc_now_iso()),
        )
        await db.commit()


async def add_staff(
    admin_tg_id: int,
    business_account_id: int,
    staff_name: str,
    staff_account_id: int,
    monthly_salary: int,
    staff_tg_id: int | None = None,
) -> int:
    if not await is_admin(admin_tg_id):
        raise PermissionError("admin only")

    staff_name = (staff_name or "").strip()
    if not staff_name:
        raise ValueError("staff_name required")

    if monthly_salary <= 0:
        raise ValueError("monthly_salary must be > 0")

    await ensure_payroll_schema()

    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON;")

        # Ensure business is registered
        cur = await db.execute(
            "SELECT 1 FROM business_accounts WHERE account_id = ? AND is_active = 1 LIMIT 1;",
            (business_account_id,),
        )
        if not await cur.fetchone():
            raise ValueError("business account is not registered")

        # Ensure staff account exists
        cur = await db.execute(
            "SELECT 1 FROM accounts WHERE id = ? AND is_active = 1 LIMIT 1;",
            (staff_account_id,),
        )
        if not await cur.fetchone():
            raise ValueError("staff account not found")

        cur = await db.execute(
            """
            INSERT INTO business_staff(
                business_account_id, staff_name, staff_tg_id, staff_account_id,
                monthly_salary, is_active, created_at
            )
            VALUES (?, ?, ?, ?, ?, 1, ?);
            """,
            (
                business_account_id,
                staff_name,
                staff_tg_id,
                staff_account_id,
                monthly_salary,
                _utc_now_iso(),
            ),
        )
        await db.commit()
        return int(cur.lastrowid)


async def list_staff(admin_tg_id: int, business_account_id: int) -> List[tuple]:
    if not await is_admin(admin_tg_id):
        raise PermissionError("admin only")

    await ensure_payroll_schema()

    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT id, staff_name, staff_tg_id, staff_account_id, monthly_salary, is_active
            FROM business_staff
            WHERE business_account_id = ?
            ORDER BY id ASC;
            """,
            (business_account_id,),
        )
        return await cur.fetchall()


async def run_payroll(
    admin_tg_id: int,
    business_account_id: int,
    year: int,
    month: int,
    note: str,
) -> List[tuple]:
    """
    Pays salaries from business account to staff accounts.
    Prevents duplicate run for same (business, year, month).
    Returns list of (staff_id, receipt_no).
    """
    if not await is_admin(admin_tg_id):
        raise PermissionError("admin only")

    if month < 1 or month > 12:
        raise ValueError("month must be 1..12")

    note = (note or "").strip()
    if not note:
        note = f"Salary {year}-{month:02d}"

    await ensure_payroll_schema()

    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON;")
        await db.execute("BEGIN IMMEDIATE;")

        # Prevent duplicate month run
        try:
            await db.execute(
                """
                INSERT INTO payroll_runs(business_account_id, year, month, created_by_tg_id, created_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                (business_account_id, year, month, admin_tg_id, _utc_now_iso()),
            )
        except Exception:
            await db.execute("ROLLBACK;")
            raise ValueError("payroll already executed for this business/month")

        cur = await db.execute(
            """
            SELECT id, staff_name, staff_account_id, monthly_salary
            FROM business_staff
            WHERE business_account_id = ?
              AND is_active = 1;
            """,
            (business_account_id,),
        )
        staff_rows = await cur.fetchall()

        await db.commit()

    # Execute transfers (each creates its own ledger row + receipt)
    results: List[tuple] = []
    for staff_id, staff_name, staff_account_id, salary in staff_rows:
        desc = f"{note} | {staff_name}"
        receipt_no, _png = await banking_transfer(
            from_account_id=business_account_id,
            to_account_id=int(staff_account_id),
            amount=int(salary),
            description=desc,
            created_by_tg_id=admin_tg_id,
            forced=False,
        )
        results.append((int(staff_id), str(receipt_no)))

    return results
