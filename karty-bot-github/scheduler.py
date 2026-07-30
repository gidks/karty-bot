"""Фоновые задачи: карта дня в 9:00 по Москве и возвращающее сообщение
через N дней после расклада."""

import asyncio
import json
import logging
import zlib
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import cards
import config
import database as db
import keyboards as kb
import llm
import prompts
import texts
from texts import esc

log = logging.getLogger(__name__)


def _today_msk() -> str:
    return datetime.now(ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d")


# Картинка → file_id: первый раз Telegram тянет файл по URL, дальше отправляем
# по id, то есть бесплатно и мгновенно. Живёт в памяти процесса, после рестарта
# наполняется заново — это нормально, потерь нет.
_MOOD_FILE_IDS: dict[int, str] = {}


def _mood_index(user_id: int, salt: str) -> int:
    """Какая картинка достанется этому человеку в этот раз. crc32, а не hash():
    у встроенного hash() соль случайная при каждом запуске, и одна и та же
    рассылка давала бы разные картинки после рестарта."""
    return zlib.crc32(f"{user_id}:{salt}".encode()) % max(config.MOOD_COUNT, 1)


async def send_mood_photo(bot: Bot, chat_id: int, user_id: int, salt: str) -> None:
    """Прикладывает к напоминалке атмосферную картинку из банка. Ротация — по
    пользователю и поводу, чтобы одному человеку не приходило одно и то же.
    Картинка некритична: любая ошибка не должна ронять само сообщение."""
    if config.MOOD_COUNT <= 0 or not config.MOOD_IMG_BASE:
        return
    idx = _mood_index(user_id, salt)
    ref = _MOOD_FILE_IDS.get(idx) or config.mood_img_url(idx)
    if not ref:
        return
    try:
        msg = await bot.send_photo(chat_id, ref)
        if msg.photo and idx not in _MOOD_FILE_IDS:
            _MOOD_FILE_IDS[idx] = msg.photo[-1].file_id
    except Exception as e:  # noqa: BLE001 — картинка не важнее письма
        log.warning("картинка напоминалки не ушла (%s): %s", idx, e)


async def personal_daily_text(user_id: int, name: str, card: dict) -> str | None:
    """Личная карта дня для подписчицы: та же карта, но просеянная через её
    последний расклад.

    Возвращает:
      текст — получилось;
      ""    — персонализировать нечем (раскладов ещё не было), это НЕ сбой;
      None  — сбой (провайдер, база): шлём общий текст.
    Разница важна: на None срабатывает предохранитель, а «нечем» не должно
    выключать персонализацию всем остальным."""
    try:
        rows = await db.last_readings(user_id, 1)
        if not rows:
            return ""  # запрос к модели не делаем вообще
        last = rows[0]
        body = await llm.chat(
            prompts.build_daily_personal_messages(
                card, name, last["topic"], last["question"]),
            max_tokens=400, temperature=0.9,
        )
    except Exception as e:  # noqa: BLE001 — личная карта не должна ронять рассылку
        # Сюда попадает и «database is locked», и любой сбой провайдера:
        # подписчица получит общий текст, остальные — свою карту как обычно.
        log.warning("personal daily failed for %s: %s", user_id, e)
        return None
    if not (body or "").strip():
        return None
    return texts.DAILY_HEADER_PERSONAL.format(card=card["name"]) + esc(body)


async def weekly_topup_job(bot: Bot) -> None:
    """Недельное пополнение бесплатных раскладов. Сначала запоминаем, у кого
    было пусто (им и скажем), потом начисляем."""
    if config.FREE_WEEKLY_TOPUP <= 0 or config.FREE_TOPUP_CAP <= 0:
        return
    targets = (await db.topup_notify_targets(config.TOPUP_NOTIFY_DAYS)
               if config.TOPUP_NOTIFY_DAYS > 0 else [])
    n = await db.weekly_topup(config.FREE_WEEKLY_TOPUP, config.FREE_TOPUP_CAP)
    log.info("weekly topup: пополнено %s, напоминаем %s", n, len(targets))

    for r in targets:
        try:
            await send_mood_photo(bot, r["user_id"], r["user_id"], f"topup:{_today_msk()}")
            await bot.send_message(
                r["user_id"],
                texts.TOPUP_GRANTED.format(
                    name=esc(r["name"]), weekday=texts.topup_weekday()),
                reply_markup=kb.to_reading(),
            )
        except Exception:  # noqa: BLE001 — заблокировали бота и т.п.
            pass
        # Гасим «напоминание о неиспользованных бесплатных»: иначе через час
        # nudge_job напишет тому же человеку почти то же самое второй раз
        await db.mark_nudge_sent(r["user_id"])
        await asyncio.sleep(0.05)


async def daily_card_job(bot: Bot) -> None:
    """Утренняя карта дня всем подписанным. Карта — одна на всех, общий текст
    генерируется один раз за утро. Подписчицам — личный текст по их теме."""
    today = _today_msk()
    users = await db.daily_optin_users()
    if not users:
        return

    card = cards.daily_card(today)
    try:
        body = await llm.chat(prompts.build_daily_messages(card),
                              max_tokens=350, temperature=0.9)
    except llm.LLMError as e:
        log.error("daily card LLM failed: %s", e)
        body = card["essence"]

    text = texts.DAILY_HEADER.format(card=card["name"]) + esc(body)

    # Картинка карты — одна на всех. Первому шлём по URL, дальше переиспользуем
    # file_id из ответа Telegram, чтобы не тянуть URL каждому подписчику.
    photo_url = config.card_img_url(card["id"])
    photo_ref = photo_url or None

    sent = 0
    tried = done = fails = 0
    for row in users:
        if row["last_daily_date"] == today:
            continue  # уже вытянула сама сегодня

        # Личный текст — только активным подписчицам. Предохранители считают
        # ПОПЫТКИ, а не удачи: если провайдер лежит, мы не должны молотить
        # запросы по всей базе и задерживать рассылку остальным.
        body_text = text
        if (config.DAILY_PERSONAL and db.sub_active(row)
                and tried < config.DAILY_PERSONAL_LIMIT and fails < 3):
            personal = await personal_daily_text(
                row["user_id"],
                row["display_name"] or row["first_name"] or "дорогая",
                card,
            )
            if personal == "":
                pass          # нечего персонализировать — ни попытка, ни сбой
            elif personal:
                tried += 1
                done += 1
                fails = 0
                body_text = personal
            else:
                tried += 1
                fails += 1    # реальный сбой: три подряд — выключаем на утро

        # Первое утро: человек карту дня не заказывал (теперь она включена по
        # умолчанию), поэтому сразу говорим, что это регулярно, и даём выход.
        first_morning = not row["last_daily_date"]

        res = await db.record_daily(row["user_id"], today, card["id"],
                                    config.STREAK_REWARD_DAYS)
        extra = texts.streak_line(res["streak"], res["best"])
        if res["reward"]:
            extra += texts.STREAK_REWARD.format(days=texts.days_phrase(res["streak"]))
        if first_morning:
            extra += texts.DAILY_FIRST_NOTE
        try:
            if photo_ref:
                try:
                    msg = await bot.send_photo(row["user_id"], photo_ref)
                    if msg.photo and photo_ref == photo_url:
                        photo_ref = msg.photo[-1].file_id  # закешировали file_id
                except Exception:  # noqa: BLE001 — картинка не критична
                    pass
            await bot.send_message(row["user_id"], body_text + extra,
                                   reply_markup=kb.daily_morning())
            sent += 1
        except Exception:  # noqa: BLE001 — заблокировали бота и т.п.
            pass
        await asyncio.sleep(0.05)
    if fails >= 3:
        log.warning("персональные карты дня отключены на это утро: 3 сбоя подряд")
    log.info("daily card sent to %s users (личных: %s из %s попыток)",
             sent, done, tried)


async def week_serial_job(bot: Bot) -> None:
    """«Неделя»-сериал: каждое утро — кусочек её же недельного расклада.
    Текст уже сгенерирован в момент расклада, нейросеть не тратится."""
    today = _today_msk()
    rows = await db.due_week_serials(today)
    sent = 0
    for r in rows:
        if r["day_date"] != today:
            # Пропущенные дни (бот был выключен и т.п.) — не шлём задним числом
            await db.mark_week_serial_sent(r["id"])
            continue
        text = texts.WEEK_SERIAL_MSG.format(
            day=esc(r["day_label"]), card=esc(r["card_name"] or ""),
            body=esc(r["body"]),
        )
        if r["is_last"]:
            text += texts.WEEK_SERIAL_LAST
        try:
            await bot.send_message(
                r["user_id"], text,
                reply_markup=kb.to_reading() if r["is_last"] else None,
            )
            sent += 1
        except Exception:  # noqa: BLE001 — заблокировали бота и т.п.
            pass
        finally:
            await db.mark_week_serial_sent(r["id"])
        await asyncio.sleep(0.05)
    if sent:
        log.info("week serial sent to %s users", sent)


async def followup_job(bot: Bot) -> None:
    """Через FOLLOWUP_DAYS дней после последнего расклада — тёплое «как всё сложилось?».

    Задача крутится каждый час, поэтому письмо может выпасть на ночь. Держим его
    в дневном окне: ночной пуш ловит блокировку надёжнее любого спама."""
    hour = datetime.now(ZoneInfo(config.TIMEZONE)).hour
    if not config.FOLLOWUP_HOUR_FROM <= hour < config.FOLLOWUP_HOUR_TO:
        return
    rows = await db.due_followups(config.FOLLOWUP_DAYS)
    for r in rows:
        try:
            first_card = "карта"
            try:
                parsed = json.loads(r["cards"] or "[]")
                if parsed:
                    first_card = parsed[0]["name"]
            except (ValueError, KeyError, TypeError):
                pass
            await send_mood_photo(bot, r["user_id"], r["user_id"], f"followup:{r['id']}")
            await bot.send_message(
                r["user_id"],
                texts.FOLLOWUP.format(
                    name=esc(r["name"]), topic=esc(r["topic"] or "твою ситуацию"),
                    card=esc(first_card),
                ),
            )
        except Exception:  # noqa: BLE001
            pass
        finally:
            # Помечаем в любом случае, чтобы не долбить заблокировавших
            await db.mark_followup_sent(r["id"])
        await asyncio.sleep(0.05)


async def nudge_job(bot: Bot) -> None:
    """Одно напоминание тем, у кого остались бесплатные расклады, но кто пропал
    на NUDGE_DAYS дней. Шлём один раз за всю жизнь пользователя — не спамим."""
    rows = await db.due_nudges(config.NUDGE_DAYS)
    sent = 0
    for r in rows:
        try:
            await send_mood_photo(bot, r["user_id"], r["user_id"], "nudge")
            await bot.send_message(
                r["user_id"],
                texts.NUDGE_FREE.format(
                    name=esc(r["name"]),
                    left=texts.free_left_phrase(r["free_readings_left"]),
                ),
                reply_markup=kb.to_reading(),
            )
            sent += 1
        except Exception:  # noqa: BLE001 — заблокировали бота и т.п.
            pass
        finally:
            # Помечаем в любом случае, чтобы не долбить заблокировавших
            await db.mark_nudge_sent(r["user_id"])
        await asyncio.sleep(0.05)
    if sent:
        log.info("nudge sent to %s users", sent)


def setup(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(config.TIMEZONE))
    scheduler.add_job(daily_card_job, "cron", hour=config.DAILY_HOUR, minute=0,
                      args=[bot], id="daily_card")
    # «Неделя»-сериал — следом за картой дня, чтобы утро складывалось в ритуал
    scheduler.add_job(week_serial_job, "cron", hour=config.DAILY_HOUR, minute=2,
                      args=[bot], id="week_serial")
    scheduler.add_job(followup_job, "interval", hours=1, args=[bot], id="followups")
    if config.FREE_WEEKLY_TOPUP > 0 and config.FREE_TOPUP_CAP > 0:
        # Недельное пополнение бесплатных раскладов — раз в неделю, днём
        scheduler.add_job(
            weekly_topup_job, "cron",
            day_of_week=config.TOPUP_WEEKDAY, hour=config.TOPUP_HOUR, minute=0,
            args=[bot], id="weekly_topup",
        )
    if config.NUDGE_DAYS > 0:
        # Днём, чтобы не будить: 12:00 по Москве
        scheduler.add_job(nudge_job, "cron", hour=12, minute=0, args=[bot], id="nudges")
    scheduler.start()
    return scheduler
