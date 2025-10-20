# db.py
import os
import random
import string
import aiosqlite

DB_PATH = os.environ.get("DB_PATH", "bank.db")

# -------------------- Helpers --------------------
async def _generate_unique_account_id(db: aiosqlite.Connection, prefix: str, digits: int, reserved: set[str] | None = None) -> str:
    reserved = reserved or set()
    while True:
        suffix = "".join(random.choices(string.digits, k=digits))
        acc_id = f"{prefix}{suffix}"
        if acc_id in reserved:
            continue
        cur = await db.execute("SELECT 1 FROM accounts WHERE account_id = ?", (acc_id,))
        if not await cur.fetchone():
            return acc_id

# -------------------- INIT --------------------
async def init_db(owner_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE,
            username TEXT,
            full_name TEXT,
            personal_account TEXT
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT UNIQUE,
            owner_tg_id INTEGER,
            type TEXT,           -- 'PERSONAL' | 'BUSINESS' | 'BANK'
            name TEXT,
            balance REAL DEFAULT 0
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS register_codes (
            code TEXT UNIQUE
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            txid TEXT,
            from_acc TEXT,
            to_acc TEXT,
            amount REAL,
            status TEXT
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            tg_id INTEGER UNIQUE,
            name TEXT
        )
        """)

        # ensure main bank account exists
        cursor = await db.execute("SELECT 1 FROM accounts WHERE account_id = 'ACC-001'")
        if not await cursor.fetchone():
            await db.execute(
                "INSERT INTO accounts (account_id, owner_tg_id, type, name, balance) VALUES ('ACC-001', ?, 'BANK', 'Central Bank', 0)",
                (owner_id,)
            )
        await db.commit()

# -------------------- USERS --------------------
async def create_user(tg_id, username, full_name, code):
    async with aiosqlite.connect(DB_PATH) as db:
        # consume a valid registration code
        cursor = await db.execute("SELECT code FROM register_codes WHERE code = ?", (code,))
        if not await cursor.fetchone():
            return None, "Invalid registration code."

        # already registered?
        cur_user = await db.execute("SELECT 1 FROM users WHERE tg_id = ?", (tg_id,))
        if await cur_user.fetchone():
            return None, "User already registered."

        # consume code
        await db.execute("DELETE FROM register_codes WHERE code = ?", (code,))

        # unique personal account (avoid ACC-001)
        account_id = await _generate_unique_account_id(db, "ACC-", 6, reserved={"ACC-001"})

        await db.execute(
            "INSERT INTO users (tg_id, username, full_name, personal_account) VALUES (?, ?, ?, ?)",
            (tg_id, username, full_name, account_id)
        )
        await db.execute(
            "INSERT INTO accounts (account_id, owner_tg_id, type, name, balance) VALUES (?, ?, 'PERSONAL', ?, 0)",
            (account_id, tg_id, full_name)
        )
        await db.commit()
    return account_id, None

async def get_user_by_tgid(tg_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

async def get_user_by_account(account_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT owner_tg_id FROM accounts WHERE account_id = ?", (account_id,))
        row = await cur.fetchone()
        if not row:
            return None
        owner_tg = row["owner_tg_id"]
        cur2 = await db.execute("SELECT * FROM users WHERE tg_id = ?", (owner_tg,))
        u = await cur2.fetchone()
        if not u:
            return {"tg_id": owner_tg}
        return {"tg_id": u["tg_id"], "username": u["username"], "full_name": u["full_name"], "account_id": u["personal_account"]}

async def list_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
        SELECT u.tg_id, u.username, u.full_name, a.account_id
        FROM users u
        JOIN accounts a ON a.owner_tg_id = u.tg_id AND a.type='PERSONAL'
        ORDER BY u.full_name COLLATE NOCASE
        """)
        rows = await cur.fetchall()
        return [{"tg_id": r["tg_id"], "username": r["username"], "full_name": r["full_name"], "account_id": r["account_id"]} for r in rows]

# -------------------- ACCOUNTS --------------------
async def list_user_accounts(tg_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT account_id, type, name, balance FROM accounts WHERE owner_tg_id = ?", (tg_id,))
        rows = await cur.fetchall()
        return [{"account_id": r["account_id"], "type": r["type"], "name": r["name"], "balance": r["balance"]} for r in rows]

async def can_use_account(tg_id, account_id, must_be_type=None):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT type FROM accounts WHERE account_id = ? AND owner_tg_id = ?", (account_id, tg_id))
        row = await cur.fetchone()
        if not row:
            return False
        if must_be_type and row[0] != must_be_type:
            return False
        return True

async def get_account_balance(account_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance FROM accounts WHERE account_id = ?", (account_id,))
        row = await cur.fetchone()
        return row[0] if row else 0

async def adjust_account_balance(account_id, amount):
    if amount == 0:
        return False, "Amount must be non-zero."
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance FROM accounts WHERE account_id = ?", (account_id,))
        row = await cur.fetchone()
        if not row:
            return False, "Account not found."
        new_bal = row[0] + amount
        if new_bal < 0:
            return False, "Insufficient funds."
        await db.execute("UPDATE accounts SET balance = ? WHERE account_id = ?", (new_bal, account_id))
        await db.commit()
    return True, None

# -------------------- TRANSFERS --------------------
async def transfer_funds(from_acc, to_acc, amount):
    if amount is None or amount <= 0:
        return False, "Amount must be > 0."
    if from_acc == to_acc:
        return False, "Cannot transfer to the same account."
    async with aiosqlite.connect(DB_PATH) as db:
        c1 = await db.execute("SELECT balance FROM accounts WHERE account_id = ?", (from_acc,))
        fr = await c1.fetchone()
        c2 = await db.execute("SELECT balance FROM accounts WHERE account_id = ?", (to_acc,))
        to = await c2.fetchone()
        if not fr or not to:
            return False, "Account not found."
        if fr[0] < amount:
            return False, "Not enough balance."
        await db.execute("UPDATE accounts SET balance = balance - ? WHERE account_id = ?", (amount, from_acc))
        await db.execute("UPDATE accounts SET balance = balance + ? WHERE account_id = ?", (amount, to_acc))
        await db.commit()
    return True, "Completed"

async def create_transaction(txid, from_acc, to_acc, amount, status):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO transactions (txid, from_acc, to_acc, amount, status) VALUES (?, ?, ?, ?, ?)",
            (txid, from_acc, to_acc, amount, status)
        )
        await db.commit()

# -------------------- BUSINESS --------------------
async def create_business_account(owner_tg_id, name):
    async with aiosqlite.connect(DB_PATH) as db:
        acc_id = await _generate_unique_account_id(db, "BUS-", 5)
        await db.execute(
            "INSERT INTO accounts (account_id, owner_tg_id, type, name, balance) VALUES (?, ?, 'BUSINESS', ?, 0)",
            (acc_id, owner_tg_id, name)
        )
        await db.commit()
    return acc_id, None

async def transfer_account_ownership(acc_id, new_owner):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT account_id FROM accounts WHERE account_id = ?", (acc_id,))
        if not await cur.fetchone():
            return False, "Account not found."
        await db.execute("UPDATE accounts SET owner_tg_id = ? WHERE account_id = ?", (new_owner, acc_id))
        await db.commit()
    return True, None

# -------------------- ADMIN --------------------
async def add_register_code(code):
    code = (code or "").strip()
    if not code:
        return False, "Code cannot be empty."
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("INSERT INTO register_codes (code) VALUES (?)", (code,))
            await db.commit()
            return True, None
        except Exception:
            return False, "Code already exists."

async def add_admin(tg_id, name):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO admins (tg_id, name) VALUES (?, ?)", (tg_id, name))
        await db.commit()

async def remove_admin(tg_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admins WHERE tg_id = ?", (tg_id,))
        await db.commit()

async def list_admins():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT tg_id, name FROM admins")
        rows = await cur.fetchall()
        return [(r[0], r[1]) for r in rows]

async def is_admin(tg_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT tg_id FROM admins WHERE tg_id = ?", (tg_id,))
        return bool(await cur.fetchone())

async def is_bank_owner(tg_id, owner_id):
    return int(tg_id) == int(owner_id)

# -------------------- DELETE --------------------
async def delete_account(account_id: str):
    acc = account_id.upper()
    if acc == "ACC-001":
        return False, "Cannot delete the main bank account."
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM accounts WHERE account_id = ?", (acc,))
        await db.commit()
        if cur.rowcount == 0:
            return False, "Account not found."
    return True, None

async def delete_business_account(account_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM accounts WHERE account_id = ? AND type = 'BUSINESS'", (account_id.upper(),))
        await db.commit()
        if cur.rowcount == 0:
            return False, "Business account not found."
    return True, None
