"""Отправка расклада в чат: серийная подача карта за картой, паузы «печатает…»,
картинки карт.

Вынесено из handlers, потому что теперь этим пользуются двое: сам бот, когда
человек раскладывает руками, и планировщик, когда приходит очередной шаг
бандла. Работаем через bot + chat_id, а не через Message: у фоновой задачи
объекта сообщения нет.
"""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

import config
import keyboards as kb
import texts
from texts import esc

log = logging.getLogger(__name__)


def today_msk() -> str:
    return datetime.now(ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d")


async def typing_while(bot: Bot, chat_id: int, coro):
    """Держит индикатор «печатает…», пока крутится долгая корутина (генерация LLM)."""
    task = asyncio.create_task(coro)
    try:
        while True:
            try:
                await bot.send_chat_action(chat_id, "typing")
            except Exception:  # noqa: BLE001 — индикатор не критичен
                pass
            done, _ = await asyncio.wait({task}, timeout=4)
            if done:
                break
    finally:
        if not task.done():
            task.cancel()
    return task.result()


async def send_card_photo(bot: Bot, chat_id: int, card_id: int) -> None:
    """Картинка карты в чат. Отключены или URL не отдался — молча пропускаем,
    текст придёт в любом случае."""
    url = config.card_img_url(card_id)
    if not url:
        return
    try:
        await bot.send_photo(chat_id, url)
    except Exception:  # noqa: BLE001 — картинка не критична, текст важнее
        pass


# Картинки экранов продажи → file_id. Первый раз Telegram тянет файл по URL,
# дальше отправляем по id: бесплатно и мгновенно. Живёт в памяти процесса,
# после рестарта наполняется заново — потерь нет.
_OFFER_FILE_IDS: dict[str, str] = {}


async def send_offer_photo(bot: Bot, chat_id: int, name: str) -> bool:
    """Картинка под экран продажи ('pack' | 'him' | 'month' | 'sub').
    Товар за 249 ₽ невидим — картинка делает обещание вещью, а сообщение
    с фото занимает пол-экрана и останавливает пролистывание."""
    ref = _OFFER_FILE_IDS.get(name) or config.offer_img_url(name)
    if not ref:
        return False
    try:
        msg = await bot.send_photo(chat_id, ref)
        if msg.photo and name not in _OFFER_FILE_IDS:
            _OFFER_FILE_IDS[name] = msg.photo[-1].file_id
        return True
    except Exception as e:  # noqa: BLE001 — картинка не важнее оффера
        log.warning("картинка оффера %s не ушла: %s", name, e)
        return False


async def send_offer(
    bot: Bot, chat_id: int, img: str | None, text: str,
    markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Экран продажи: картинка отдельным сообщением, следом текст с кнопкой.

    Картинкой и подписью одним сообщением не шлём: у caption лимит 1024 знака,
    а экраны объяснения бандлов длиннее. Текст важнее картинки."""
    if img:
        await send_offer_photo(bot, chat_id, img)
    await bot.send_message(chat_id, text, reply_markup=markup)


async def send_serial(
    bot: Bot, chat_id: int, spread: str, drawn: list[dict],
    labels: list[str] | None, parsed: dict, reading_id: int | None,
    final_markup: InlineKeyboardMarkup | None = None,
    header: str | None = None,
) -> None:
    """Серийная подача: карта за картой с паузами, затем итог.

    Подсказки-реплики вешаются reply-клавиатурой на последнее карточное
    сообщение (inline-кнопки итога их не перебивают — это разные клавиатуры)."""
    chips_kb: ReplyKeyboardMarkup | None = (
        kb.chips_reply(parsed["chips"]) if parsed.get("chips") else None)
    tail_markup = (final_markup if final_markup is not None
                   else kb.after_reading(reading_id))

    async def pause(sec: float) -> None:
        try:
            await bot.send_chat_action(chat_id, "typing")
        except Exception:  # noqa: BLE001 — индикатор не критичен
            pass
        await asyncio.sleep(sec)

    if header:
        await bot.send_message(chat_id, header)

    if spread == "week":
        if parsed.get("intro"):
            await bot.send_message(
                chat_id, texts.WEEK_INTRO_MSG.format(body=esc(parsed["intro"])))
        groups = [(0, 3), (3, 5), (5, 7)]
        for gi, (lo, hi) in enumerate(groups):
            await pause(1.1)
            blocks = [
                texts.week_day_html(
                    labels[i] if labels and i < len(labels) else f"День {i + 1}",
                    drawn[i], parsed["cards"][i])
                for i in range(lo, hi)
            ]
            await bot.send_message(
                chat_id, "\n\n".join(blocks),
                reply_markup=chips_kb if gi == len(groups) - 1 else None)
    else:
        if parsed.get("intro"):
            await bot.send_message(
                chat_id, texts.WEEK_INTRO_MSG.format(body=esc(parsed["intro"])))
        n = len(parsed["cards"])
        # Много карт (Кельтский крест) — темп бодрее, иначе разбор растянется
        step = 0.9 if n >= 8 else 1.4
        for i, body in enumerate(parsed["cards"]):
            await pause(step if i else 0.6)
            label = labels[i] if labels and i < len(labels) else None
            await bot.send_message(
                chat_id, texts.card_message_html(drawn[i], label, body, with_ref=True),
                reply_markup=chips_kb if i == n - 1 else None)

    await pause(1.0)
    tail = texts.AFTER_READING_SERIAL if parsed.get("chips") else texts.AFTER_READING
    if parsed.get("summary"):
        await bot.send_message(
            chat_id, texts.SUMMARY_MSG.format(body=esc(parsed["summary"])) + tail,
            reply_markup=tail_markup)
    else:
        invite = (texts.SERIAL_INVITE if parsed.get("chips")
                  else texts.AFTER_READING.lstrip("\n"))
        await bot.send_message(chat_id, invite, reply_markup=tail_markup)


async def send_plain_reading(
    bot: Bot, chat_id: int, body: str, reading_id: int | None,
    final_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Фолбэк: модель не разметила ответ блоками. У больших раскладов полотно
    не влезает в одно сообщение — режем по абзацам."""
    chunks = texts.split_body(esc(body))
    for part in chunks[:-1]:
        await bot.send_message(chat_id, part)
        await asyncio.sleep(0.4)
    await bot.send_message(
        chat_id, chunks[-1] + texts.AFTER_READING,
        reply_markup=(final_markup if final_markup is not None
                      else kb.after_reading(reading_id)),
    )
