from io import BytesIO
from aiogram import F
from aiogram.types import Message, BufferedInputFile
from aiogram.dispatcher.router import Router

from app.db import (
    get_active_account,
    list_accounts,
)
from app.receipt.generator import generate_receipt
import aiosqlite
from app.config import settings
from datetime import datetime, timezone

router = Router()


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


@router.message(F.text.startswith("/transfer"))
async def transfer_handler(message: Message):
    """
    Usage:
    /transfer <to_account_id> <amount> <description...>
    """
    parts = message.text.split(maxsplit=3)
    if len(parts) < 4:
        await message.answer(
            "Usage:\n"
            "/transfer <to_account_id> <amount> <description>"
        )
        return

    _, to_acc_raw, amount_raw, description = parts

    if not to_acc_raw.isdigit() or not amount_raw.isdigit():
        await message.answer("Account ID and amount must be numeric.")
        return

    to_account_id = int(to_acc_raw)
    amount = int(amount_raw)

    if amount <= 0:
        await message.answer("Amount must be greater than zero.")
        return

    sender = await get_active_account(message.from_user.id)
    if not sender:
        await message.answer("No active account. Create or switch an account first.")
        return

    # verify receiver exists
    _, all_accounts = await list_accounts(message.from_user.id)
    receiver_label = f"ACCOUNT-{to_account_id}"

    receipt_no, image = generate_receipt(
        sender_account=f"{sender.label} ({sender.kind})",
        receiver_account=receiver_label,
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

    bio = BytesIO()
    bio.name = f"receipt_{receipt_no}.png"
    image.save(bio, format="PNG")
    bio.seek(0)

    await message.answer_photo(
        BufferedInputFile(bio.read(), filename=bio.name),
        caption=(
            f"Transfer completed.\n"
            f"Receipt No: {receipt_no}"
        ),
    )
