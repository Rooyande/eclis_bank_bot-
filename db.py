# db.py
import os, aiosqlite, string, random
from typing import Tuple, Dict, Any, List

DB_PATH = os.environ.get("DB_PATH", "bank.db")

def _gen_account_id() -> str:
    # e.g., ACC-AB12CD
    alphabet = string.ascii_uppercase + string.digits
    return "ACC-" + "".join(random.choice(alphabet) for _ in range(6))

async def init_db(bank_owner_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT
        );

        CREATE TABLE IF NOT EXISTS accounts (
            account_id TEXT PRIMARY KEY,
            owner_tg_id INTEGER,
            balance REAL DEFAULT 0,
            type TEXT CHECK(type IN ('PERSONAL','BUSINESS','BANK')) NOT NULL,
            name TEXT,
            FOREIGN KEY(owner_tg_id) REFERENCES users(tg_id)
        );

        CREATE TABLE IF NOT EXISTS account_access (
            tg_id INTEGER,
            account_id TEXT,
            PRIMARY KEY (tg_id, account_id),
            FOREIGN KEY(tg_id) REFERENCES users(tg_id),
            FOREIGN KEY(account_id) REFERENCES accounts(account_id)
        );

        CREATE TABLE IF NOT EXISTS transactions (
            txid TEXT PRIMARY KEY,
            from_account TEXT,
            to_account TEXT,
            amount REAL,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS register_codes (
            code TEXT PRIMARY KEY,
            used INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS admins (
            tg_id INTEGER PRIMARY KEY,
            name TEXT
        );
        """)
        # Ensure bank owner exists in users
        await db.execute(
            "INSERT OR IGNORE INTO users (tg_id, username, full_name) VALUES (?, ?, ?)",
            (bank_owner_id, None, "BANK_OWNER")
        )

        # Ensure ACC-001 exists as BANK account
        await db.execute("""
            INSERT OR IGNORE INTO accounts (account_id, owner_tg_id, balance, type, name)
            VALUES ('ACC-001', ?, 0, 'BANK', 'Central Bank')
        """, (bank_owner_id,))
        await db.commit()

async def is_bank_owner(tg_id: int, bank_owner_id: int) -> bool:
    return tg_id == bank_owner_id

async def is_admin(tg_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM admins WHERE tg_id = ?", (tg_id,)) as cur:
            row = await cur.fetchone()
            return row is not None

# ----- Admin management (owner only) -----
async def add_admin(tg_id: int, name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO admins (tg_id, name) VALUES (?, ?)", (tg_id, name))
        await db.commit()

async def remove_admin(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admins WHERE tg_id = ?", (tg_id,))
        await db.commit()

async def list_admins() -> List[Tuple[int, str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT tg_id, name FROM admins ORDER BY name") as cur:
            return await cur.fetchall()

# ----- Registration codes -----
async def add_register_code(code: str) -> Tuple[bool, str]:
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("INSERT INTO register_codes (code, used) VALUES (?, 0)", (code,))
            await db.commit()
            return True, "OK"
        except Exception as e:
            return False, f"Cannot add code: {e}"

async def _use_register_code(code: str) -> Tuple[bool, str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT used FROM register_codes WHERE code = ?", (code,)) as cur:
            row = await cur.fetchone()
            if not row:
                return False, "Invalid code"
            if row[0] == 1:
                return False, "Code already used"
        await db.execute("UPDATE register_codes SET used = 1 WHERE code = ?", (code,))
        await db.commit()
        return True, "OK"

# ----- Users & Accounts -----
async def create_user(tg_id: int, username: str, full_name: str, code: str) -> Tuple[str | None, str]:
    ok, msg = await _use_register_code(code)
    if not ok:
        return None, msg

    async with aiosqlite.connect(DB_PATH) as db:
        # upsert user
        await db.execute("""
            INSERT INTO users (tg_id, username, full_name) VALUES (?, ?, ?)
            ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name
        """, (tg_id, username, full_name))

        # create personal account
        while True:
            acc = _gen_account_id()
            async with db.execute("SELECT 1 FROM accounts WHERE account_id = ?", (acc,)) as cur:
                if not await cur.fetchone():
                    break

        await db.execute("""
            INSERT INTO accounts (account_id, owner_tg_id, balance, type, name)
            VALUES (?, ?, 0, 'PERSONAL', NULL)
        """, (acc, tg_id))

        # access link
        await db.execute("INSERT OR IGNORE INTO account_access (tg_id, account_id) VALUES (?, ?)", (tg_id, acc))

        await db.commit()
        return acc, "OK"

async def get_user_by_tgid(tg_id: int) -> Dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT tg_id, username, full_name FROM users WHERE tg_id = ?", (tg_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return {"tg_id": row[0], "username": row[1], "full_name": row[2]}

async def get_user_by_account(account_id: str) -> Dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT u.tg_id, u.username, u.full_name
            FROM accounts a JOIN users u ON a.owner_tg_id = u.tg_id
            WHERE a.account_id = ?
        """, (account_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return {"tg_id": row[0], "username": row[1], "full_name": row[2]}

async def list_all_users() -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT u.tg_id, u.username, u.full_name, a.account_id
            FROM users u
            JOIN accounts a ON a.owner_tg_id = u.tg_id AND a.type='PERSONAL'
            ORDER BY u.full_name
        """) as cur:
            rows = await cur.fetchall()
            return [{"tg_id": r[0], "username": r[1], "full_name": r[2], "account_id": r[3]} for r in rows]

async def list_user_accounts(tg_id: int) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT a.account_id, a.type, a.name, a.balance
            FROM account_access x
            JOIN accounts a ON a.account_id = x.account_id
            WHERE x.tg_id = ?
            ORDER BY CASE a.type WHEN 'PERSONAL' THEN 0 WHEN 'BUSINESS' THEN 1 ELSE 2 END, a.account_id
        """, (tg_id,)) as cur:
            rows = await cur.fetchall()
            return [{"account_id": r[0], "type": r[1], "name": r[2], "balance": r[3]} for r in rows]

async def can_use_account(tg_id: int, account_id: str, must_be_type: str | None = None) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        # check access
        async with db.execute("""
            SELECT a.type
            FROM account_access x JOIN accounts a ON a.account_id = x.account_id
            WHERE x.tg_id = ? AND x.account_id = ?
        """, (tg_id, account_id)) as cur:
            row = await cur.fetchone()
            if not row:
                return False
            if must_be_type and row[0] != must_be_type:
                return False
            return True

async def create_business_account(requester_tg_id: int, chat_id: int, name: str) -> Tuple[str | None, str]:
    # requester must be admin; checked at bot layer
    async with aiosqlite.connect(DB_PATH) as db:
        # ensure requester exists as user
        await db.execute("INSERT OR IGNORE INTO users (tg_id, username, full_name) VALUES (?, ?, ?)", (requester_tg_id, None, None))

        # create account
        while True:
            acc = _gen_account_id()
            async with db.execute("SELECT 1 FROM accounts WHERE account_id = ?", (acc,)) as cur:
                if not await cur.fetchone():
                    break

        await db.execute("""
            INSERT INTO accounts (account_id, owner_tg_id, balance, type, name)
            VALUES (?, ?, 0, 'BUSINESS', ?)
        """, (acc, requester_tg_id, name))

        # grant access to owner (requester)
        await db.execute("INSERT OR IGNORE INTO account_access (tg_id, account_id) VALUES (?, ?)", (requester_tg_id, acc))
        await db.commit()
        return acc, "OK"

async def transfer_account_ownership(account_id: str, new_owner_tg_id: int) -> Tuple[bool, str]:
    async with aiosqlite.connect(DB_PATH) as db:
        # account must exist and be BUSINESS
        async with db.execute("SELECT type FROM accounts WHERE account_id = ?", (account_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return False, "Account not found"
            if row[0] != "BUSINESS":
                return False, "Only BUSINESS accounts can be transferred"

        await db.execute("INSERT OR IGNORE INTO users (tg_id, username, full_name) VALUES (?, ?, ?)", (new_owner_tg_id, None, None))
        await db.execute("UPDATE accounts SET owner_tg_id = ? WHERE account_id = ?", (new_owner_tg_id, account_id))
        # move access
        await db.execute("DELETE FROM account_access WHERE account_id = ?", (account_id,))
        await db.execute("INSERT INTO account_access (tg_id, account_id) VALUES (?, ?)", (new_owner_tg_id, account_id))
        await db.commit()
        return True, "OK"

# ----- Balances -----
async def get_account_balance(account_id: str) -> float | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM accounts WHERE account_id = ?", (account_id,)) as cur:
            row = await cur.fetchone()
            return float(row[0]) if row else None

async def adjust_account_balance(account_id: str, delta: float) -> Tuple[bool, str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM accounts WHERE account_id = ?", (account_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return False, "Account not found"
            new_bal = float(row[0]) + float(delta)
            if new_bal < 0:
                return False, "Insufficient funds"
        await db.execute("UPDATE accounts SET balance = ? WHERE account_id = ?", (new_bal, account_id))
        await db.commit()
        return True, "OK"

# ----- Transfer & Transactions -----
async def transfer_funds(from_account: str, to_account: str, amount: float) -> Tuple[bool, str]:
    if amount <= 0:
        return False, "Invalid amount"

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        # fetch balances
        async with db.execute("SELECT balance FROM accounts WHERE account_id = ?", (from_account,)) as cur:
            row_from = await cur.fetchone()
        async with db.execute("SELECT balance FROM accounts WHERE account_id = ?", (to_account,)) as cur:
            row_to = await cur.fetchone()

        if not row_from or not row_to:
            await db.execute("ROLLBACK")
            return False, "Account not found"

        bal_from = float(row_from[0])
        bal_to = float(row_to[0])

        if bal_from < amount:
            await db.execute("ROLLBACK")
            return False, "Insufficient funds"

        bal_from -= amount
        bal_to += amount

        await db.execute("UPDATE accounts SET balance = ? WHERE account_id = ?", (bal_from, from_account))
        await db.execute("UPDATE accounts SET balance = ? WHERE account_id = ?", (bal_to, to_account))
        await db.commit()
        return True, "OK"

async def create_transaction(txid: str, from_account: str, to_account: str, amount: float, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO transactions (txid, from_account, to_account, amount, status)
            VALUES (?, ?, ?, ?, ?)
        """, (txid, from_account, to_account, amount, status))
        await db.commit()
