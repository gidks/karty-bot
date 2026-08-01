"""Движок бандлов: день 0, шаги по расписанию и закрывающее письмо.

Живёт отдельно от handlers, потому что шаг может запустить кто угодно из двух:
она сама (нажала «Просто разложи» или ответила текстом) или планировщик, когда
подошла дата. Логика при этом одна и та же, отличается только точка входа.

FSM-состояние после расклада ставим здесь же — в том числе из фоновой задачи,
у которой своего FSMContext нет: без этого её следующая реплика не попала бы
в разговор по только что пришедшему раскладу.
"""

import json
import logging

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import StorageKey

import bundles
import cards
import config
import database as db
import delivery
import keyboards as kb
import llm
import prompts
import serial
import texts
from texts import esc

log = logging.getLogger(__name__)

# Хранилище FSM и состояние «разговор после расклада» прокидываются из bot.py
# и handlers один раз при старте — иначе получился бы круговой импорт
# handlers ↔ bundle_run.
_STORAGE = None
_DIALOGUE_STATE: State | None = None


def bind(storage=None, dialogue_state: State | None = None) -> None:
    """Вызывается дважды: из handlers (состояние) и из bot.py (хранилище).
    None означает «это оставь как есть», иначе второй вызов затирал бы первый."""
    global _STORAGE, _DIALOGUE_STATE
    if storage is not None:
        _STORAGE = storage
    if dialogue_state is not None:
        _DIALOGUE_STATE = dialogue_state


async def _dialogue_context(bot: Bot, user_id: int) -> FSMContext | None:
    """FSMContext человека вне обработчика (для фоновых задач)."""
    if _STORAGE is None:
        return None
    try:
        key = StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
        return FSMContext(storage=_STORAGE, key=key)
    except Exception as e:  # noqa: BLE001 — без диалога расклад всё равно дойдёт
        log.warning("не собрала FSMContext для %s: %s", user_id, e)
        return None


async def _enter_dialogue(
    bot: Bot, user_id: int, state: FSMContext | None, *,
    topic: str, question: str, drawn: list[dict], reading_text: str,
    dlg_max: int, bundle_id: int | None = None,
) -> None:
    """Ставит человека в разговор по только что выданному раскладу."""
    ctx = state or await _dialogue_context(bot, user_id)
    if ctx is None or _DIALOGUE_STATE is None:
        return
    try:
        await ctx.set_state(_DIALOGUE_STATE)
        await ctx.update_data(
            topic=topic, question=question,
            drawn=[{"id": c["id"], "name": c["name"], "rev": c["rev"]} for c in drawn],
            reading_text=reading_text, history=[], rounds=0,
            dlg_max=dlg_max, dlg_locked=False, bundle_id=bundle_id,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("не поставила разговор для %s: %s", user_id, e)


def _intro_dict(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _question_from_intro(intro: dict) -> str:
    """Её собственные слова — идут в поле question расклада, чтобы история
    и «разбор месяца» видели, о чём был бандл."""
    parts = [str(v).strip() for v in intro.values() if str(v or "").strip()]
    return " · ".join(parts)[:1000] or "разбор без вводных"


async def _story_block(bundle_id: int) -> str:
    """Что уже было в этом разборе: расклады по порядку и её рассказы между ними.
    Именно это делает следующий шаг продолжением, а не новым раскладом."""
    rows = await db.bundle_readings(bundle_id)
    answers = await db.bundle_answers(bundle_id)
    parts: list[str] = []
    for i, r in enumerate(rows, 1):
        try:
            names = ", ".join(
                c["name"] + (" (перевёрнутая)" if c.get("rev") else "")
                for c in json.loads(r["cards"] or "[]")
            )
        except (ValueError, TypeError, KeyError):
            names = ""
        parts.append(
            f"[{i}] {(r['created_at'] or '')[:10]} — {r['topic'] or 'расклад'}. "
            f"Карты: {names}.\nЧто ты тогда сказала: {(r['reading_text'] or '')[:900]}"
        )
    for a in answers:
        parts.append(f"Её слова между раскладами: «{a[:400]}»")
    return "\n\n".join(parts)


async def _week_card_note(bundle_id: int, week_no: int) -> str | None:
    """Карта, которая стояла на этой неделе ещё в первый день месяца.
    Шаг её не открывает заново, а активирует — поэтому модель должна её знать."""
    b = await db.get_bundle(bundle_id)
    if not b or not b["day0_reading_id"]:
        return None
    rows = await db.bundle_readings(bundle_id)
    day0 = next((r for r in rows if r["id"] == b["day0_reading_id"]), None)
    if day0 is None:
        return None
    try:
        drawn = json.loads(day0["cards"] or "[]")
    except (ValueError, TypeError):
        return None
    # В раскладе month6 порядок: 0 — тема месяца, 1..4 — недели, 5 — слепое пятно
    idx = week_no
    if idx < 1 or idx >= len(drawn):
        return None
    c = drawn[idx]
    rev = " (перевёрнутая)" if c.get("rev") else ""
    return (f"Карта, которая стояла на этой неделе ещё в первый день месяца: "
            f"{c.get('name')}{rev}. Её нужно активировать, а не открывать заново.")


# ---------------------------------------------------------------- День 0

async def deliver_day0(
    bot: Bot, chat_id: int, user_id: int, bundle_row, intro: dict,
    name: str, state: FSMContext | None = None,
) -> bool:
    """Первый расклад бандла. Отдаём всё, что знаем про «сейчас», без остатка:
    день 0 должен стоить своих денег сам по себе."""
    key = bundle_row["kind"]
    b = bundles.get(key)
    if not b:
        return False
    bundle_id = bundle_row["id"]
    spread = bundles.day0_spread(key)
    n = prompts.card_count(spread)
    labels = prompts.spread_labels(spread)
    drawn = cards.draw(n)

    await bot.send_message(
        chat_id,
        texts.READING_HEADER.format(cards=texts.cards_list_html(drawn, labels)).rstrip())

    try:
        raw = await delivery.typing_while(
            bot, chat_id,
            llm.chat(
                prompts.build_bundle_reading_messages(
                    b["title"], None, name, drawn, spread,
                    intro_answers=intro,
                    with_marker=bundles.wants_marker(key),
                ),
                max_tokens=prompts.max_tokens(spread), temperature=0.9,
            ),
        )
    except llm.LLMError:
        await bot.send_message(chat_id, texts.ERROR_LLM, reply_markup=kb.to_menu())
        return False

    parsed = serial.parse(raw, n)
    clean = serial.plain_text(parsed, labels) if parsed else raw
    marker = (parsed or {}).get("marker")

    reading_id, _ = await db.add_reading(
        user_id, b["title"], _question_from_intro(intro), drawn, clean, bundle_id)

    schedule = [(i, day) for i, (day, _step) in enumerate(bundles.schedule(key))]
    await db.start_bundle(bundle_id, intro, reading_id, schedule, marker)

    if parsed is None:
        await delivery.send_plain_reading(bot, chat_id, raw, reading_id)
    else:
        await delivery.send_serial(
            bot, chat_id, spread, drawn, labels, parsed, reading_id)

    await _send_marker(bot, chat_id, key, marker, 0)
    await _enter_dialogue(
        bot, user_id, state, topic=b["title"],
        question=_question_from_intro(intro), drawn=drawn, reading_text=clean,
        dlg_max=config.DIALOGUE_MAX_BUNDLE, bundle_id=bundle_id,
    )
    return True


async def _send_marker(
    bot: Bot, chat_id: int, key: str, marker: str | None, after_step: int,
) -> None:
    """Закрытие шага: за чем смотреть до следующего раза.

    Наблюдение, а не задание — провалить его невозможно. И это единственный
    честный ответ на «а почему не сразу?»: сразу мы всё уже сказали, а через
    три дня расклад будет на фактах, которых сейчас не существует."""
    days = bundles.step_days(key)
    if after_step >= len(days):
        return
    left = days[after_step] - (days[after_step - 1] if after_step else 0)
    when = texts.when_phrase(left)
    if marker:
        await bot.send_message(
            chat_id, texts.BUNDLE_MARKER_MSG.format(marker=esc(marker), when=when))
    else:
        await bot.send_message(chat_id, texts.BUNDLE_NO_MARKER_MSG.format(when=when))


# ---------------------------------------------------------------- Шаг

async def run_step(
    bot: Bot, chat_id: int, user_id: int, step_row, answer: str | None = None,
    state: FSMContext | None = None,
) -> bool:
    """Очередной шаг разбора. Вызывается и кнопкой, и планировщиком.

    Шаг занимается атомарно (claim_step) ДО генерации: она долгая, и без этого
    следующий тик рассылки успел бы выдать тот же расклад второй раз."""
    step_id = step_row["id"]
    bundle_id = step_row["bundle_id"]
    key = step_row["kind"]          # b.kind приходит из JOIN
    step_no = step_row["step_no"]
    b = bundles.get(key)
    step = bundles.step_at(key, step_no)
    if not b or not step:
        await db.complete_step(step_id, None)
        return False

    if answer:
        await db.set_step_answer(step_id, answer)
    if not await db.claim_step(step_id):
        return False  # кто-то уже выдал этот шаг

    try:
        if step["kind"] == bundles.STEP_FINAL:
            ok = await _run_final(bot, chat_id, user_id, bundle_id, key, b)
        else:
            ok = await _run_card_step(
                bot, chat_id, user_id, bundle_id, key, b, step, step_no,
                answer or step_row["answer"], state, step_id)
    except Exception as e:  # noqa: BLE001 — шаг не должен пропасть из-за сбоя
        log.error("шаг бандла %s сорвался: %s", step_id, e)
        await db.release_step(step_id)
        raise
    if not ok:
        await db.release_step(step_id)
    return ok


async def _run_card_step(
    bot: Bot, chat_id: int, user_id: int, bundle_id: int, key: str, b: dict,
    step: dict, step_no: int, answer: str | None, state: FSMContext | None,
    step_id: int,
) -> bool:
    spread = step["spread"]
    n = prompts.card_count(spread)
    labels = prompts.spread_labels(spread)
    drawn = cards.draw(n)

    bundle = await db.get_bundle(bundle_id)
    intro = _intro_dict(bundle["intro"] if bundle else None)
    marker = bundle["marker"] if bundle else None
    name = await _name_of(user_id)
    story = await _story_block(bundle_id)
    week_note = (await _week_card_note(bundle_id, step.get("week", 0))
                 if step["kind"] == bundles.STEP_WEEK else None)

    total = len(b["steps"])
    await bot.send_message(
        chat_id,
        texts.BUNDLE_STEP_HEADER.format(
            emoji=b["emoji"], title=esc(step["title"]), bundle=esc(b["title"]),
            n=step_no + 1, total=total)
        + texts.cards_list_html(drawn, labels))

    try:
        raw = await delivery.typing_while(
            bot, chat_id,
            llm.chat(
                prompts.build_bundle_reading_messages(
                    b["title"], step["title"], name, drawn, spread,
                    intro_answers=intro, story_block=story, marker=marker,
                    her_answer=answer, with_marker=bundles.wants_marker(key),
                    week_card_note=week_note,
                ),
                max_tokens=prompts.max_tokens(spread), temperature=0.9,
            ),
        )
    except llm.LLMError:
        await bot.send_message(chat_id, texts.ERROR_LLM, reply_markup=kb.to_menu())
        return False

    parsed = serial.parse(raw, n)
    clean = serial.plain_text(parsed, labels) if parsed else raw
    new_marker = (parsed or {}).get("marker")

    reading_id, _ = await db.add_reading(
        user_id, f"{b['title']} — {step['title']}",
        answer or (marker or ""), drawn, clean, bundle_id)
    await db.attach_reading_to_step(step_id, reading_id)
    if new_marker:
        await db.set_bundle_marker(bundle_id, new_marker)

    if parsed is None:
        await delivery.send_plain_reading(bot, chat_id, raw, reading_id)
    else:
        await delivery.send_serial(
            bot, chat_id, spread, drawn, labels, parsed, reading_id)

    await _send_marker(bot, chat_id, key, new_marker or marker, step_no + 1)
    await _enter_dialogue(
        bot, user_id, state, topic=f"{b['title']} — {step['title']}",
        question=answer or _question_from_intro(intro), drawn=drawn,
        reading_text=clean, dlg_max=config.DIALOGUE_MAX_BUNDLE_STEP,
        bundle_id=bundle_id,
    )
    return True


# ---------------------------------------------------------------- Финал

async def _run_final(
    bot: Bot, chat_id: int, user_id: int, bundle_id: int, key: str, b: dict,
) -> bool:
    """Закрывающее письмо. Карты не тянем: это сведение арки — и единственное,
    чего не даст ни один отдельный расклад, потому что нужна история."""
    bundle = await db.get_bundle(bundle_id)
    intro = _intro_dict(bundle["intro"] if bundle else None)
    name = await _name_of(user_id)
    story = await _story_block(bundle_id)
    repeat = await db.bundle_repeat_card(bundle_id)

    try:
        body = await delivery.typing_while(
            bot, chat_id,
            llm.chat(
                prompts.build_bundle_final_messages(
                    key, b["title"], name, story, intro_answers=intro,
                    repeat_card=repeat),
                max_tokens=1100, temperature=0.85,
            ),
        )
    except llm.LLMError:
        await bot.send_message(chat_id, texts.ERROR_LLM, reply_markup=kb.to_menu())
        return False

    step = b["steps"][-1]
    head = texts.BUNDLE_FINAL_HEADER.format(
        emoji=b["emoji"], title=esc(step["title"]), bundle=esc(b["title"]))
    for part in texts.split_body(head + esc(body) + texts.BUNDLE_FINAL_FOOTER):
        await bot.send_message(chat_id, part)

    await db.finish_bundle(bundle_id)
    await bot.send_message(
        chat_id, texts.BUNDLE_DONE_OFFER.get(key, ""), reply_markup=kb.bundle_done(key))
    return True


async def _name_of(user_id: int) -> str:
    row = await db.get_user(user_id)
    if row is None:
        return "дорогая"
    return row["display_name"] or row["first_name"] or "дорогая"


# ---------------------------------------------------------------- Приглашение

async def ask_step(bot: Bot, step_row) -> bool:
    """Спросить про маркер. Один вопрос, ответ одной строкой — и расклад
    НЕ блокируется её ответом: не ответила, значит завтра разложим на том,
    что знаем. Иначе это форма, а не разговор."""
    key = step_row["kind"]
    b = bundles.get(key)
    step = bundles.step_at(key, step_row["step_no"])
    if not b or not step:
        await db.complete_step(step_row["id"], None)
        return False
    if step["kind"] == bundles.STEP_FINAL or not step.get("ask"):
        # Финалу спрашивать нечего — сразу пишем письмо
        return await run_step(bot, step_row["user_id"], step_row["user_id"], step_row)

    template = texts.BUNDLE_STEP_ASK.get(step["ask"])
    if not template:
        return await run_step(bot, step_row["user_id"], step_row["user_id"], step_row)

    uid = step_row["user_id"]
    text = template.format(
        name=esc(step_row["name"] if "name" in step_row.keys() else await _name_of(uid)),
        marker_line=texts.marker_line(step_row["marker"]),
    )
    # Сбрасываем состояние: спустя дни она почти наверняка уже не в разговоре
    # по старому раскладу, но если да — её ответ на наш вопрос ушёл бы туда,
    # а не в бандл. Вопрос задаём мы, значит и контекст теперь наш.
    ctx = await _dialogue_context(bot, uid)
    if ctx is not None:
        try:
            await ctx.clear()
        except Exception:  # noqa: BLE001
            pass

    await delivery.send_offer_photo(bot, uid, b["img"])
    await bot.send_message(uid, text, reply_markup=kb.bundle_step(step_row["id"]))
    await db.mark_step_asked(step_row["id"])
    return True
