"""Фоновые задачи: карта дня в 9:00 по Москве и возвращающее сообщение
через N дней после расклада."""

import asyncio
import json
import logging
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


async def daily_card_job(bot: Bot) -> None:
    """Утренняя карта дня всем подписанным. Карта и текст — одни на всех,
    генерируем один раз за утро."""
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

    sent = 0
    for row in users:
        if row["last_daily_date"] == today:
            continue  # уже вытянула сама сегодня
        res = await db.record_daily(row["user_id"], today, card["id"],
                                    config.STREAK_REWARD_DAYS)
        extra = texts.streak_line(res["streak"], res["best"])
        if res["reward"]:
            extra += texts.STREAK_REWARD.format(days=texts.days_phrase(res["streak"]))
        try:
            await bot.send_message(row["user_id"], text + extra)
            sent += 1
        except Exception:  # noqa: BLE001 — заблокировали бота и т.п.
            pass
        await asyncio.sleep(0.05)
    log.info("daily card sent to %s users", sent)


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
    """Через FOLLOWUP_DAYS дней после последнего расклада — тёплое «как всё сложилось?»."""
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
    if config.NUDGE_DAYS > 0:
        # Днём, чтобы не будить: 12:00 по Москве
        scheduler.add_job(nudge_job, "cron", hour=12, minute=0, args=[bot], id="nudges")
    scheduler.start()
    return scheduler
