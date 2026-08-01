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

    # Движку бандлов нужно хранилище FSM: шаг может прийти из фоновой задачи,
    # у которой своего FSMContext нет, а разговор после расклада всё равно
    # должен начаться — иначе её следующая реплика улетит в пустоту.
    import bundle_run

    bundle_run.bind(dp.storage, handlers.Reading.in_dialogue)

    # Список команд в меню и сброс вебхука — вещи косметические, но оба ходят в
    # Telegram до старта полинга. В этом ДЦ связь с api.telegram.org иногда
    # отваливается (см. /etc/hosts), и таймаут здесь ронял весь процесс: бот
    # умирал, не начав работать. Полинг сам умеет ждать и переподключаться,
    # поэтому пускаем его в любом случае.
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Начать"),
            BotCommand(command="menu", description="Меню"),
            BotCommand(command="help", description="Как это работает"),
        ])
    except Exception as e:  # noqa: BLE001 — меню команд не повод не запускаться
        log.warning("не удалось обновить меню команд: %s", e)

    sched = scheduler.setup(bot)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:  # noqa: BLE001 — вебхука у нас и так нет
        log.warning("не удалось сбросить вебхук: %s", e)
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
