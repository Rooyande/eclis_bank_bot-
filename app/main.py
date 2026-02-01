import asyncio
import os
from io import BytesIO

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings
from app.db import (
    init_db,
    get_active_account,
    list_accounts,
    set_active_account,
)
from app.handlers.accounts import router as accounts_router
from app.banking import (
    transfer as banking_transfer,
    get_last_7_days,
    get_balance,
)
from app.admin import (
    ensure_owner_seed,
    is_owner,
    is_admin,
    add_admin,
    remove_admin,
    get_main_pool_account_id,
)
from app.payroll import (
    register_business_account,
    add_staff,
    list_staff,
    run_payroll,
    ensure_payroll_schema,
)
from app.receipt.generator import generate_receipt

CURRENCY_UNIT = "SOLEN"


def _ensure_db_dir():
    # If DB_PATH contains a directory (e.g. data/bot.db), create it.
    db_path = getattr(settings, "DB_PATH", "data/eclis.db")
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)


# ───────── UI (Inline Menu) ─────────

def build_main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="Balance", callback_data="menu:balance")
    kb.button(text="Accounts / Switch", callback_data="menu:accounts")
    kb.button(text="History (7d)", callback_data="menu:history")
    kb.button(text="Transfer", callback_data="menu:transfer")
    kb.button(text="Admin", callback_data="menu:admin")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


async def send_menu_to_message(msg: Message):
    await msg.answer("ECLIS BANKING SYSTEM\n\nSelect an action:", reply_markup=build_main_menu())


# ───────── Receipt regeneration (for payroll sending) ─────────

async def _regen_receipt_png(receipt_no: str) -> bytes:
    _ensure_db_dir()
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT from_account_id, to_account_id, amount, status, COALESCE(description,'')
            FROM transactions
            WHERE receipt_no = ?
            LIMIT 1;
            """,
            (receipt_no,),
        )
        tx = await cur.fetchone()
        if not tx:
            raise ValueError("receipt not found")

        from_id, to_id, amount, status, desc = tx

        def _fmt(label, kind, acc_id):
            return f"{label} ({kind}) [ID:{acc_id}]"

        if from_id is None:
            sender_display = "SYSTEM"
        else:
            cur = await db.execute("SELECT label, kind FROM accounts WHERE id = ? LIMIT 1;", (int(from_id),))
            r = await cur.fetchone()
            sender_display = _fmt(r[0], r[1], int(from_id)) if r else f"ACCOUNT [ID:{from_id}]"

        if to_id is None:
            receiver_display = "SYSTEM"
        else:
            cur = await db.execute("SELECT label, kind FROM accounts WHERE id = ? LIMIT 1;", (int(to_id),))
            r = await cur.fetchone()
            receiver_display = _fmt(r[0], r[1], int(to_id)) if r else f"ACCOUNT [ID:{to_id}]"

    receipt_no2, image = generate_receipt(
        sender_account=sender_display,
        receiver_account=receiver_display,
        amount=int(amount),
        status=str(status),
        description=str(desc),
        receipt_no=str(receipt_no),
    )

    bio = BytesIO()
    bio.name = f"receipt_{receipt_no2}.png"
    image.save(bio, format="PNG")
    return bio.getvalue()


# ───────── Helpers without mutating Message (fix frozen_instance) ─────────

async def show_balance(user_id: int, reply_to: Message):
    acc = await get_active_account(user_id)
    if not acc:
        await reply_to.answer("No active account. Create: /new_personal or /new_business")
        return
    bal = await get_balance(acc.id)
    await reply_to.answer(f"Balance for {acc.label} ({acc.kind}) [ID:{acc.id}]: {bal:,} {CURRENCY_UNIT}")


async def show_history(user_id: int, reply_to: Message):
    acc = await get_active_account(user_id)
    if not acc:
        await reply_to.answer("No active account. Create: /new_personal or /new_business")
        return
    rows = await get_last_7_days(acc.id, limit=30)
    if not rows:
        await reply_to.answer("No transactions in last 7 days.")
        return

    lines = [f"Last 7 days history for {acc.label} ({acc.kind}) [ID:{acc.id}]:"]
    for r in rows:
        direction = "OUT" if r.from_account_id == acc.id else "IN"
        other = r.to_account_id if direction == "OUT" else r.from_account_id
        lines.append(f"{direction} | {r.amount:,} {CURRENCY_UNIT} | {r.status} | other:{other} | #{r.receipt_no}")
    await reply_to.answer("\n".join(lines))


# ───────── Core handlers ─────────

async def start_handler(message: Message):
    await send_menu_to_message(message)


async def menu_handler(message: Message):
    await send_menu_to_message(message)


async def init_handler(message: Message):
    _ensure_db_dir()
    await init_db()
    await ensure_payroll_schema()
    await message.answer("DB INIT OK")


# ───────── OWNER / ADMIN ─────────

async def set_owner_handler(message: Message):
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /set_owner <tg_id>")
        return
    try:
        await ensure_owner_seed(int(parts[1]))
    except Exception as e:
        await message.answer(f"Failed: {e}")
        return
    await message.answer("OWNER set.")


async def admin_add_handler(message: Message):
    if not await is_owner(message.from_user.id):
        await message.answer("Only OWNER can add admins.")
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /admin_add <tg_id>")
        return
    await add_admin(int(parts[1]))
    await message.answer("Admin added.")


async def admin_remove_handler(message: Message):
    if not await is_owner(message.from_user.id):
        await message.answer("Only OWNER can remove admins.")
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /admin_remove <tg_id>")
        return
    await remove_admin(int(parts[1]))
    await message.answer("Admin removed.")


# ───────── POOL ─────────

async def pool_balance_handler(message: Message):
    if not await is_admin(message.from_user.id):
        await message.answer("Admin only.")
        return
    pool_id = await get_main_pool_account_id()
    bal = await get_balance(pool_id)
    await message.answer(f"MAIN POOL balance: {bal:,} {CURRENCY_UNIT}")


async def pool_give_handler(message: Message):
    if not await is_admin(message.from_user.id):
        await message.answer("Admin only.")
        return

    parts = message.text.split(maxsplit=3)
    if len(parts) < 4:
        await message.answer("Usage: /pool_give <to_account_id> <amount> <desc>")
        return

    _, to_id_raw, amount_raw, desc = parts
    if not to_id_raw.isdigit() or not amount_raw.isdigit():
        await message.answer("Account ID and amount must be numeric.")
        return

    pool_id = await get_main_pool_account_id()

    try:
        receipt_no, png = await banking_transfer(
            from_account_id=pool_id,
            to_account_id=int(to_id_raw),
            amount=int(amount_raw),
            description=desc,
            created_by_tg_id=message.from_user.id,
            forced=True,
        )
    except Exception as e:
        await message.answer(f"Failed: {e}")
        return

    await message.answer_photo(
        BufferedInputFile(png, filename=f"receipt_{receipt_no}.png"),
        caption=f"POOL TRANSFER OK\nReceipt: {receipt_no}",
    )


async def force_transfer_handler(message: Message):
    if not await is_admin(message.from_user.id):
        await message.answer("Admin only.")
        return

    parts = message.text.split(maxsplit=4)
    if len(parts) < 5:
        await message.answer("Usage: /force <from_id> <to_id> <amount> <desc>")
        return

    _, from_raw, to_raw, amount_raw, desc = parts
    if not (from_raw.isdigit() and to_raw.isdigit() and amount_raw.isdigit()):
        await message.answer("IDs and amount must be numeric.")
        return

    try:
        receipt_no, png = await banking_transfer(
            from_account_id=int(from_raw),
            to_account_id=int(to_raw),
            amount=int(amount_raw),
            description=desc,
            created_by_tg_id=message.from_user.id,
            forced=True,
        )
    except Exception as e:
        await message.answer(f"Force transfer failed: {e}")
        return

    await message.answer_photo(
        BufferedInputFile(png, filename=f"receipt_{receipt_no}.png"),
        caption=f"FORCED TRANSFER OK\nReceipt: {receipt_no}",
    )


# ───────── PAYROLL (ADMIN) ─────────

async def biz_register_handler(message: Message):
    if not await is_admin(message.from_user.id):
        await message.answer("Admin only.")
        return

    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /biz_register <business_account_id>")
        return

    try:
        await register_business_account(message.from_user.id, int(parts[1]))
    except Exception as e:
        await message.answer(f"Failed: {e}")
        return

    await message.answer("Business account registered.")


async def staff_add_handler(message: Message):
    if not await is_admin(message.from_user.id):
        await message.answer("Admin only.")
        return

    parts = message.text.split(maxsplit=4)
    if len(parts) < 5:
        await message.answer("Usage: /staff_add <biz_id> <staff_account_id> <salary> <name...>")
        return

    _, biz_raw, staff_acc_raw, salary_raw, name = parts
    if not (biz_raw.isdigit() and staff_acc_raw.isdigit() and salary_raw.isdigit()):
        await message.answer("biz_id, staff_account_id, salary must be numeric.")
        return

    try:
        staff_id = await add_staff(
            admin_tg_id=message.from_user.id,
            business_account_id=int(biz_raw),
            staff_name=name,
            staff_account_id=int(staff_acc_raw),
            monthly_salary=int(salary_raw),
            staff_tg_id=None,
        )
    except Exception as e:
        await message.answer(f"Failed: {e}")
        return

    await message.answer(f"Staff added. Staff ID: {staff_id}\n(Optional) Link TG: /staff_link {staff_id} <tg_id>")


async def staff_list_handler(message: Message):
    if not await is_admin(message.from_user.id):
        await message.answer("Admin only.")
        return

    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /staff_list <business_account_id>")
        return

    try:
        rows = await list_staff(message.from_user.id, int(parts[1]))
    except Exception as e:
        await message.answer(f"Failed: {e}")
        return

    if not rows:
        await message.answer("No staff found.")
        return

    lines = [f"Staff list for business account {parts[1]}:"]
    for r in rows:
        staff_id, staff_name, staff_tg_id, staff_account_id, monthly_salary, is_active = r
        status = "ACTIVE" if is_active else "OFF"
        tg_part = f"tg:{staff_tg_id}" if staff_tg_id else "tg:NOT_LINKED"
        lines.append(
            f"- #{staff_id} | {staff_name} | acc:{staff_account_id} | {monthly_salary:,} {CURRENCY_UNIT} | {tg_part} | {status}"
        )

    await message.answer("\n".join(lines))


async def staff_link_handler(message: Message):
    if not await is_admin(message.from_user.id):
        await message.answer("Admin only.")
        return

    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer("Usage: /staff_link <staff_id> <tg_id>")
        return

    staff_id = int(parts[1])
    tg_id = int(parts[2])

    _ensure_db_dir()
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON;")
        await db.execute("UPDATE business_staff SET staff_tg_id = ? WHERE id = ?;", (tg_id, staff_id))
        await db.commit()

    await message.answer("Staff TG ID linked.")


async def staff_unlink_handler(message: Message):
    if not await is_admin(message.from_user.id):
        await message.answer("Admin only.")
        return

    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /staff_unlink <staff_id>")
        return

    staff_id = int(parts[1])

    _ensure_db_dir()
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON;")
        await db.execute("UPDATE business_staff SET staff_tg_id = NULL WHERE id = ?;", (staff_id,))
        await db.commit()

    await message.answer("Staff TG ID unlinked.")


async def payroll_run_handler(message: Message):
    if not await is_admin(message.from_user.id):
        await message.answer("Admin only.")
        return

    parts = message.text.split(maxsplit=4)
    if len(parts) < 4:
        await message.answer("Usage: /payroll <biz_id> <YYYY> <MM> <note...>")
        return

    biz_raw = parts[1]
    year_raw = parts[2]
    month_raw = parts[3]
    note = parts[4] if len(parts) == 5 else ""

    if not (biz_raw.isdigit() and year_raw.isdigit() and month_raw.isdigit()):
        await message.answer("biz_id, YYYY, MM must be numeric.")
        return

    biz_id = int(biz_raw)
    year = int(year_raw)
    month = int(month_raw)

    try:
        results = await run_payroll(
            admin_tg_id=message.from_user.id,
            business_account_id=biz_id,
            year=year,
            month=month,
            note=note,
        )
    except Exception as e:
        await message.answer(f"Payroll failed: {e}")
        return

    if not results:
        await message.answer("Payroll done, but no active staff.")
        return

    # lookup staff tg ids
    _ensure_db_dir()
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, staff_name, staff_tg_id FROM business_staff WHERE business_account_id = ?;",
            (biz_id,),
        )
        staff_rows = await cur.fetchall()

    staff_map = {int(r[0]): (str(r[1]), (int(r[2]) if r[2] is not None else None)) for r in staff_rows}

    sent = 0
    not_linked = 0
    failed = 0
    total_paid = 0

    for staff_id, receipt_no in results:
        _name, tg_id = staff_map.get(int(staff_id), (f"staff#{staff_id}", None))

        try:
            png = await _regen_receipt_png(str(receipt_no))
            _ensure_db_dir()
            async with aiosqlite.connect(settings.DB_PATH) as db:
                cur = await db.execute("SELECT amount FROM transactions WHERE receipt_no = ? LIMIT 1;", (str(receipt_no),))
                r = await cur.fetchone()
                if r:
                    total_paid += int(r[0])
        except Exception:
            png = None

        if tg_id is None:
            not_linked += 1
            continue

        if png is None:
            failed += 1
            continue

        try:
            await message.bot.send_photo(
                chat_id=tg_id,
                photo=BufferedInputFile(png, filename=f"receipt_{receipt_no}.png"),
                caption=f"Salary payment receipt.\nReceipt No: {receipt_no}",
            )
            sent += 1
        except Exception:
            failed += 1

    await message.answer(
        "Payroll executed.\n"
        f"Period: {year}-{month:02d}\n"
        f"Total paid: {total_paid:,} {CURRENCY_UNIT}\n"
        f"Receipts: {len(results)}\n"
        f"Sent to staff: {sent}\n"
        f"Not linked (no TG): {not_linked}\n"
        f"Failed deliveries: {failed}"
    )


# ───────── USER ─────────

async def balance_handler(message: Message):
    await show_balance(message.from_user.id, message)


async def transfer_handler(message: Message):
    parts = message.text.split(maxsplit=3)
    if len(parts) < 4:
        await message.answer("Usage: /transfer <to_account_id> <amount> <desc>")
        return

    _, to_raw, amount_raw, desc = parts
    if not to_raw.isdigit() or not amount_raw.isdigit():
        await message.answer("Account ID and amount must be numeric.")
        return

    sender = await get_active_account(message.from_user.id)
    if not sender:
        await message.answer("No active account.")
        return

    to_account_id = int(to_raw)

    try:
        receipt_no, png = await banking_transfer(
            from_account_id=sender.id,
            to_account_id=to_account_id,
            amount=int(amount_raw),
            description=desc,
            created_by_tg_id=message.from_user.id,
            forced=False,
        )
    except Exception as e:
        await message.answer(f"Transfer failed: {e}")
        return

    filename = f"receipt_{receipt_no}.png"

    await message.answer_photo(
        BufferedInputFile(png, filename=filename),
        caption=f"Transfer OK\nReceipt: {receipt_no}",
    )

    # receiver (best-effort)
    try:
        _ensure_db_dir()
        async with aiosqlite.connect(settings.DB_PATH) as db:
            cur = await db.execute(
                "SELECT owner_tg_id FROM accounts WHERE id = ? AND is_active = 1 LIMIT 1;",
                (to_account_id,),
            )
            row = await cur.fetchone()

        if row:
            receiver_owner_tg_id = int(row[0])
            if receiver_owner_tg_id not in (message.from_user.id, 0):
                await message.bot.send_photo(
                    chat_id=receiver_owner_tg_id,
                    photo=BufferedInputFile(png, filename=filename),
                    caption=f"You received a transfer.\nReceipt: {receipt_no}",
                )
    except Exception:
        await message.answer("Note: Could not deliver receipt to receiver.")


async def history_handler(message: Message):
    await show_history(message.from_user.id, message)


# ───────── Callback handlers (Menu) ─────────

async def on_menu_callback(call: CallbackQuery):
    await call.answer()
    user_id = call.from_user.id
    msg = call.message

    if call.data == "menu:balance":
        await show_balance(user_id, msg)

    elif call.data == "menu:accounts":
        active_id, accounts = await list_accounts(user_id)
        if not accounts:
            await msg.answer("No accounts. Create: /new_personal or /new_business")
            return

        kb = InlineKeyboardBuilder()
        for acc in accounts:
            mark = "✅" if acc.id == active_id else "▫️"
            kb.button(
                text=f"{mark} {acc.label} ({acc.kind}) [ID:{acc.id}]",
                callback_data=f"switch:{acc.id}",
            )
        kb.adjust(1)
        await msg.answer("Select active account:", reply_markup=kb.as_markup())

    elif call.data == "menu:history":
        await show_history(user_id, msg)

    elif call.data == "menu:transfer":
        await msg.answer("Use:\n/transfer <to_account_id> <amount> <description>")

    elif call.data == "menu:admin":
        if not await is_admin(user_id):
            await msg.answer("Admin only.")
            return
        await msg.answer(
            "Admin:\n"
            "/pool_balance\n"
            "/pool_give <to_account_id> <amount> <desc>\n"
            "/force <from_account_id> <to_account_id> <amount> <desc>\n"
            "/biz_register <business_account_id>\n"
            "/staff_add <business_account_id> <staff_account_id> <salary> <name...>\n"
            "/staff_list <business_account_id>\n"
            "/staff_link <staff_id> <tg_id>\n"
            "/staff_unlink <staff_id>\n"
            "/payroll <business_account_id> <YYYY> <MM> <note...>\n"
            "\nOwner-only:\n"
            "/set_owner <tg_id>\n"
            "/admin_add <tg_id>\n"
            "/admin_remove <tg_id>"
        )


async def on_switch_callback(call: CallbackQuery):
    await call.answer()
    data = call.data or ""
    _, acc_id_str = data.split(":", 1)
    if not acc_id_str.isdigit():
        await call.message.answer("Invalid switch payload.")
        return

    acc_id = int(acc_id_str)
    try:
        await set_active_account(call.from_user.id, acc_id)
    except Exception as e:
        await call.message.answer(f"Switch failed: {e}")
        return

    acc = await get_active_account(call.from_user.id)
    if not acc:
        await call.message.answer("Active account set, but could not load it.")
        return

    await call.message.answer(f"Active account: {acc.label} ({acc.kind}) [ID:{acc.id}]")
    await call.message.answer("Menu:", reply_markup=build_main_menu())


async def main():
    _ensure_db_dir()

    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher()

    dp.include_router(accounts_router)

    dp.message.register(start_handler, F.text == "/start")
    dp.message.register(menu_handler, F.text == "/menu")
    dp.message.register(init_handler, F.text == "/init")

    dp.message.register(set_owner_handler, F.text.startswith("/set_owner"))
    dp.message.register(admin_add_handler, F.text.startswith("/admin_add"))
    dp.message.register(admin_remove_handler, F.text.startswith("/admin_remove"))

    dp.message.register(pool_balance_handler, F.text == "/pool_balance")
    dp.message.register(pool_give_handler, F.text.startswith("/pool_give"))
    dp.message.register(force_transfer_handler, F.text.startswith("/force"))

    dp.message.register(biz_register_handler, F.text.startswith("/biz_register"))
    dp.message.register(staff_add_handler, F.text.startswith("/staff_add"))
    dp.message.register(staff_list_handler, F.text.startswith("/staff_list"))
    dp.message.register(staff_link_handler, F.text.startswith("/staff_link"))
    dp.message.register(staff_unlink_handler, F.text.startswith("/staff_unlink"))
    dp.message.register(payroll_run_handler, F.text.startswith("/payroll"))

    dp.message.register(balance_handler, F.text == "/balance")
    dp.message.register(transfer_handler, F.text.startswith("/transfer"))
    dp.message.register(history_handler, F.text == "/history")

    dp.callback_query.register(on_menu_callback, F.data.startswith("menu:"))
    dp.callback_query.register(on_switch_callback, F.data.startswith("switch:"))

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())