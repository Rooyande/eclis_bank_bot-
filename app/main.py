import asyncio
from io import BytesIO

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile

from app.config import settings
from app.db import init_db, get_active_account
from app.handlers.accounts import router as accounts_router
from app.receipt.generator import generate_receipt
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

CURRENCY_UNIT = "SOLEN"


async def start_handler(message: Message):
    await message.answer(
        "ECLIS BANKING SYSTEM\n\n"
        "User commands:\n"
        "/init\n"
        "/new_personal | /new_business\n"
        "/accounts | /switch <account_id>\n"
        "/balance\n"
        "/transfer <to_account_id> <amount> <description>\n"
        "/history\n\n"
        "Admin commands:\n"
        "/set_owner <tg_id>\n"
        "/admin_add <tg_id>\n"
        "/admin_remove <tg_id>\n"
        "/pool_balance\n"
        "/pool_give <to_account_id> <amount> <description>\n"
        "/force <from_account_id> <to_account_id> <amount> <description>"
    )


async def init_handler(message: Message):
    await init_db()
    await message.answer("DB INIT OK")


# ───────── OWNER / ADMIN ─────────

async def set_owner_handler(message: Message):
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /set_owner <tg_id>")
        return

    owner_id = int(parts[1])
    await ensure_owner_seed(owner_id)
    await message.answer(f"OWNER set to TG ID: {owner_id}")


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
        await message.answer(
            "Usage:\n/force <from_account_id> <to_account_id> <amount> <desc>"
        )
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


# ───────── USER ─────────

async def balance_handler(message: Message):
    acc = await get_active_account(message.from_user.id)
    if not acc:
        await message.answer("No active account.")
        return

    bal = await get_balance(acc.id)
    await message.answer(
        f"Balance for {acc.label} ({acc.kind}) [ID:{acc.id}]: {bal:,} {CURRENCY_UNIT}"
    )


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

    try:
        receipt_no, png = await banking_transfer(
            from_account_id=sender.id,
            to_account_id=int(to_raw),
            amount=int(amount_raw),
            description=desc,
            created_by_tg_id=message.from_user.id,
            forced=False,
        )
    except Exception as e:
        await message.answer(f"Transfer failed: {e}")
        return

    await message.answer_photo(
        BufferedInputFile(png, filename=f"receipt_{receipt_no}.png"),
        caption=f"Transfer OK\nReceipt: {receipt_no}",
    )


async def history_handler(message: Message):
    acc = await get_active_account(message.from_user.id)
    if not acc:
        await message.answer("No active account.")
        return

    rows = await get_last_7_days(acc.id)
    if not rows:
        await message.answer("No transactions in last 7 days.")
        return

    lines = [f"History for {acc.label} ({acc.kind})\n"]
    for r in rows:
        direction = "OUT" if r.from_account_id == acc.id else "IN"
        lines.append(
            f"{direction} | {r.amount:,} {CURRENCY_UNIT} | {r.status} | #{r.receipt_no}"
        )

    await message.answer("\n".join(lines))


async def main():
    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher()

    dp.include_router(accounts_router)

    dp.message.register(start_handler, F.text == "/start")
    dp.message.register(init_handler, F.text == "/init")

    dp.message.register(set_owner_handler, F.text.startswith("/set_owner"))
    dp.message.register(admin_add_handler, F.text.startswith("/admin_add"))
    dp.message.register(admin_remove_handler, F.text.startswith("/admin_remove"))

    dp.message.register(pool_balance_handler, F.text == "/pool_balance")
    dp.message.register(pool_give_handler, F.text.startswith("/pool_give"))
    dp.message.register(force_transfer_handler, F.text.startswith("/force"))

    dp.message.register(balance_handler, F.text == "/balance")
    dp.message.register(transfer_handler, F.text.startswith("/transfer"))
    dp.message.register(history_handler, F.text == "/history")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
