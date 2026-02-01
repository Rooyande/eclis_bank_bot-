import asyncio
from io import BytesIO
from datetime import datetime, timezone, timedelta

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile

from app.config import settings
from app.db import init_db, get_active_account
from app.receipt.generator import generate_receipt
from app.handlers.accounts import router as accounts_router


async def _get_account_brief(account_id: int):
    """
    Returns (owner_tg_id, label, kind) or None
    """
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            "SELECT owner_tg_id, label, kind FROM accounts WHERE id = ? AND is_active = 1 LIMIT 1;",
            (account_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        return int(row[0]), str(row[1]), str(row[2])


async def _insert_transaction(
    receipt_no: str,
    from_account_id: int,
    to_account_id: int,
    amount: int,
    status: str,
    description: str,
    created_by_tg_id: int,
):
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO transactions (
                receipt_no, ts_utc,
                from_account_id, to_account_id,
                amount, status, description,
                created_by_tg_id, forced
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0);
            """,
            (
                receipt_no,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                from_account_id,
                to_account_id,
                amount,
                status,
                description,
                created_by_tg_id,
            ),
        )
        await db.commit()


async def start_handler(message: Message):
    await message.answer(
        "ECLIS BANKING SYSTEM\n\n"
        "Commands:\n"
        "/init  (one-time DB init)\n"
        "/new_personal | /new_business\n"
        "/accounts | /switch <account_id>\n"
        "/transfer <to_account_id> <amount> <description>\n"
        "/history"
    )


async def init_handler(message: Message):
    await init_db()
    await message.answer("DB INIT OK")


async def receipt_test_handler(message: Message):
    # test receipt generator only
    receipt_no, image = generate_receipt(
        sender_account="TEST-SENDER",
        receiver_account="TEST-RECEIVER",
        amount=123456,
        status="SUCCESS",
        description="Test receipt only",
    )
    bio = BytesIO()
    bio.name = f"receipt_{receipt_no}.png"
    image.save(bio, format="PNG")
    bio.seek(0)
    await message.answer_photo(
        BufferedInputFile(bio.read(), filename=bio.name),
        caption=f"Receipt No: {receipt_no}",
    )


async def transfer_handler(message: Message):
    """
    Usage:
      /transfer <to_account_id> <amount> <description...>
    Sends receipt to sender and receiver (if receiver has started bot).
    """
    parts = message.text.split(maxsplit=3)
    if len(parts) < 4:
        await message.answer("Usage: /transfer <to_account_id> <amount> <description>")
        return

    _, to_acc_raw, amount_raw, description = parts

    if not to_acc_raw.isdigit() or not amount_raw.isdigit():
        await message.answer("to_account_id and amount must be numeric.")
        return

    to_account_id = int(to_acc_raw)
    amount = int(amount_raw)

    if amount <= 0:
        await message.answer("Amount must be greater than zero.")
        return

    description = description.strip()
    if not description:
        await message.answer("Description is required.")
        return

    sender = await get_active_account(message.from_user.id)
    if not sender:
        await message.answer("No active account. Create/switch account first.")
        return

    receiver_info = await _get_account_brief(to_account_id)
    if not receiver_info:
        await message.answer("Receiver account not found.")
        return

    receiver_owner_tg_id, receiver_label, receiver_kind = receiver_info

    sender_display = f"{sender.label} ({sender.kind}) [ID:{sender.id}]"
    receiver_display = f"{receiver_label} ({receiver_kind}) [ID:{to_account_id}]"

    receipt_no, image = generate_receipt(
        sender_account=sender_display,
        receiver_account=receiver_display,
        amount=amount,
        status="SUCCESS",
        description=description,
    )

    await _insert_transaction(
        receipt_no=receipt_no,
        from_account_id=sender.id,
        to_account_id=to_account_id,
        amount=amount,
        status="SUCCESS",
        description=description,
        created_by_tg_id=message.from_user.id,
    )

    # Prepare PNG once, reuse for both
    bio = BytesIO()
    bio.name = f"receipt_{receipt_no}.png"
    image.save(bio, format="PNG")
    png_bytes = bio.getvalue()

    # Send to sender
    await message.answer_photo(
        BufferedInputFile(png_bytes, filename=bio.name),
        caption=f"Transfer completed.\nReceipt No: {receipt_no}",
    )

    # Send to receiver (best-effort)
    if receiver_owner_tg_id != message.from_user.id:
        try:
            await message.bot.send_photo(
                chat_id=receiver_owner_tg_id,
                photo=BufferedInputFile(png_bytes, filename=bio.name),
                caption=f"You received a transfer.\nReceipt No: {receipt_no}",
            )
        except Exception:
            # receiver might not have started bot / blocked bot / privacy limits
            await message.answer("Note: Could not deliver receipt to receiver (they may not have started the bot).")


async def history_handler(message: Message):
    """
    Shows last 7 days transactions for the user's active account.
    """
    acc = await get_active_account(message.from_user.id)
    if not acc:
        await message.answer("No active account. Create/switch account first.")
        return

    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")

    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT receipt_no, ts_utc, from_account_id, to_account_id, amount, status, COALESCE(description,'')
            FROM transactions
            WHERE (from_account_id = ? OR to_account_id = ?)
              AND ts_utc >= ?
            ORDER BY ts_utc DESC
            LIMIT 30;
            """,
            (acc.id, acc.id, cutoff),
        )
        rows = await cur.fetchall()

    if not rows:
        await message.answer("No transactions in last 7 days.")
        return

    lines = [f"Last 7 days history for: {acc.label} ({acc.kind}) [ID:{acc.id}]\n"]
    for r in rows:
        receipt_no, ts_utc, from_id, to_id, amount, status, desc = r
        direction = "OUT" if from_id == acc.id else "IN"
        other = to_id if direction == "OUT" else from_id
        lines.append(
            f"{direction} | {amount:,} | {status} | other:{other} | {ts_utc} | #{receipt_no}\n"
            f"desc: {desc}"
        )

    await message.answer("\n\n".join(lines))


async def main():
    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher()

    # Routers
    dp.include_router(accounts_router)

    # Core commands
    dp.message.register(start_handler, F.text == "/start")
    dp.message.register(init_handler, F.text == "/init")
    dp.message.register(receipt_test_handler, F.text == "/receipt")

    # Banking
    dp.message.register(transfer_handler, F.text.startswith("/transfer"))
    dp.message.register(history_handler, F.text == "/history")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
