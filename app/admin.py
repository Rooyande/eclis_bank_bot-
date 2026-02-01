from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from app.config import settings
from app.db import create_account, get_or_create_owner


SYSTEM_POOL_OWNER_TG_ID = 0  # system owner (not a real telegram user)
SYSTEM_POOL_KIND = "system"
SYSTEM_POOL_LABEL = "MAIN POOL"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def _ensure_meta_table() -> None:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                k TEXT PRIMARY KEY,
                v TEXT NOT NULL
            );
            """
        )
        await db.commit()


async def get_owner_tg_id() -> Optional[int]:
    await _ensure_meta_table()
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute("SELECT v FROM meta WHERE k='OWNER_TG_ID' LIMIT 1;")
        row = await cur.fetchone()
        if not row:
            return None
        v = str(row[0]).strip()
        return int(v) if v.isdigit() else None


async def ensure_owner_seed(owner_tg_id: int) -> None:
    """
    FINALIZE SECURITY:
    - Can ONLY be set once.
    - If OWNER_TG_ID already exists, this function raises.
    """
    await _ensure_meta_table()
    existing = await get_owner_tg_id()
    if existing is not None:
        raise PermissionError("OWNER already set and locked")

    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            "INSERT INTO meta(k, v) VALUES('OWNER_TG_ID', ?);",
            (str(owner_tg_id),),
        )
        await db.commit()


async def is_owner(tg_user_id: int) -> bool:
    owner_id = await get_owner_tg_id()
    return owner_id is not None and tg_user_id == owner_id


async def is_admin(tg_user_id: int) -> bool:
    """
    Admins are stored in admins table; owner is implicitly admin.
    """
    if await is_owner(tg_user_id):
        return True

    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM admins WHERE tg_user_id = ? AND is_active = 1 LIMIT 1;",
            (tg_user_id,),
        )
        row = await cur.fetchone()
        return bool(row)


async def add_admin(tg_user_id: int) -> None:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO admins(tg_user_id, is_active, created_at)
            VALUES (?, 1, ?)
            ON CONFLICT(tg_user_id) DO UPDATE SET is_active=1;
            """,
            (tg_user_id, _utc_now_iso()),
        )
        await db.commit()


async def remove_admin(tg_user_id: int) -> None:
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            "UPDATE admins SET is_active = 0 WHERE tg_user_id = ?;",
            (tg_user_id,),
        )
        await db.commit()


async def ensure_main_pool_account() -> int:
    """
    Creates (or returns) MAIN POOL as a system account.
    Returns account_id of the pool.
    """
    await get_or_create_owner(SYSTEM_POOL_OWNER_TG_ID)

    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT id FROM accounts
            WHERE owner_tg_id = ?
              AND kind = ?
              AND label = ?
              AND is_active = 1
            LIMIT 1;
            """,
            (SYSTEM_POOL_OWNER_TG_ID, SYSTEM_POOL_KIND, SYSTEM_POOL_LABEL),
        )
        row = await cur.fetchone()
        if row:
            return int(row[0])

    pool_id = await create_account(
        tg_user_id=SYSTEM_POOL_OWNER_TG_ID,
        kind=SYSTEM_POOL_KIND,
        label=SYSTEM_POOL_LABEL,
        set_active=True,
    )
    return int(pool_id)


async def get_main_pool_account_id() -> int:
    return await ensure_main_pool_account()
