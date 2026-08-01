"""Тесты изменений монетизации: обновляемый лимит, суточный лимит диалога,
разбор месяца, Кельтский крест, тексты пейволла. Запуск: python3 test_monetization.py"""

import asyncio
import os
import sys
import tempfile
from datetime import timedelta

os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("LLM_API_KEY", "test")
os.environ.setdefault("ADMIN_IDS", "1")

DB_FILE = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ["DB_PATH"] = DB_FILE

import aiosqlite  # noqa: E402

import bundles  # noqa: E402
import config  # noqa: E402
import database as db  # noqa: E402
import handlers  # noqa: E402
import keyboards as kb  # noqa: E402
import prompts  # noqa: E402
import scheduler  # noqa: E402
import serial  # noqa: E402
import texts  # noqa: E402

OK, FAIL = 0, 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name} {extra}")


# ---------- 1. Тексты: все .format() без KeyError ----------

def test_texts() -> None:
    print("\n[тексты и витрина подписки]")
    row = {"display_name": "Аня", "first_name": None}

    class R(dict):
        def __getitem__(self, k):
            return dict.__getitem__(self, k)

    paywall = handlers._paywall_text({"display_name": "Аня", "first_name": None})
    check("PAYWALL собирается",
          str(config.PRICE_PACK_RUB) in paywall and "Аня" in paywall)
    check("PAYWALL продаёт пакет, а не подписку",
          str(config.PACK_READINGS) in paywall and "Подписка" not in paywall)
    check("PAYWALL обещает пополнение", "начислю" in paywall)
    check("PAYWALL без «без лимита»", "без лимита" not in paywall)

    tariffs = texts.TARIFFS.format(
        ladder=texts.ladder_lines(), left="свободных раскладов нет",
        benefits=texts.sub_benefits(), free_note=texts.free_note(),
    )
    check("TARIFFS собирается", "Разбор месяца" in tariffs)
    check("TARIFFS показывает всю лестницу",
          all(str(config.PLANS[k]["rub"]) in tariffs
              for k in ("single", "pack5", "month")))
    check("недельного тарифа на витрине нет",
          "Неделя —" not in tariffs and "week" not in config.PLANS)

    check("HELP собирается",
          "Кельтский крест" in texts.HELP.format(
              free_terms=texts.free_terms(), support="@s"))
    check("CELTIC_LOCKED собирается",
          "299" in texts.CELTIC_LOCKED.format(
              benefits=texts.sub_benefits(), p_month=299))
    check("REVIEW_LOCKED собирается",
          "299" in texts.REVIEW_LOCKED.format(
              benefits=texts.sub_benefits(), p_month=299))
    check("REVIEW_NOT_ENOUGH собирается",
          "3" in texts.REVIEW_NOT_ENOUGH.format(n=3, have="1 расклад"))
    check("REVIEW_COOLDOWN собирается",
          "дней" in texts.REVIEW_COOLDOWN.format(days=texts.days_phrase(5)))
    check("REVIEW_HEADER собирается",
          "5 раскладов" in texts.REVIEW_HEADER.format(n=texts.readings_phrase(5)))
    check("DIALOGUE_LIMIT собирается", "🖤" in texts.DIALOGUE_LIMIT)
    offer = texts.DIALOGUE_LIMIT_OFFER.format(
        free=5, p_single=config.PRICE_SINGLE_RUB,
        d_paid=config.DIALOGUE_MAX_PAID, next_free="в понедельник")
    check("оффер на исчерпании реплик собирается",
          str(config.PRICE_SINGLE_RUB) in offer
          and str(config.DIALOGUE_MAX_PAID) in offer)
    check("DIALOGUE_UNLOCKED собирается",
          "10" in texts.DIALOGUE_UNLOCKED.format(d_paid=10))
    check("DIALOGUE_LIMIT_SUB собирается",
          "20" in texts.DIALOGUE_LIMIT_SUB.format(n=20))
    check("DIALOGUE_LIMIT_SUB_MONTH собирается",
          "300" in texts.DIALOGUE_LIMIT_SUB_MONTH.format(n=300))
    check("TOPUP_GRANTED собирается",
          "понедельник" in texts.TOPUP_GRANTED.format(name="Аня", weekday="понедельник"))
    check("DAILY_HEADER_PERSONAL собирается",
          "лично" in texts.DAILY_HEADER_PERSONAL.format(card="Луна"))
    check("витрина подписки без плейсхолдеров",
          "{" not in texts.sub_benefits())
    check("на экране фичи её пункт не дублируется",
          "Кельтский" not in texts.sub_benefits(skip="celtic")
          and "Разбор месяца" not in texts.sub_benefits(skip="review"))
    check("названия тарифов не обещают «без ограничений»",
          all("без ограничений" not in p["title"] for p in config.PLANS.values()),
          str([p["title"] for p in config.PLANS.values()]))
    check("в текстах нигде не осталось «без лимита»",
          not any("без лимита" in v for v in vars(texts).values()
                  if isinstance(v, str)))

    # Длинный фолбэк должен резаться под лимит Telegram (4096)
    long_body = "\n\n".join("абзац " * 60 for _ in range(12))
    parts = texts.split_body(long_body)
    check("длинное полотно режется на части",
          len(parts) > 1 and all(len(p) <= 3500 for p in parts),
          f"({len(parts)} частей, макс {max(len(p) for p in parts)})")
    check("при склейке текст не теряется",
          "".join(parts).replace("\n", "").replace(" ", "")
          == long_body.replace("\n", "").replace(" ", ""))
    check("короткий текст не дробится",
          texts.split_body("привет") == ["привет"])
    check("абзац длиннее лимита тоже режется",
          all(len(p) <= 3500 for p in texts.split_body("я" * 9000)))
    # Жёсткий разрыв не должен разрубить HTML-сущность (&amp;)
    hard = ("я" * 3495) + "&amp;" + ("я" * 3000)
    hp = texts.split_body(hard)
    check("HTML-сущность не разрывается на жёстком разрыве",
          all(not (p.count("&") > p.count(";")) for p in hp),
          str([p[-8:] for p in hp]))
    check("и текст при этом не теряется", "".join(hp) == hard)
    check("последняя часть + хвост влезают в лимит",
          len(parts[-1] + texts.AFTER_READING) < 4096)
    check("next_topup_phrase непустая", bool(texts.next_topup_phrase()))
    check("free_terms упоминает пополнение", "понедельник" in texts.free_terms())
    check("balance_line пустого баланса зовёт в понедельник",
          "следующий бесплатный" in texts.balance_line(0, 0, None))
    check("_paywall_text работает на строке-словаре",
          "Аня" in handlers._paywall_text(row))
    check("balance_short молчит на пустом балансе",
          texts.balance_short(0, 0, None) == "")
    check("balance_short показывает остаток",
          "3 расклада" in texts.balance_short(1, 2, None))
    check("balance_short у подписчицы — про подписку",
          "подписка" in texts.balance_short(0, 0, "01.09 12:00"))


# ---------- 2. Кельтский крест ----------

def test_celtic() -> None:
    print("\n[Кельтский крест]")
    check("10 карт в SPREAD_CARDS", handlers.SPREAD_CARDS["celtic"] == 10)
    check("10 подписей позиций",
          len(prompts.SPREAD_POSITIONS["celtic"]) == 10)
    check("подписи совпадают с числом карт",
          len(prompts.spread_labels("celtic")) == handlers.SPREAD_CARDS["celtic"])
    check("правила расклада есть", "celtic" in prompts.SPREAD_RULES)
    check("формат на 10 блоков", "===КАРТА 10===" in prompts.SPREAD_RULES["celtic"])
    check("название есть", texts.SPREAD_TITLES["celtic"] == "Кельтский крест")
    check("вопрос-приглашение есть", "celtic" in texts.ASK_SPREAD_QUESTION)
    check("расклад премиальный", "celtic" in config.PREMIUM_SPREADS)
    check("Mini App для него отключён", "celtic" in handlers.NO_APP_SPREADS)
    check("max_tokens поднят", handlers._max_tokens("celtic") == 2600)
    check("справочники общие с промптами",
          handlers.SPREAD_CARDS is prompts.SPREAD_CARD_COUNT)
    check("max_tokens прочих не изменился",
          handlers._max_tokens("classic") == 1300
          and handlers._max_tokens("yesno") == 500
          and handlers._max_tokens("week") == 1800)

    # Разбор серийного ответа на 10 карт
    body = "\n".join(f"===КАРТА {i}===\nтекст {i}" for i in range(1, 11))
    parsed = serial.parse(body + "\n===ИТОГ===\nвывод\n===ПОДСКАЗКИ===\nага\nи что?", 10)
    check("serial.parse разбирает 10 карт",
          parsed is not None and len(parsed["cards"]) == 10)
    check("итог и подсказки на месте",
          parsed and parsed["summary"] == "вывод" and len(parsed["chips"]) == 2)
    # Недостающий блок -> фолбэк на цельный текст
    broken = "\n".join(f"===КАРТА {i}===\nтекст" for i in range(1, 10))
    check("кривая разметка -> None (фолбэк)", serial.parse(broken, 10) is None)


# ---------- 3. Клавиатуры ----------

def test_keyboards() -> None:
    print("\n[клавиатуры]")
    free = kb.spreads(False)
    sub = kb.spreads(True)
    flat_free = [b.text for r in free.inline_keyboard for b in r]
    flat_sub = [b.text for r in sub.inline_keyboard for b in r]
    check("Кельтский крест виден без подписки — с замком",
          any("Кельтский" in t and "🔒" in t for t in flat_free))
    check("у подписчицы — без замка",
          any("Кельтский" in t and "🔒" not in t for t in flat_sub))
    check("кнопка разбора месяца в меню",
          any("Разбор месяца" in b.text
              for r in kb.main_menu().inline_keyboard for b in r))
    check("sub_plans: остался только месяц",
          [b.callback_data for r in kb.sub_plans().inline_keyboard for b in r][:1]
          == ["buy:month"])
    check("нигде не покупается снятый недельный тариф",
          not any(b.callback_data == "buy:week"
                  for m in (kb.plans(), kb.sub_plans(), kb.pack_offer())
                  for r in m.inline_keyboard for b in r))
    check("sub_plans: настраиваемая кнопка «назад»",
          any(b.callback_data == "menu"
              for r in kb.sub_plans(back="menu").inline_keyboard for b in r))
    check("цена разового в тарифах — 49",
          any("49" in b.text for r in kb.plans().inline_keyboard for b in r))
    check("пейволл предлагает пакет первым",
          kb.pack_offer().inline_keyboard[0][0].callback_data == "buy:pack5")
    check("на исчерпании реплик одна кнопка — продолжить",
          kb.continue_offer().inline_keyboard[0][0].callback_data == "buy:single")
    bundle_menu = [b.callback_data
                   for r in kb.spreads(False, bundle_keys=["him", "month"]).inline_keyboard
                   for b in r]
    check("бандлы живут в меню раскладов, а не в тарифах",
          "bundle:show:him" in bundle_menu and "bundle:show:month" in bundle_menu)
    running = [b.text for r in kb.spreads(
        False, bundle_keys=["him"], active={"him"}).inline_keyboard for b in r]
    check("идущий бандл показан как идущий, а не как цена",
          any("идёт" in t for t in running))


# ---------- 4. База: миграция, пополнение, лимиты ----------

async def _fresh_db() -> None:
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    await db.init()


async def _mk_user(uid: int, name="Аня", free=0, paid=0, sub_until=None,
                   last_seen=None, display=True) -> None:
    conn = await aiosqlite.connect(DB_FILE)
    try:
        await conn.execute(
            """INSERT INTO users (user_id, first_name, display_name, created_at,
                                  last_seen, free_readings_left, paid_readings_left,
                                  subscription_until)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid, name, name if display else None, db.now_iso(),
             last_seen or db.now_iso(), free, paid, sub_until),
        )
        await conn.commit()
    finally:
        await conn.close()


async def _free_of(uid: int) -> int:
    row = await db.get_user(uid)
    return row["free_readings_left"]


async def test_topup() -> None:
    print("\n[недельное пополнение]")
    await _fresh_db()
    future = (db.now_utc().replace(microsecond=0)).isoformat().replace(
        db.now_utc().strftime("%Y"), str(db.now_utc().year + 1))
    await _mk_user(1, free=0)                    # пустая — пополнить
    await _mk_user(2, free=1)                    # ниже потолка — пополнить до 2
    await _mk_user(3, free=2)                    # на потолке — не трогать
    await _mk_user(4, free=5)                    # накопила рефералкой — не отбирать
    await _mk_user(5, free=0, sub_until=future)  # подписчица — не нужно
    await _mk_user(6, free=0, display=False)     # не прошла онбординг — мимо

    n = await db.weekly_topup(config.FREE_WEEKLY_TOPUP, config.FREE_TOPUP_CAP)
    check("пополнены только те, кто ниже потолка", n == 2, f"(n={n})")
    check("пустой начислили 1", await _free_of(1) == 1)
    check("одному добрали до потолка", await _free_of(2) == 2)
    check("на потолке не изменилось", await _free_of(3) == 2)
    check("накопленное сверх потолка не отобрали", await _free_of(4) == 5)
    check("подписчице не начисляли", await _free_of(5) == 0)
    check("без онбординга не начисляли", await _free_of(6) == 0)

    # Повторный запуск не должен пробить потолок
    await db.weekly_topup(config.FREE_WEEKLY_TOPUP, config.FREE_TOPUP_CAP)
    check("второй прогон не пробивает потолок", await _free_of(1) == 2)
    await db.weekly_topup(config.FREE_WEEKLY_TOPUP, config.FREE_TOPUP_CAP)
    check("третий прогон ничего не меняет", await _free_of(1) == 2)

    # Выключенное пополнение
    check("нулевые настройки ничего не делают",
          await db.weekly_topup(0, 2) == 0 and await db.weekly_topup(1, 0) == 0)


async def test_topup_notify() -> None:
    print("\n[кому писать о пополнении]")
    await _fresh_db()
    old = (db.now_utc().timestamp(), )
    from datetime import timedelta
    long_ago = (db.now_utc() - timedelta(days=90)).isoformat()
    future = (db.now_utc() + timedelta(days=5)).isoformat()
    await _mk_user(1, free=0)                                 # пустая, активная
    await _mk_user(2, free=2)                                 # есть расклады
    await _mk_user(3, free=0, last_seen=long_ago)              # давно пропала
    await _mk_user(4, free=0, sub_until=future)                # подписчица
    await _mk_user(5, free=0, paid=1)                          # есть оплаченный
    ids = [r["user_id"] for r in await db.topup_notify_targets(30)]
    check("пишем только пустым и недавно активным", ids == [1], f"(ids={ids})")
    check("имя приходит для подстановки",
          (await db.topup_notify_targets(30))[0]["name"] == "Аня")
    assert old


async def _talk(uid: int, date: str, day: int, month: int) -> str:
    """Одна реплика: проверка лимита, потом списание — как в обработчике."""
    status, _used = await db.sub_dialogue_check(uid, date, day, month)
    if status == "ok":
        await db.sub_dialogue_add(uid, date)
    return status


async def test_dialogue_cap() -> None:
    print("\n[лимиты диалога у подписчиц: сутки и месяц]")
    await _fresh_db()
    await _mk_user(1)
    day, month = 3, 7
    st = [await _talk(1, "2026-07-25", day, month) for _ in range(5)]
    check("первые N реплик за день разрешены", st[:3] == ["ok", "ok", "ok"])
    check("дальше — стоп по суткам", st[3:] == ["day", "day"])
    check("счётчик за сутки дошёл до потолка",
          (await db.sub_dialogue_used(1, "2026-07-25"))[0] == 3)

    check("на следующий день суточный счётчик обнулился",
          await _talk(1, "2026-07-26", day, month) == "ok")

    # добиваем месячный бюджет: 3 (25-го) + 1 (26-го) + ещё 3
    tail = [await _talk(1, "2026-07-27", day, month) for _ in range(4)]
    check("месячный бюджет ловится отдельным статусом",
          tail == ["ok", "ok", "ok", "month"], f"({tail})")
    st_m, used_m = await db.sub_dialogue_check(1, "2026-07-27", day, month)
    check("в сообщении про месяц — месячное число",
          st_m == "month" and used_m == month, f"({st_m},{used_m})")

    check("с новым месяцем бюджет обнулился",
          await _talk(1, "2026-08-01", day, month) == "ok")
    check("оба лимита 0 = без ограничений",
          await _talk(1, "2026-08-02", 0, 0) == "ok")
    check("несуществующий пользователь не ломает счётчик",
          await _talk(999, "2026-08-02", day, month) == "ok")
    check("у несуществующего ничего не записалось",
          await db.sub_dialogue_used(999, "2026-08-02") == (0, 0))

    # Сорванная генерация не должна съедать бюджет: check без add
    before = await db.sub_dialogue_used(1, "2026-08-03")
    await db.sub_dialogue_check(1, "2026-08-03", day, month)
    after = await db.sub_dialogue_used(1, "2026-08-03")
    check("проверка лимита сама ничего не списывает", before == after,
          f"({before} -> {after})")


async def test_review() -> None:
    print("\n[разбор месяца]")
    await _fresh_db()
    await _mk_user(1)
    row = await db.get_user(1)
    check("без прошлого разбора — можно сразу",
          db.review_days_left(row, 25) == 0)
    await db.set_review_done(1)
    row = await db.get_user(1)
    check("после разбора включается пауза",
          db.review_days_left(row, 25) == 25)
    check("нулевой кулдаун не блокирует", db.review_days_left(row, 0) == 0)

    for i in range(4):
        await db.add_reading(
            1, "Отношения", f"вопрос {i}",
            [{"id": i, "name": f"Карта {i}", "rev": False}], "текст")
    rows = await db.review_readings(1, 12)
    check("расклады для разбора поднимаются", len(rows) == 4)
    check("порядок от старых к новым", rows[0]["question"] == "вопрос 0")
    block = handlers._review_block(rows)
    check("блок для промпта собирается",
          "Отношения" in block and "Карта 0" in block)
    msgs = prompts.build_review_messages("Аня", block)
    check("промпт разбора собирается",
          len(msgs) == 2 and "Разбор" not in msgs[0]["content"][:0] + "x"
          and "Аня" in msgs[1]["content"])
    check("в промпте есть правила без мистики",
          "не «карты подают знак»" in msgs[0]["content"])


async def test_migration() -> None:
    print("\n[миграция старой базы]")
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    # Старая схема — без новых колонок
    conn = await aiosqlite.connect(DB_FILE)
    await conn.execute(
        """CREATE TABLE users (
               user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
               display_name TEXT, source TEXT, created_at TEXT NOT NULL,
               last_seen TEXT, free_readings_left INTEGER NOT NULL DEFAULT 0,
               paid_readings_left INTEGER NOT NULL DEFAULT 0,
               subscription_until TEXT, readings_count INTEGER NOT NULL DEFAULT 0,
               daily_opt_in INTEGER NOT NULL DEFAULT 0, last_daily_date TEXT,
               referrer_id INTEGER, referral_bonus_given INTEGER NOT NULL DEFAULT 0)""")
    await conn.execute(
        """CREATE TABLE readings (id INTEGER PRIMARY KEY AUTOINCREMENT,
               user_id INTEGER NOT NULL, topic TEXT, question TEXT, cards TEXT,
               reading_text TEXT, created_at TEXT NOT NULL,
               followup_sent INTEGER NOT NULL DEFAULT 0)""")
    await conn.execute(
        "INSERT INTO users (user_id, first_name, display_name, created_at, "
        "free_readings_left) VALUES (7, 'Оля', 'Оля', ?, 3)", (db.now_iso(),))
    await conn.commit()
    await conn.close()

    await db.init()  # должен доехать без ошибок и добавить колонки
    row = await db.get_user(7)
    cols = set(row.keys())
    check("колонка sub_dialogue_date добавлена", "sub_dialogue_date" in cols)
    check("колонка sub_dialogue_count добавлена", "sub_dialogue_count" in cols)
    check("колонка last_review добавлена", "last_review" in cols)
    check("старые данные целы", row["free_readings_left"] == 3)
    check("review_days_left не падает на старой строке",
          db.review_days_left(row, 25) == 0)
    check("пополнение работает после миграции",
          await db.weekly_topup(1, 2) >= 0)
    ok, _src = await db.readings_available(7)
    check("баланс читается", ok is True)


async def test_daily_personal() -> None:
    print("\n[личная карта дня]")
    await _fresh_db()
    from datetime import timedelta
    future = (db.now_utc() + timedelta(days=5)).isoformat()
    await _mk_user(1, sub_until=future)
    await _mk_user(2)
    conn = await aiosqlite.connect(DB_FILE)
    # readings_count > 0 — рассылка теперь идёт только тем, кто уже раскладывал:
    # первое утреннее письмо приходит на следующее утро после первого расклада
    await conn.execute("UPDATE users SET daily_opt_in = 1, readings_count = 1")
    await conn.commit()
    await conn.close()
    rows = await db.daily_optin_users()
    check("рассылка знает про подписку",
          all("subscription_until" in r.keys() for r in rows))
    subs = [r["user_id"] for r in rows if db.sub_active(r)]
    check("подписчица определяется в рассылке", subs == [1], f"({subs})")

    msgs = prompts.build_daily_personal_messages(
        {"name": "Луна", "essence": "туман", "upright": "неясность"},
        "Аня", "Отношения", "он не пишет")
    check("промпт личной карты собирается",
          "Отношения" in msgs[1]["content"] and "Аня" in msgs[1]["content"])
    empty = prompts.build_daily_personal_messages(
        {"name": "Луна", "essence": "туман", "upright": "неясность"},
        "Аня", None, None)
    check("без раскладов промпт не выдумывает тему",
          "Раскладов пока не было" in empty[1]["content"])
    check("функция личной карты есть в планировщике",
          callable(scheduler.personal_daily_text))
    # «Раскладов ещё нет» — это не сбой провайдера: возвращаем "", а не None,
    # иначе предохранитель выключил бы персонализацию всем остальным
    nothing = await scheduler.personal_daily_text(
        2, "Аня", {"name": "Луна", "essence": "туман", "upright": "неясность"})
    check("без раскладов — пустая строка, а не сбой", nothing == "",
          f"({nothing!r})")
    check("джоб пополнения есть в планировщике",
          callable(scheduler.weekly_topup_job))


# ---------- 5. Экономика: прикидка косты ----------

def test_economics() -> None:
    """Не «доказательство прибыльности», а проверка, что у каждой статьи
    расходов есть потолок. Себестоимость: расклад ≈ 3 ₽, реплика ≈ 1 ₽
    (оценка по max_tokens, не измерено — сверить по счёту AITunnel)."""
    print("\n[экономика: у всего ли есть потолок]")
    r_cost, d_cost = 3, 1

    free_max = config.FREE_WEEKLY_TOPUP * 4 * r_cost
    check(f"бесплатница ограничена сверху: ≈ {free_max} ₽/мес",
          0 < free_max <= 20, f"({free_max})")

    check("у диалога подписчицы есть суточный потолок",
          config.DIALOGUE_MAX_SUB > 0)
    check("и месячный бюджет", config.DIALOGUE_MAX_SUB_MONTH > 0)
    dialog_max = min(config.DIALOGUE_MAX_SUB * 30,
                     config.DIALOGUE_MAX_SUB_MONTH) * d_cost
    net = config.PRICE_MONTH_RUB * 0.965
    print(f"      · верхняя граница диалога у подписчицы: {dialog_max} ₽/мес "
          f"при выручке {net:.0f} ₽ — патологический случай, но конечный")
    check("диалог не может съесть выручку месяца в одиночку",
          dialog_max < net * 0.6, f"({dialog_max} vs {net:.0f})")
    check("месячный бюджет реально ограничивает суточный",
          config.DIALOGUE_MAX_SUB_MONTH < config.DIALOGUE_MAX_SUB * 30)
    print(f"      · расклады у подписчицы по-прежнему без счёта: "
          f"{r_cost} ₽ за расклад — это осознанный хвост, следить по /stats")


async def test_daily_default() -> None:
    """Карта дня по умолчанию: опт-ин показывался только тем, кто сам нажал
    «карта дня», и до него не доходил почти никто — 0 подписок из 17."""
    print("\n[карта дня по умолчанию]")
    await _fresh_db()

    # Старый пользователь из базы, заведённой до изменения
    conn = await aiosqlite.connect(DB_FILE)
    await conn.execute(
        """INSERT INTO users (user_id, created_at, daily_opt_in,
                              daily_default_applied, readings_count, free_readings_left)
           VALUES (7, '2026-07-01', 0, 0, 3, 0)"""
    )
    await conn.commit()
    await conn.close()
    await db.init()
    old = await db.get_user(7)
    check("миграция включает утреннюю карту старым пользователям",
          old["daily_opt_in"] == 1)

    # Новый — уже подписан на регистрации
    await db.create_user(8, None, "Аня", None, "src_ero")
    check("новый пользователь подписан по умолчанию",
          (await db.get_user(8))["daily_opt_in"] == 1)

    # Без расклада утреннее письмо не уходит
    ids = [r["user_id"] for r in await db.daily_optin_users()]
    check("без расклада рассылка не трогает", ids == [7], f"({ids})")

    # Осознанное «отключить» не должно отменяться следующим рестартом
    await db.set_daily_opt(7, False)
    await db.init()
    check("отписка переживает рестарт",
          (await db.get_user(7))["daily_opt_in"] == 0)

    # Повторный init не должен падать на PRAGMA journal_mode внутри транзакции
    await db.init()
    check("повторная миграция не роняет старт", True)

    s = await db.stats_snapshot()
    check("/stats считает отписавшихся", s["daily_off"] == 1, f"({s['daily_off']})")
    check("/stats отдаёт поведение по источникам, а не только счётчик стартов",
          all(len(row) == 5 for row in s["sources"]), f"({s['sources']})")
    check("у утренней рассылки есть кнопка отписки",
          any("daily_sub:off" in b.callback_data
              for r in kb.daily_morning().inline_keyboard for b in r))
    check("первое утро объясняет, откуда письмо",
          "каждое утро" in texts.DAILY_FIRST_NOTE)
    check("followup держится в дневном окне",
          0 <= config.FOLLOWUP_HOUR_FROM < config.FOLLOWUP_HOUR_TO <= 24)


async def test_mood_images() -> None:
    """Банк картинок для напоминалок: ротация, стабильность, выключаемость."""
    print("\n[картинки напоминалок]")
    check("банк не пустой", config.MOOD_COUNT > 0)

    urls = {config.mood_img_url(i) for i in range(config.MOOD_COUNT)}
    check("каждому индексу свой файл", len(urls) == config.MOOD_COUNT)
    check("имена совпадают с файлами в mood/",
          config.mood_img_url(0).endswith("/mood_01.jpg")
          and config.mood_img_url(config.MOOD_COUNT - 1).endswith(
              f"/mood_{config.MOOD_COUNT:02d}.jpg"))
    check("индекс за границей не падает, а заворачивается",
          config.mood_img_url(config.MOOD_COUNT) == config.mood_img_url(0))

    # Стабильность: crc32, а не hash() — иначе после рестарта картинки поедут
    a = scheduler._mood_index(1296080172, "nudge")
    check("один и тот же повод даёт ту же картинку",
          a == scheduler._mood_index(1296080172, "nudge"))
    check("индекс всегда внутри банка",
          all(0 <= scheduler._mood_index(u, s) < config.MOOD_COUNT
              for u in (1, 999, 1296080172) for s in ("nudge", "followup:7", "topup:x")))

    # Разным людям и разным поводам — разные картинки (не идеальная равномерность,
    # но одно и то же подряд приходить не должно)
    spread = {scheduler._mood_index(u, "nudge") for u in range(1, 40)}
    check("между людьми картинки разные", len(spread) > config.MOOD_COUNT // 2,
          f"({len(spread)} из {config.MOOD_COUNT})")
    per_user = {scheduler._mood_index(42, s)
                for s in ("nudge", "followup:1", "followup:2", "topup:2026-08-03")}
    check("одному человеку на разные поводы — разное", len(per_user) > 1)

    # Выключается конфигом, без правки кода
    saved = config.MOOD_COUNT
    config.MOOD_COUNT = 0
    check("MOOD_COUNT=0 выключает картинки", config.mood_img_url(0) == "")
    config.MOOD_COUNT = saved




# ---------- 6. Лестница цен и пакеты ----------

def test_ladder() -> None:
    print("\n[лестница цен]")
    check("вход подешевел вдвое", config.PRICE_SINGLE_RUB <= 49)
    check("месяц подешевел вдвое", config.PRICE_MONTH_RUB <= 299)
    check("недельного тарифа нет в продаже", "week" not in config.PLANS)
    check("но старый платёж на неделю всё ещё отрабатывается",
          config.plan("week") is not None and config.plan("week")["days"] == 7)
    check("неизвестный тариф не роняет", config.plan("нет такого") is None)

    kinds = {k: v.get("kind") for k, v in config.PLANS.items()}
    check("у каждой ступени есть тип", all(kinds.values()), str(kinds))
    check("пакет даёт больше одного расклада",
          config.PLANS["pack5"]["readings"] == config.PACK_READINGS
          and config.PACK_READINGS > 1)
    per = config.PRICE_PACK_RUB / config.PACK_READINGS
    check("в пакете расклад дешевле, чем поштучно",
          per < config.PRICE_SINGLE_RUB, f"({per:.0f} vs {config.PRICE_SINGLE_RUB})")
    check("бандл дороже пакета, но дешевле месяца",
          config.PRICE_PACK_RUB < config.PRICE_BUNDLE_RUB <= config.PRICE_MONTH_RUB)
    check("лестница на витрине без дыр",
          all(k in config.PLANS for k in config.LADDER))

    check("после оплаты каждого тарифа есть что сказать",
          all("{" not in texts.pay_success(k) for k in config.PLANS))
    check("и у снятого с продажи тоже", "✅" in texts.pay_success("week"))
    check("неизвестный тариф не падает после оплаты",
          "✅" in texts.pay_success("что-то-новое"))


async def test_purchases() -> None:
    print("\n[что начисляет покупка]")
    await _fresh_db()
    await _mk_user(1)

    await db.apply_purchase(1, "single")
    check("разовый даёт один расклад", (await db.get_user(1))["paid_readings_left"] == 1)

    await db.apply_purchase(1, "pack5")
    check(f"пакет даёт сразу {config.PACK_READINGS}",
          (await db.get_user(1))["paid_readings_left"] == 1 + config.PACK_READINGS)

    await db.apply_purchase(1, "month")
    row = await db.get_user(1)
    check("месяц включает подписку", db.sub_active(row))

    # Незакрытый платёж со снятым тарифом обязан отработать, а не упасть:
    # деньги уже списаны
    await _mk_user(2)
    await db.apply_purchase(2, "week")
    check("старый недельный платёж всё ещё включает подписку",
          db.sub_active(await db.get_user(2)))
    check("несуществующий тариф просто ничего не делает",
          await db.apply_purchase(2, "нет-такого") is None)

    bid = await db.apply_purchase(1, "bundle_him")
    check("бандл не начисляет расклады, а заводит разбор", isinstance(bid, int))
    b = await db.get_bundle(bid)
    check("новый бандл ждёт начала", b["status"] == "new" and b["kind"] == "him")
    check("остаток раскладов бандлом не тронут",
          (await db.get_user(1))["paid_readings_left"] == 1 + config.PACK_READINGS)


def test_dialogue_tiers() -> None:
    print("\n[лимиты реплик по тарифам]")
    check("бесплатный расклад — затравка",
          handlers._dialogue_limit("free") == config.DIALOGUE_MAX)
    check("платный ощутимо длиннее",
          handlers._dialogue_limit("paid") == config.DIALOGUE_MAX_PAID
          and config.DIALOGUE_MAX_PAID > config.DIALOGUE_MAX)
    check("бандл — самый длинный разговор",
          config.DIALOGUE_MAX_BUNDLE > config.DIALOGUE_MAX_PAID)
    check("шаг бандла тоже не куцый",
          config.DIALOGUE_MAX_BUNDLE_STEP >= config.DIALOGUE_MAX_PAID)

    # Главное, ради чего лимиты вообще есть: при цене месяца 299 ₽ потолок
    # расходов на диалог должен быть заметно меньше выручки
    net = config.PRICE_MONTH_RUB * 0.965
    worst = min(config.DIALOGUE_MAX_SUB * 30, config.DIALOGUE_MAX_SUB_MONTH) * 1
    check("диалог подписчицы не съедает половину выручки месяца",
          worst < net * 0.5, f"({worst} ₽ при выручке {net:.0f} ₽)")

    # Внутри бандла ничего не продаём: она уже заплатила
    inside = handlers._dialogue_limit_text({"bundle_id": 7}, 25)
    check("в бандле на исчерпании реплик нет оффера",
          str(config.PRICE_SINGLE_RUB) not in inside)
    outside = handlers._dialogue_limit_text({}, 5)
    check("вне бандла — есть", str(config.PRICE_SINGLE_RUB) in outside)


# ---------- 7. Бандлы ----------

def test_bundle_config() -> None:
    print("\n[сценарии бандлов]")
    for key in bundles.all_keys():
        b = bundles.get(key)
        days = bundles.step_days(key)
        check(f"«{b['title']}»: дней столько же, сколько шагов",
              len(days) == len(b["steps"]), f"({days} vs {len(b['steps'])})")
        check(f"«{b['title']}»: дни строго растут", days == sorted(set(days)))
        check(f"«{b['title']}»: последний шаг — закрывающее письмо",
              b["steps"][-1]["kind"] == bundles.STEP_FINAL)
        check(f"«{b['title']}»: ровно три вопроса на входе",
              len(b["intro"]) == 3)
        check(f"«{b['title']}»: у вопросов есть подпись для промпта",
              all(q.get("label") for q in b["intro"]))
        check(f"«{b['title']}»: расклад дня 0 известен промптам",
              b["day0"]["spread"] in prompts.SPREAD_RULES)
        for st in b["steps"][:-1]:
            check(f"«{b['title']}»: расклад шага «{st['title']}» известен промптам",
                  st["spread"] in prompts.SPREAD_RULES)
            check(f"«{b['title']}»: у шага «{st['title']}» есть текст вопроса",
                  st["ask"] in texts.BUNDLE_STEP_ASK)
        check(f"«{b['title']}»: есть экран объяснения", key in texts.BUNDLE_ABOUT)
        check(f"«{b['title']}»: тариф существует", b["plan"] in config.PLANS)
        check(f"«{b['title']}»: картинка витрины настроена",
              bool(config.offer_img_url(b["img"])))

    check("даты берутся из .env",
          config.bundle_days("2,5,9", [3, 7, 14]) == [2, 5, 9])
    check("кривая строка не ломает расписание",
          config.bundle_days("три,семь", [3, 7, 14]) == [3, 7, 14])
    check("несовпадение по количеству откатывается к умолчанию",
          bundles.step_days("him") == [3, 7, 14])
    saved = config.BUNDLE_HIM_DAYS
    config.BUNDLE_HIM_DAYS = "2,4"      # шагов три, дней два
    check("два дня на три шага — работаем по умолчанию",
          bundles.step_days("him") == [3, 7, 14])
    config.BUNDLE_HIM_DAYS = saved

    saved_on = config.BUNDLES_ENABLED
    config.BUNDLES_ENABLED = 0
    check("выключение снимает бандлы с витрины", bundles.on_sale() == [])
    check("и убирает их из лестницы", "Он и я" not in texts.ladder_lines())
    config.BUNDLES_ENABLED = saved_on


def test_bundle_prompts() -> None:
    print("\n[промпты бандлов]")
    drawn = [{"id": i, "name": f"Карта {i}", "rev": False,
              "essence": "суть", "upright": "прямое", "reversed": "обратное"}
             for i in range(3)]
    msgs = prompts.build_bundle_reading_messages(
        "Он и я", "Что он чувствует", "Аня", drawn, "feelings",
        intro_answers={"Его зовут": "Артём"},
        story_block="[1] был расклад", marker="напишет ли первым",
        her_answer="написал в среду", with_marker=True)
    sys_text = msgs[0]["content"][0]["text"]
    user = msgs[1]["content"]
    check("маркер требуется отдельным блоком", "===МАРКЕР===" in sys_text)
    check("маркер — наблюдение, а не задание",
          "«посмотри»" in sys_text and "«напиши ему»" in sys_text)
    check("правила продолжения подключены", "Продолжай линию" in sys_text)
    check("её слова первого дня в промпте", "Артём" in user)
    check("прошлый маркер в промпте", "напишет ли первым" in user)
    check("её рассказ в промпте", "написал в среду" in user)

    silent = prompts.build_bundle_reading_messages(
        "Он и я", "Что сдвинулось", "Аня", drawn, "shift",
        story_block="[1] был расклад")[1]["content"]
    check("молчание не блокирует расклад",
          "Она ничего не рассказала" in silent)

    fin = prompts.build_bundle_final_messages(
        "him", "Он и я", "Аня", "[1] расклад", repeat_card="Луна — 3 раза")
    fin_sys, fin_user = fin[0]["content"][0]["text"], fin[1]["content"]
    check("финал не тянет карты", "карты ты сейчас не тянешь" in fin_sys)
    check("повторяющаяся карта уезжает в письмо", "Луна" in fin_user)
    no_repeat = prompts.build_bundle_final_messages(
        "month", "Месяц вперёд", "Аня", "[1]")[1]["content"]
    check("без повторов модель не выдумывает их",
          "не выдумывай" in no_repeat)


def test_bundle_serial() -> None:
    print("\n[маркер в разметке ответа]")
    body = ("===КАРТА 1===\nа\n===КАРТА 2===\nб\n===КАРТА 3===\nв\n"
            "===ИТОГ===\nвывод\n===ПОДСКАЗКИ===\nага\n"
            "===МАРКЕР===\nпосмотри, напишет ли он\nпервым до выходных")
    parsed = serial.parse(body, 3)
    check("маркер разбирается", parsed and parsed["marker"])
    check("многострочный маркер сжимается в одну строку",
          "\n" not in (parsed["marker"] or ""))
    check("маркер не путается с картами", len(parsed["cards"]) == 3)
    check("маркер попадает в сохранённый текст",
          "первым до выходных" in serial.plain_text(parsed))
    # Старые расклады без маркера не должны сломаться
    old = serial.parse("===КАРТА 1===\nа\n===ИТОГ===\nб", 1)
    check("расклад без маркера по-прежнему разбирается",
          old is not None and old["marker"] is None)


async def test_bundle_flow() -> None:
    print("\n[бандл: расписание и шаги]")
    await _fresh_db()
    await _mk_user(1)
    bid = await db.create_bundle(1, "him")
    check("бандл заводится в статусе «не начат»",
          (await db.get_bundle(bid))["status"] == "new")
    check("он виден как незавершённый", len(await db.open_bundles(1, "him")) == 1)

    rid, _ = await db.add_reading(
        1, "Он и я", "вопрос",
        [{"id": 5, "name": "Луна", "rev": False}], "текст дня 0", bid)
    schedule = [(i, d) for i, (d, _st) in enumerate(bundles.schedule("him"))]
    await db.start_bundle(bid, {"Его зовут": "Артём"}, rid, schedule,
                          "посмотри, напишет ли он первым")
    b = await db.get_bundle(bid)
    check("после дня 0 бандл активен", b["status"] == "active")
    check("маркер сохранён", "напишет ли" in b["marker"])
    check("расклад дня 0 привязан", b["day0_reading_id"] == rid)
    check("расклады бандла собираются", len(await db.bundle_readings(bid)) == 1)

    today = db.now_utc().strftime("%Y-%m-%d")
    check("сегодня ни один шаг ещё не подошёл",
          [r["id"] for r in await db.due_bundle_steps(today)] == [])

    far = (db.now_utc() + timedelta(days=99)).strftime("%Y-%m-%d")
    due = await db.due_bundle_steps(far)
    # Даже если бот стоял месяц, за раз выдаём ОДИН шаг: иначе человек получил
    # бы весь разбор за одно утро, включая закрывающее письмо
    check("после долгого простоя приезжает только один шаг", len(due) == 1,
          f"({len(due)})")
    check("и это самый первый несделанный", due[0]["step_no"] == 0)
    check("в очередь приезжает и вид бандла, и маркер",
          due[0]["kind"] == "him" and "напишет ли" in due[0]["marker"])

    step_id = due[0]["id"]
    await db.mark_step_asked(step_id)
    check("спрошенный шаг из очереди уходит",
          step_id not in [r["id"] for r in await db.due_bundle_steps(far)])
    check("и находится как ожидающий ответа",
          (await db.awaiting_step(1))["id"] == step_id)
    check("сегодня он ещё не просрочен",
          step_id not in [r["id"] for r in await db.overdue_bundle_steps(today)])
    tomorrow = (db.now_utc() + timedelta(days=1)).strftime("%Y-%m-%d")
    check("а завтра — уже да, разложим без ответа",
          step_id in [r["id"] for r in await db.overdue_bundle_steps(tomorrow)])

    await db.set_step_answer(step_id, "написал в среду сам")
    check("её ответ сохранён",
          await db.bundle_answers(bid) == ["написал в среду сам"])

    # Порядок нерушим: следующий шаг не приедет, пока не закрыт предыдущий
    check("второй шаг ждёт закрытия первого",
          [r["step_no"] for r in await db.due_bundle_steps(far)] == [])
    await db.complete_step(step_id, None)
    check("после закрытия первого приезжает второй",
          [r["step_no"] for r in await db.due_bundle_steps(far)] == [1])

    # Занять шаг можно только один раз: долгая генерация не должна пересечься
    # со следующим тиком рассылки и выдать один и тот же расклад дважды
    second = (await db.due_bundle_steps(far))[0]["id"]
    check("шаг занимается один раз", await db.claim_step(second) is True)
    check("повторно занять нельзя", await db.claim_step(second) is False)
    await db.release_step(second)
    check("сорванный шаг возвращается в очередь",
          await db.claim_step(second) is True)

    # Повторяющаяся карта — то, ради чего пишется финальное письмо
    await db.add_reading(1, "Он и я — шаг", "q",
                         [{"id": 5, "name": "Луна", "rev": False},
                          {"id": 6, "name": "Башня", "rev": False}], "текст", bid)
    check("повторяющаяся карта находится",
          "Луна" in (await db.bundle_repeat_card(bid) or ""))
    await _mk_user(2)
    empty = await db.create_bundle(2, "month")
    check("без повторов возвращается None",
          await db.bundle_repeat_card(empty) is None)

    await db.finish_bundle(bid)
    check("доигранный бандл больше не открыт",
          not [r for r in await db.open_bundles(1, "him")])
    check("и его шаги больше не приезжают",
          not [r for r in await db.due_bundle_steps(far) if r["bundle_id"] == bid])


async def test_bundle_stats() -> None:
    print("\n[бандлы в /stats]")
    await _fresh_db()
    await _mk_user(1)
    await db.create_bundle(1, "him")
    b2 = await db.create_bundle(1, "month")
    await db.finish_bundle(b2)
    s = await db.stats_snapshot()
    check("статистика считает бандлы",
          s["b_total"] == 2 and s["b_new"] == 1 and s["b_done"] == 1)
    check("экран статистики собирается",
          "{" not in texts.ADMIN_STATS.format(
              users=1, users_24h=0, users_7d=0, readings=0, readings_24h=0,
              pay_count=0, rub=0.0, stars=0, subs=0, daily=0, daily_off=0,
              rate_up=0, rate_down=0, sources="—",
              b_total=s["b_total"], b_new=s["b_new"],
              b_active=s["b_active"], b_done=s["b_done"]))


async def main() -> None:
    test_texts()
    test_celtic()
    test_keyboards()
    test_economics()
    await test_topup()
    await test_topup_notify()
    await test_dialogue_cap()
    await test_review()
    await test_migration()
    await test_daily_personal()
    await test_daily_default()
    await test_mood_images()
    test_ladder()
    test_dialogue_tiers()
    test_bundle_config()
    test_bundle_prompts()
    test_bundle_serial()
    await test_purchases()
    await test_bundle_flow()
    await test_bundle_stats()
    print(f"\nИтого: ok {OK}, fail {FAIL}")
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
