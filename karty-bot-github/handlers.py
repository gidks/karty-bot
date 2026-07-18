"""Все обработчики бота: онбординг, расклад (в т.ч. выбор карт в Mini App),
диалог после расклада, карта дня, пейволл и оплата (Stars + ЮKassa),
рефералы, админка."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardRemove,
)

import cards
import config
import database as db
import keyboards as kb
import llm
import payments
import prompts
import texts
from texts import esc

log = logging.getLogger(__name__)
router = Router()

# Пользователи, для которых прямо сейчас генерируется расклад (защита от даблкликов)
BUSY: set[int] = set()


# ---------- Состояния ----------

class Onboarding(StatesGroup):
    waiting_name = State()


class Reading(StatesGroup):
    waiting_question = State()
    waiting_cards = State()   # ждём, пока вытянет карты в Mini App
    in_dialogue = State()


class AdminCast(StatesGroup):
    waiting_message = State()
    confirming = State()


# ---------- Хелперы ----------

async def _ack(call: CallbackQuery, text: str | None = None) -> None:
    """Ответ на нажатие кнопки. Если кнопку нажали, пока бот был выключен,
    Telegram отвечает «query is too old» — глотаем и обрабатываем нажатие дальше."""
    try:
        await call.answer(text)
    except TelegramBadRequest:
        pass


def _display_name(row) -> str:
    return row["display_name"] or row["first_name"] or "дорогая"


def _msk(dt_iso: str | None) -> str | None:
    if not dt_iso:
        return None
    try:
        dt = datetime.fromisoformat(dt_iso)
        if dt <= datetime.now(timezone.utc):
            return None
        return dt.astimezone(ZoneInfo(config.TIMEZONE)).strftime("%d.%m %H:%M")
    except ValueError:
        return None


def _today_msk() -> str:
    return datetime.now(ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d")


async def _show_menu(message: Message, text: str | None = None) -> None:
    await message.answer(text or texts.MENU_TITLE, reply_markup=kb.main_menu())


def _past_block(rows) -> str | None:
    """Краткое резюме прошлых раскладов для промпта («память» бота)."""
    items = []
    for r in rows:
        try:
            names = ", ".join(
                c["name"] + (" (перевёрнутая)" if c.get("rev") else "")
                for c in json.loads(r["cards"] or "[]")
            )
        except (ValueError, TypeError, KeyError):
            names = ""
        date = (r["created_at"] or "")[:10]
        q = (r["question"] or "").strip()[:150]
        items.append(f"— {date}, тема «{r['topic'] or '—'}», вопрос: «{q}». Карты: {names}")
    return "\n".join(items) if items else None


async def _typing_while(bot: Bot, chat_id: int, coro):
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


async def _ensure_user(message: Message) -> object | None:
    """Возвращает строку пользователя; если человека нет в базе — начинает онбординг."""
    row = await db.get_user(message.from_user.id)
    if row is None:
        await db.create_user(
            message.from_user.id, message.from_user.username,
            message.from_user.first_name, None, None,
        )
        row = await db.get_user(message.from_user.id)
    return row


# ---------- /start, онбординг ----------

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id
    row = await db.get_user(uid)

    if row is None:
        referrer_id = None
        source = None
        args = (command.args or "").strip()
        if args.startswith("ref_") and args[4:].isdigit():
            candidate = int(args[4:])
            if candidate != uid:
                referrer_id = candidate
        elif args:
            source = args[:64]
        await db.create_user(
            uid, message.from_user.username, message.from_user.first_name,
            referrer_id, source,
        )
        row = await db.get_user(uid)

    await db.touch(uid, message.from_user.username)

    if row["display_name"]:
        await message.answer(
            texts.WELCOME_BACK.format(name=esc(row["display_name"])),
            reply_markup=kb.main_menu(),
        )
        return

    await state.set_state(Onboarding.waiting_name)
    await message.answer(
        texts.WELCOME_NEW.format(free=config.FREE_READINGS),
        reply_markup=kb.name_default(esc(message.from_user.first_name or "")),
    )


@router.callback_query(F.data == "name_default")
async def cb_name_default(call: CallbackQuery, state: FSMContext) -> None:
    await _ack(call)
    name = (call.from_user.first_name or "Гостья").strip()[:30]
    await db.set_display_name(call.from_user.id, name)
    await state.clear()
    await call.message.answer(
        texts.NAME_SAVED.format(name=esc(name)), reply_markup=kb.main_menu())


@router.message(Onboarding.waiting_name, F.text, ~F.text.startswith("/"))
async def onboarding_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if not name or len(name) > 30:
        await message.answer(texts.NAME_TOO_LONG)
        return
    await db.set_display_name(message.from_user.id, name)
    await state.clear()
    await message.answer(
        texts.NAME_SAVED.format(name=esc(name)), reply_markup=kb.main_menu())


# ---------- Меню, помощь ----------

@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _ensure_user(message)
    await _show_menu(message)


@router.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery, state: FSMContext) -> None:
    await _ack(call)
    await state.clear()
    await call.message.answer(texts.MENU_TITLE, reply_markup=kb.main_menu())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        texts.HELP.format(free=config.FREE_READINGS, support=texts.SUPPORT_CONTACT),
        reply_markup=kb.to_menu(),
    )


@router.callback_query(F.data == "help")
async def cb_help(call: CallbackQuery) -> None:
    await _ack(call)
    await call.message.answer(
        texts.HELP.format(free=config.FREE_READINGS, support=texts.SUPPORT_CONTACT),
        reply_markup=kb.to_menu(),
    )


# ---------- Админ-команды (регистрируем раньше диалоговых состояний) ----------

@router.message(Command("stats"), F.from_user.id.in_(config.ADMIN_IDS))
async def cmd_stats(message: Message) -> None:
    s = await db.stats_snapshot()
    sources = "\n".join(f"· {esc(src)}: {n}" for src, n in s["sources"]) or "—"
    await message.answer(texts.ADMIN_STATS.format(
        users=s["users"], users_24h=s["users_24h"], users_7d=s["users_7d"],
        readings=s["readings"], readings_24h=s["readings_24h"],
        pay_count=s["pay_count"], rub=s["rub"], stars=s["stars"],
        subs=s["subs"], daily=s["daily"], sources=sources,
        rate_up=s["rate_up"], rate_down=s["rate_down"],
    ))


@router.message(Command("broadcast"), F.from_user.id.in_(config.ADMIN_IDS))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    await state.set_state(AdminCast.waiting_message)
    await message.answer(texts.BROADCAST_ASK)


# ---------- Расклад: выбор темы и вопрос ----------

@router.callback_query(F.data == "new_reading")
async def cb_new_reading(call: CallbackQuery, state: FSMContext) -> None:
    await _ack(call)
    await state.clear()
    uid = call.from_user.id
    ok, _src = await db.readings_available(uid)
    if not ok:
        row = await db.get_user(uid)
        await call.message.answer(
            texts.PAYWALL.format(
                name=esc(_display_name(row)), free=config.FREE_READINGS,
                p_single=config.PRICE_SINGLE_RUB, p_week=config.PRICE_WEEK_RUB,
                p_month=config.PRICE_MONTH_RUB,
            ),
            reply_markup=kb.plans(),
        )
        return
    await call.message.answer(texts.SPREAD_MENU, reply_markup=kb.spreads())


@router.callback_query(F.data.startswith("spread:"))
async def cb_spread(call: CallbackQuery, state: FSMContext) -> None:
    await _ack(call)
    key = call.data.split(":", 1)[1]
    if key != "classic" and key not in texts.SPREAD_TITLES:
        return
    if config.MINIAPP_URL:
        # Вопрос и тема задаются прямо в Mini App (payload v2) — сразу к колоде
        await state.set_state(Reading.waiting_cards)
        await state.update_data(spread=key, topic_key=None, question="")
        await call.message.answer(
            texts.DRAW_MINIAPP, reply_markup=kb.draw_cards_webapp(key))
        return
    if key == "classic":
        await call.message.answer(texts.ASK_TOPIC, reply_markup=kb.topics())
        return
    await state.set_state(Reading.waiting_question)
    await state.update_data(spread=key, topic_key=None)
    await call.message.answer(texts.ASK_SPREAD_QUESTION[key])


@router.callback_query(F.data.startswith("topic:"))
async def cb_topic(call: CallbackQuery, state: FSMContext) -> None:
    await _ack(call)
    topic_key = call.data.split(":", 1)[1]
    if topic_key not in texts.ASK_QUESTION:
        return
    await state.set_state(Reading.waiting_question)
    await state.update_data(topic_key=topic_key, spread="classic")
    await call.message.answer(texts.ASK_QUESTION[topic_key])


# Сколько карт тянется в каждом раскладе (по умолчанию 3)
SPREAD_CARDS: dict[str, int] = {"yesno": 1, "week": 7}


def _spread_n(spread: str) -> int:
    return SPREAD_CARDS.get(spread, 3)


def _parse_webapp_payload(raw: str, spread: str) -> tuple[list[dict], str, str | None] | None:
    """Данные из Mini App.
    v1: '{"v":1,"spread":...,"cards":[{"id":..,"rev":0|1}]}'
    v2: то же + "q" (вопрос, набранный в аппе) и "topic" (тема для классики).
    Возвращает (карты, вопрос, topic_key). Любая ошибка формата -> None
    (тогда бот вытянет сам)."""
    try:
        payload = json.loads(raw)
        chosen = payload["cards"]
        need = _spread_n(spread)
        ids = [int(c["id"]) for c in chosen]
        if len(ids) != need or len(set(ids)) != len(ids):
            return None
        if any(i < 0 or i > 77 for i in ids):
            return None
        by_id = {c["id"]: c for c in cards.CARDS}
        drawn = [{**by_id[int(c["id"])], "rev": bool(c.get("rev"))} for c in chosen]
        q = str(payload.get("q") or "").strip()[:500]
        topic = payload.get("topic")
        topic_key = topic if isinstance(topic, str) and topic in texts.TOPIC_TITLES else None
        return drawn, q, topic_key
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


@router.message(Reading.waiting_question, F.text, ~F.text.startswith("/"))
async def reading_question(message: Message, state: FSMContext, bot: Bot) -> None:
    question = message.text.strip()
    if len(question) < 5:
        await message.answer(texts.QUESTION_TOO_SHORT)
        return

    if message.from_user.id in BUSY:
        await message.answer(texts.BUSY)
        return

    if not await _check_readings_available(message, state):
        return

    data = await state.get_data()
    if config.MINIAPP_URL and not data.get("no_app"):
        spread = data.get("spread", "classic")
        await state.set_state(Reading.waiting_cards)
        await state.update_data(question=question)
        await message.answer(texts.DRAW_MINIAPP, reply_markup=kb.draw_cards_webapp(spread))
        return

    await _do_reading(message, state, bot, question, drawn=None)


@router.message(Reading.waiting_cards, F.web_app_data)
async def webapp_cards(message: Message, state: FSMContext, bot: Bot) -> None:
    """Пользовательница вытянула карты в Mini App (вопрос и тема — в payload v2)."""
    data = await state.get_data()
    spread = data.get("spread", "classic")
    parsed = _parse_webapp_payload(message.web_app_data.data, spread)
    if parsed is None:
        drawn, q, topic_key = None, "", None
    else:
        drawn, q, topic_key = parsed
    question = (q or data.get("question", "")).strip()
    if topic_key and spread == "classic":
        await state.update_data(topic_key=topic_key)
    await _do_reading(message, state, bot, question, drawn)


@router.message(Reading.waiting_cards, F.text, ~F.text.startswith("/"))
async def webapp_fallback(message: Message, state: FSMContext, bot: Bot) -> None:
    """Кнопка не сработала / написала текст вместо Mini App.
    Длинный текст считаем вопросом и тянем карты сами; короткий («сам») —
    спрашиваем вопрос в чате и дальше работаем без Mini App."""
    data = await state.get_data()
    text = (message.text or "").strip()
    stored_q = (data.get("question") or "").strip()
    if stored_q:
        # Вопрос уже был задан в чате (старый путь) — просто тянем сами
        await _do_reading(message, state, bot, stored_q, drawn=None)
        return
    if len(text) >= 5:
        await _do_reading(message, state, bot, text[:500], drawn=None)
        return
    # «сам» и прочие короткие ответы: продолжаем в чате без Mini App
    spread = data.get("spread", "classic")
    await state.set_state(Reading.waiting_question)
    await state.update_data(no_app=True)
    if spread == "classic":
        prompt = texts.ASK_QUESTION.get(
            data.get("topic_key") or "own", texts.ASK_QUESTION["own"])
    else:
        prompt = texts.ASK_SPREAD_QUESTION.get(spread, texts.ASK_QUESTION["own"])
    await message.answer(prompt, reply_markup=ReplyKeyboardRemove())


async def _check_readings_available(message: Message, state: FSMContext) -> bool:
    """Есть ли расклады; если нет — показывает пейволл."""
    ok, _ = await db.readings_available(message.from_user.id)
    if ok:
        return True
    await state.clear()
    row = await db.get_user(message.from_user.id)
    await message.answer(
        texts.PAYWALL.format(
            name=esc(_display_name(row)), free=config.FREE_READINGS,
            p_single=config.PRICE_SINGLE_RUB, p_week=config.PRICE_WEEK_RUB,
            p_month=config.PRICE_MONTH_RUB,
        ),
        reply_markup=kb.plans(),
    )
    return False


async def _do_reading(
    message: Message, state: FSMContext, bot: Bot,
    question: str, drawn: list[dict] | None,
) -> None:
    """Генерация расклада. drawn=None — бот тянет карты сам,
    иначе — карты, выбранные в Mini App."""
    uid = message.from_user.id
    if uid in BUSY:
        await message.answer(texts.BUSY)
        return

    ok, source = await db.readings_available(uid)
    if not ok:
        await _check_readings_available(message, state)
        return

    data = await state.get_data()
    spread = data.get("spread", "classic")
    if spread == "classic":
        topic_key = data.get("topic_key", "own")
        topic_title = texts.TOPIC_TITLES.get(topic_key, "Свой вопрос")
    else:
        topic_title = texts.SPREAD_TITLES.get(spread, "Расклад")
    row = await db.get_user(uid)
    name = _display_name(row)

    BUSY.add(uid)
    try:
        await message.answer(
            texts.DRAWING_WEBAPP if drawn else texts.DRAWING,
            reply_markup=ReplyKeyboardRemove(),
        )
        await bot.send_chat_action(message.chat.id, "typing")

        # «Память»: прошлые расклады ещё не включают текущий — он сохраняется позже
        past = _past_block(await db.last_readings(uid, 3))

        if drawn is None:
            drawn = cards.draw(_spread_n(spread))

        # Ритуал: сначала карты ложатся на стол, разбор приходит отдельно
        await asyncio.sleep(0.8)
        header = texts.READING_HEADER_ONE if len(drawn) == 1 else texts.READING_HEADER
        await message.answer(header.format(cards=texts.cards_list_html(
            drawn, prompts.spread_labels(spread))).rstrip())

        try:
            reading_text = await _typing_while(
                bot, message.chat.id,
                llm.chat(
                    prompts.build_reading_messages(
                        name, topic_title, question, drawn, past, spread),
                    max_tokens=(400 if spread == "yesno"
                                else 1300 if spread == "week" else 1000),
                    temperature=0.9,
                ),
            )
        except llm.LLMError:
            await message.answer(texts.ERROR_LLM, reply_markup=kb.to_menu())
            return

        # Списываем расклад только после успешной генерации
        await db.consume_reading(uid, source)
        reading_id, total = await db.add_reading(
            uid, topic_title, question, drawn, reading_text)

        await message.answer(esc(reading_text) + texts.AFTER_READING,
                             reply_markup=kb.after_reading(reading_id))

        await state.set_state(Reading.in_dialogue)
        await state.update_data(
            topic=topic_title, question=question,
            drawn=[{"id": c["id"], "name": c["name"], "rev": c["rev"]} for c in drawn],
            reading_text=reading_text, history=[], rounds=0,
        )

        # Реферальный бонус — после первого расклада приглашённой
        if total == 1 and row["referrer_id"] and not row["referral_bonus_given"]:
            await db.mark_referral_bonus_given(uid)
            await db.add_free_readings(row["referrer_id"], 1)
            try:
                await bot.send_message(row["referrer_id"], texts.REFERRAL_BONUS)
            except Exception:  # noqa: BLE001 — реферер мог заблокировать бота
                pass
    finally:
        BUSY.discard(uid)


# ---------- Диалог после расклада ----------

@router.message(Reading.in_dialogue, F.text, ~F.text.startswith("/"))
async def dialogue(message: Message, state: FSMContext, bot: Bot) -> None:
    uid = message.from_user.id
    if uid in BUSY:
        await message.answer(texts.BUSY)
        return

    data = await state.get_data()
    row = await db.get_user(uid)
    rounds = data.get("rounds", 0) + 1
    # Лимит реплик — только без активной подписки; подписчицы говорят без лимита
    if rounds > config.DIALOGUE_MAX and not (row and db.sub_active(row)):
        await state.clear()
        await message.answer(texts.DIALOGUE_LIMIT, reply_markup=kb.after_reading())
        return

    history = data.get("history", [])
    history.append({"role": "user", "content": message.text.strip()[:1000]})
    history = history[-10:]

    drawn = [
        {**(cards.by_id(c["id"]) or {"name": c["name"], "essence": "", "upright": "",
                                     "reversed": ""}), "rev": c["rev"]}
        for c in data.get("drawn", [])
    ]

    BUSY.add(uid)
    try:
        try:
            reply = await _typing_while(
                bot, message.chat.id,
                llm.chat(
                    prompts.build_dialogue_messages(
                        _display_name(row), data.get("topic", ""), data.get("question", ""),
                        drawn, data.get("reading_text", ""), history,
                    ),
                    max_tokens=450, temperature=0.8,
                ),
            )
        except llm.LLMError:
            await message.answer(texts.ERROR_LLM, reply_markup=kb.after_reading())
            return

        history.append({"role": "assistant", "content": reply})
        await state.update_data(history=history[-10:], rounds=rounds)
        await message.answer(esc(reply), reply_markup=kb.after_reading())
    finally:
        BUSY.discard(uid)


# ---------- Карта дня ----------

@router.callback_query(F.data == "daily_card")
async def cb_daily(call: CallbackQuery, bot: Bot) -> None:
    await _ack(call)
    uid = call.from_user.id
    row = await db.get_user(uid)
    if row is None:
        return
    today = _today_msk()
    opted = bool(row["daily_opt_in"])

    if row["last_daily_date"] == today:
        await call.message.answer(texts.DAILY_ALREADY, reply_markup=kb.daily_toggle(opted))
        return

    card = cards.daily_card(today)
    await bot.send_chat_action(call.message.chat.id, "typing")
    try:
        text = await llm.chat(prompts.build_daily_messages(card), max_tokens=350,
                              temperature=0.9)
    except llm.LLMError:
        text = card["essence"]

    res = await db.record_daily(uid, today, card["id"], config.STREAK_REWARD_DAYS)
    extra = texts.streak_line(res["streak"], res["best"])
    if res["reward"]:
        extra += texts.STREAK_REWARD.format(days=texts.days_phrase(res["streak"]))
    body = texts.DAILY_HEADER.format(card=card["name"]) + esc(text) + extra
    if opted:
        await call.message.answer(body, reply_markup=kb.daily_toggle(True))
    else:
        await call.message.answer(body)
        await call.message.answer(texts.DAILY_OFFER, reply_markup=kb.daily_toggle(False))


@router.callback_query(F.data.startswith("daily_sub:"))
async def cb_daily_sub(call: CallbackQuery) -> None:
    await _ack(call)
    on = call.data.endswith(":on")
    await db.set_daily_opt(call.from_user.id, on)
    await call.message.answer(
        texts.DAILY_SUB_ON if on else texts.DAILY_SUB_OFF,
        reply_markup=kb.to_menu(),
    )


# ---------- Коллекция карт ----------

_COLLECTION_GROUPS = [
    ("✨ Старшие арканы", 0, 21),
    ("🪄 Жезлы", 22, 35),
    ("🍷 Кубки", 36, 49),
    ("🗡 Мечи", 50, 63),
    ("🪙 Пентакли", 64, 77),
]


@router.callback_query(F.data == "collection")
async def cb_collection(call: CallbackQuery) -> None:
    await _ack(call)
    uid = call.from_user.id
    col = await db.collection(uid)
    seen = col["seen"]
    if not seen:
        await call.message.answer(texts.COLLECTION_EMPTY, reply_markup=kb.collection_menu())
        return

    total = len(cards.CARDS)
    lines = [
        f"Встречено <b>{len(seen)} из {total}</b>",
        texts.collection_bar(len(seen), total) + f" {round(100 * len(seen) / total)}%",
        "",
    ]
    for title, lo, hi in _COLLECTION_GROUPS:
        got = sum(1 for i in seen if lo <= i <= hi)
        lines.append(f"{title} — {got}/{hi - lo + 1}")

    recent_names = [c["name"] for c in
                    (cards.by_id(i) for i in col["recent"]) if c]
    if recent_names:
        lines += ["", "Новые: " + ", ".join(recent_names)]

    row = await db.get_user(uid)
    if row and (row["daily_streak"] or 0) > 1:
        lines.append(
            f"🔥 Серия карты дня: {texts.days_phrase(row['daily_streak'])}"
            + (f" (рекорд — {texts.days_phrase(row['best_streak'])})"
               if row["best_streak"] > row["daily_streak"] else "")
        )

    tail = (texts.COLLECTION_DONE if len(seen) >= total
            else texts.COLLECTION_FOOTER.format(left=total - len(seen)))
    await call.message.answer(
        texts.COLLECTION_TITLE + "\n".join(lines) + tail,
        reply_markup=kb.collection_menu(),
    )


# ---------- Оценка расклада ----------

@router.callback_query(F.data.startswith("rate:"))
async def cb_rate(call: CallbackQuery) -> None:
    parts = call.data.split(":")
    if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in {"up", "down"}:
        await _ack(call)
        return
    value = 1 if parts[2] == "up" else -1
    await db.set_reading_rating(int(parts[1]), call.from_user.id, value)
    await _ack(call, texts.RATE_THANKS_UP if value == 1 else texts.RATE_THANKS_DOWN)
    try:
        # Убираем кнопки оценки, оставляем «Новый расклад / В меню»
        await call.message.edit_reply_markup(reply_markup=kb.after_reading())
    except Exception:  # noqa: BLE001 — сообщение могло устареть
        pass


# ---------- Мои расклады ----------

def _short_date(iso: str | None, fmt: str = "%d.%m") -> str:
    if not iso:
        return "—"
    try:
        return (datetime.fromisoformat(iso)
                .astimezone(ZoneInfo(config.TIMEZONE)).strftime(fmt))
    except ValueError:
        return iso[:10]


@router.callback_query(F.data == "my_readings")
async def cb_my_readings(call: CallbackQuery) -> None:
    await _ack(call)
    h = await db.history(call.from_user.id)
    if not h["total"]:
        await call.message.answer(texts.MY_READINGS_EMPTY, reply_markup=kb.to_reading())
        return

    lines = [f"С картами с {_short_date(h['first_date'])} · {texts.readings_phrase(h['total'])}"]
    top_card_name, top_card_n = None, 0
    if h["top_card"]:
        card = cards.by_id(h["top_card"][0])
        top_card_n = h["top_card"][1]
        if card and top_card_n >= 2:
            top_card_name = card["name"]
            lines.append(
                f"Чаще всего выпадает: <b>{esc(top_card_name)}</b> — "
                + texts.times_phrase(top_card_n))
    if h["top_topic"] and h["top_topic"][1] >= 2:
        lines.append(f"Любимая тема: {esc(h['top_topic'][0])}")
    if h["rate_up"]:
        lines.append(f"Попаданий 👍: {h['rate_up']}")

    import json as _json
    items = []
    for r in h["last"]:
        try:
            names = ", ".join(c["name"] for c in _json.loads(r["cards"] or "[]"))
        except (ValueError, TypeError, KeyError):
            names = "—"
        mark = " 👍" if r["rating"] == 1 else (" 👎" if r["rating"] == -1 else "")
        items.append(
            f"🃏 {_short_date(r['created_at'])} · <b>{esc(r['topic'] or '')}</b>"
            f" — {esc(names)}{mark}")
    lines += ["", "<b>Последние:</b>"] + items

    body = texts.HISTORY_TITLE + "\n".join(lines)
    if top_card_name and top_card_n >= 3:
        body += texts.HISTORY_TOP_CARD_NOTE.format(
            card=esc(top_card_name), n=top_card_n)
    body += texts.HISTORY_FOOTER
    await call.message.answer(body, reply_markup=kb.to_reading())


# ---------- Реферальная ссылка ----------

@router.callback_query(F.data == "share")
async def cb_share(call: CallbackQuery) -> None:
    await _ack(call)
    link = f"https://t.me/{config.BOT_USERNAME}?start=ref_{call.from_user.id}"
    await call.message.answer(
        texts.SHARE_TEXT.format(free=config.FREE_READINGS, link=link))
    await call.message.answer(texts.SHARE_INSTRUCTION, reply_markup=kb.to_menu())


# ---------- Тарифы и оплата ----------

@router.callback_query(F.data == "tariffs")
async def cb_tariffs(call: CallbackQuery) -> None:
    await _ack(call)
    row = await db.get_user(call.from_user.id)
    if row is None:
        return
    left = texts.balance_line(
        row["free_readings_left"], row["paid_readings_left"],
        _msk(row["subscription_until"]),
    )
    await call.message.answer(
        texts.TARIFFS.format(
            p_single=config.PRICE_SINGLE_RUB, p_week=config.PRICE_WEEK_RUB,
            p_month=config.PRICE_MONTH_RUB, left=left,
        ),
        reply_markup=kb.plans(),
    )


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(call: CallbackQuery) -> None:
    await _ack(call)
    plan_key = call.data.split(":", 1)[1]
    plan = config.PLANS.get(plan_key)
    if not plan:
        return
    await call.message.answer(
        texts.CHOOSE_PAY_METHOD.format(
            plan=plan["title"], price=f"{plan['rub']} ₽ / {plan['stars']} ⭐"),
        reply_markup=kb.pay_methods(plan_key),
    )


@router.callback_query(F.data.startswith("pay:stars:"))
async def cb_pay_stars(call: CallbackQuery, bot: Bot) -> None:
    await _ack(call)
    plan_key = call.data.split(":")[2]
    plan = config.PLANS.get(plan_key)
    if not plan:
        return
    await bot.send_invoice(
        chat_id=call.message.chat.id,
        title=f"Карты Не Врут — {plan['title']}",
        description=texts.STARS_DESC.format(plan=plan["title"]),
        payload=f"stars:{plan_key}:{call.from_user.id}",
        currency="XTR",
        prices=[LabeledPrice(label=plan["title"], amount=plan["stars"])],
    )


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def stars_paid(message: Message) -> None:
    sp = message.successful_payment
    parts = (sp.invoice_payload or "").split(":")
    if len(parts) != 3 or parts[0] != "stars":
        return
    plan_key = parts[1]
    if plan_key not in config.PLANS:
        return
    uid = message.from_user.id
    is_new = await db.create_payment_row(
        sp.telegram_payment_charge_id, uid, plan_key,
        float(sp.total_amount), sp.currency or "XTR", "stars", "succeeded",
    )
    if is_new:
        await db.apply_purchase(uid, plan_key)
        await message.answer(texts.PAY_SUCCESS[plan_key], reply_markup=kb.main_menu())
    else:
        await message.answer(texts.PAY_ALREADY, reply_markup=kb.main_menu())


@router.callback_query(F.data.startswith("pay:card:"))
async def cb_pay_card(call: CallbackQuery) -> None:
    await _ack(call)
    plan_key = call.data.split(":")[2]
    plan = config.PLANS.get(plan_key)
    if not plan:
        return
    if not payments.available():
        await call.message.answer(texts.PAY_UNAVAILABLE,
                                  reply_markup=kb.pay_methods(plan_key))
        return
    try:
        payment_id, url = await payments.create(call.from_user.id, plan_key)
    except Exception as e:  # noqa: BLE001 — API ЮKassa может быть недоступен
        log.error("YooKassa create failed: %s", e)
        await call.message.answer(texts.PAY_UNAVAILABLE,
                                  reply_markup=kb.pay_methods(plan_key))
        return
    await db.create_payment_row(
        payment_id, call.from_user.id, plan_key, float(plan["rub"]),
        "RUB", "yookassa", "pending",
    )
    await call.message.answer(
        texts.PAY_LINK_TEXT.format(plan=plan["title"], price=plan["rub"]),
        reply_markup=kb.pay_link(url, plan["rub"], payment_id),
    )


@router.callback_query(F.data.startswith("check_pay:"))
async def cb_check_pay(call: CallbackQuery) -> None:
    payment_id = call.data.split(":", 1)[1]
    row = await db.get_payment(payment_id)
    if row is None or row["user_id"] != call.from_user.id:
        await _ack(call)
        return
    try:
        st = await payments.status(payment_id)
    except Exception as e:  # noqa: BLE001
        log.error("YooKassa status failed: %s", e)
        await _ack(call)
        await call.message.answer(texts.PAY_NOT_FOUND)
        return

    await _ack(call)
    if st == "succeeded":
        if await db.mark_succeeded_once(payment_id):
            await db.apply_purchase(row["user_id"], row["plan"])
            await call.message.answer(texts.PAY_SUCCESS[row["plan"]],
                                      reply_markup=kb.main_menu())
        else:
            await call.message.answer(texts.PAY_ALREADY, reply_markup=kb.main_menu())
    elif st == "canceled":
        await call.message.answer(texts.PAY_CANCELED, reply_markup=kb.plans())
    else:
        await call.message.answer(texts.PAY_NOT_FOUND)


# ---------- Админка: рассылка ----------

@router.message(AdminCast.waiting_message)
async def broadcast_preview(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.update_data(bc_chat=message.chat.id, bc_msg=message.message_id)
    await state.set_state(AdminCast.confirming)
    await bot.copy_message(message.chat.id, message.chat.id, message.message_id)
    count = len(await db.all_user_ids())
    await message.answer(texts.BROADCAST_PREVIEW.format(count=count),
                         reply_markup=kb.broadcast_confirm())


@router.callback_query(AdminCast.confirming, F.data == "bc_confirm")
async def broadcast_go(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await _ack(call, "Поехали")
    data = await state.get_data()
    await state.clear()
    user_ids = await db.all_user_ids()
    ok = fail = 0
    for uid in user_ids:
        try:
            await bot.copy_message(uid, data["bc_chat"], data["bc_msg"])
            ok += 1
        except Exception:  # noqa: BLE001 — заблокировали бота и т.п.
            fail += 1
        await asyncio.sleep(0.05)
    await call.message.answer(texts.BROADCAST_DONE.format(ok=ok, fail=fail))


@router.callback_query(AdminCast.confirming, F.data == "bc_cancel")
async def broadcast_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await _ack(call)
    await state.clear()
    await call.message.answer(texts.BROADCAST_CANCELED)


# ---------- Фолбэки (в самом конце — ловят всё остальное) ----------

@router.message(StateFilter(None), F.text)
async def fallback_text(message: Message) -> None:
    await _ensure_user(message)
    await message.answer(texts.FALLBACK_TEXT, reply_markup=kb.main_menu())


@router.message()
async def fallback_other(message: Message) -> None:
    await message.answer(texts.ONLY_TEXT, reply_markup=kb.to_menu())
