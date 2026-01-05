from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db import SessionLocal
from app.services import (
    ensure_user, get_balance, atomic_transfer, format_receipt
)
from app.config import RECEIPT_GROUP_ID

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message):
    async with SessionLocal() as session:
        await ensure_user(session, message.from_user.id, message.from_user.username)
        await session.commit()

    await message.answer("Bank Bot v2 is running.\nCommands:\n/balance\n/pay <amount> <tg_id>")


@router.message(Command("balance"))
async def cmd_balance(message: Message):
    async with SessionLocal() as session:
        acc_id = await ensure_user(session, message.from_user.id, message.from_user.username)
        bal = await get_balance(session, acc_id)
        await session.commit()

    await message.answer(f"Balance: {bal}")


@router.message(Command("pay"))
async def cmd_pay(message: Message):
    parts = message.text.split()
    if len(parts) < 3:
        return await message.answer("Usage: /pay <amount> <tg_id>")

    try:
        amount = int(parts[1])
        target_tg_id = int(parts[2])
    except:
        return await message.answer("Invalid input. Usage: /pay <amount> <tg_id>")

    async with SessionLocal() as session:
        from_acc = await ensure_user(session, message.from_user.id, message.from_user.username)
        to_acc = await ensure_user(session, target_tg_id, None)

        try:
            txid = await atomic_transfer(
                session=session,
                actor_tg_id=message.from_user.id,
                from_acc=from_acc,
                to_acc=to_acc,
                amount=amount,
                tx_type="transfer",
                reason="user transfer",
            )
            await session.commit()
        except Exception as e:
            await session.rollback()
            return await message.answer(f"Transfer failed: {e}")

        receipt = format_receipt(
            txid=txid,
            tx_type="transfer",
            from_acc=from_acc,
            to_acc=to_acc,
            amount=amount,
            actor_tg_id=message.from_user.id,
            reason="user transfer",
        )

    await message.answer(receipt)

    # group receipt (best-effort)
    if RECEIPT_GROUP_ID:
        try:
            await message.bot.send_message(chat_id=RECEIPT_GROUP_ID, text=receipt)
        except:
            pass

