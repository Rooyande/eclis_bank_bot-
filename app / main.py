import asyncio
from aiogram import Bot, Dispatcher

from app.config import BOT_TOKEN, require_env
from app.services import init_db
from app.handlers import router


async def main():
    require_env()
    await init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

