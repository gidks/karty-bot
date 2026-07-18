"""«Карты Не Врут» — точка входа. Запуск: python bot.py"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

import config
import database as db
import handlers
import scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")


async def main() -> None:
    config.validate()
    await db.init()

    session = AiohttpSession(proxy=config.BOT_PROXY) if config.BOT_PROXY else None
    if config.BOT_PROXY:
        log.info("Telegram: соединение через прокси %s", config.BOT_PROXY)

    bot = Bot(
        token=config.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(handlers.router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Начать"),
        BotCommand(command="menu", description="Меню"),
        BotCommand(command="help", description="Как это работает"),
    ])

    sched = scheduler.setup(bot)
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Бот запущен. Модель: %s | ЮKassa: %s",
             config.LLM_MODEL, "вкл" if config.yookassa_available() else "выкл")
    try:
        await dp.start_polling(bot)
    finally:
        sched.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Бот остановлен.")
