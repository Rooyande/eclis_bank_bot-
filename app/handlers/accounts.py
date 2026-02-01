from aiogram import F
from aiogram.types import Message
from aiogram.dispatcher.router import Router

from app.db import (
    init_db,
    list_accounts,
    set_active_account,
    create_account,
    get_active_account,
)

router = Router()


@router.message(F.text == "/init")
async def init_handler(message: Message):
    """
    One-time DB init command.
    """
    await init_db()
    await message.answer("DB INIT OK")


@router.message(F.text == "/accounts")
async def list_accounts_handler(message: Message):
    """
    Shows all accounts and current active one.
    """
    tg_id = message.from_user.id
    active_id, accounts = await list_accounts(tg_id)

    if not accounts:
        await message.answer(
            "No accounts found.\n"
            "Use /new_personal or /new_business to create one."
        )
        return

    lines = ["Your accounts:"]
    for acc in accounts:
        mark = "✅" if acc.id == active_id else "▫️"
        lines.append(
            f"{mark} ID:{acc.id} | {acc.label} ({acc.kind})"
        )

    lines.append("\nUse /switch <account_id> to change active account.")
    await message.answer("\n".join(lines))


@router.message(F.text.startswith("/switch"))
async def switch_account_handler(message: Message):
    """
    Switch active account.
    """
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /switch <account_id>")
        return

    account_id = int(parts[1])
    tg_id = message.from_user.id

    try:
        await set_active_account(tg_id, account_id)
    except Exception as e:
        await message.answer(f"Error: {e}")
        return

    acc = await get_active_account(tg_id)
    await message.answer(
        f"Active account switched to:\n"
        f"{acc.label} ({acc.kind})"
    )


@router.message(F.text == "/new_personal")
async def new_personal_handler(message: Message):
    tg_id = message.from_user.id
    acc_id = await create_account(
        tg_user_id=tg_id,
        kind="personal",
        label="Personal",
        set_active=True,
    )
    await message.answer(f"Personal account created. ID: {acc_id}")


@router.message(F.text == "/new_business")
async def new_business_handler(message: Message):
    tg_id = message.from_user.id
    acc_id = await create_account(
        tg_user_id=tg_id,
        kind="business",
        label="Business",
        set_active=True,
    )
    await message.answer(f"Business account created. ID: {acc_id}")
