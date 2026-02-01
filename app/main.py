import asyncio
from io import BytesIO

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BufferedInputFile

from app.config import settings
from app.receipt.generator import generate_receipt
from app.handlers.accounts import router as accounts_router


async def start_handler(message: Message):
    await message.answer(
        "ECLIS BANKING SYSTEM\n\n"
        "Bot is running.\n"
        "Use /receipt to get a test receipt.\n"
        "Use /init then /new_personal or /new_business then /accounts."
    )


async def receipt_handler(message: Message):
    # test data (later will come from DB / active account)
    sender_account = "USER-1001 (Personal)"
    receiver_account = "USER-2002 (Business)"
    amount = 250000
    status = "SUCCESS"
    description = "Test transfer"

    receipt_no, image = generate_receipt(
        sender_account=sender_account,
        receiver_account=receiver_account,
        amount=amount,
        status=status,
        description=description,
    )

    bio = BytesIO()
    bio.name = f"receipt_{receipt_no}.png"
    image.save(bio, format="PNG")
    bio.seek(0)

    await message.answer_photo(
        BufferedInputFile(bio.read(), filename=bio.name),
        caption=f"Receipt No: {receipt_no}",
    )


async def main():
    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher()

    dp.include_router(accounts_router)

    dp.message.register(start_handler, F.text == "/start")
    dp.message.register(receipt_handler, F.text == "/receipt")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
