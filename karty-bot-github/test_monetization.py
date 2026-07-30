"""Тесты изменений монетизации: обновляемый лимит, суточный лимит диалога,
разбор месяца, Кельтский крест, тексты пейволла. Запуск: python3 test_monetization.py"""

import asyncio
import os
import sys
import tempfile

os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("LLM_API_KEY", "test")
os.environ.setdefault("ADMIN_IDS", "1")

DB_FILE = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ["DB_PATH"] = DB_FILE

import aiosqlite  # noqa: E402

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

    paywall = texts.PAYWALL.format(
        name="Аня", topup=texts.topup_promise(),
        p_single=config.PRICE_SINGLE_RUB, p_week=config.PRICE_WEEK_RUB,
        p_month=config.PRICE_MONTH_RUB, benefits=texts.sub_benefits(),
    )
    check("PAYWALL собирается", "79" in paywall and "Кельтский крест" in paywall)
    check("PAYWALL обещает пополнение", "начислю" in paywall)
    check("PAYWALL без «без лимита»", "без лимита" not in paywall)

    tariffs = texts.TARIFFS.format(
        p_single=79, p_week=249, p_month=599, left="свободных раскладов нет",
        benefits=texts.sub_benefits(), free_note=texts.free_note(),
    )
    check("TARIFFS собирается", "Разбор месяца" in tariffs)

    check("HELP собирается",
          "Кельтский крест" in texts.HELP.format(
              free_terms=texts.free_terms(), support="@s"))
    check("CELTIC_LOCKED собирается",
          "249" in texts.CELTIC_LOCKED.format(
              benefits=texts.sub_benefits(), p_week=249, p_month=599))
    check("REVIEW_LOCKED собирается",
          "599" in texts.REVIEW_LOCKED.format(
              benefits=texts.sub_benefits(), p_week=249, p_month=599))
    check("REVIEW_NOT_ENOUGH собирается",
          "3" in texts.REVIEW_NOT_ENOUGH.format(n=3, have="1 расклад"))
    check("REVIEW_COOLDOWN собирается",
          "дней" in texts.REVIEW_COOLDOWN.format(days=texts.days_phrase(5)))
    check("REVIEW_HEADER собирается",
          "5 раскладов" in texts.REVIEW_HEADER.format(n=texts.readings_phrase(5)))
    check("DIALOGUE_LIMIT собирается",
          "30" in texts.DIALOGUE_LIMIT.format(sub=30, free=5))
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
    check("sub_plans: только подписки",
          [b.callback_data for r in kb.sub_plans().inline_keyboard for b in r][:2]
          == ["buy:week", "buy:month"])
    check("sub_plans: настраиваемая кнопка «назад»",
          any(b.callback_data == "menu"
              for r in kb.sub_plans(back="menu").inline_keyboard for b in r))
    check("цена разового в тарифах — 79",
          any("79" in b.text for r in kb.plans().inline_keyboard for b in r))


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
    print(f"\nИтого: ok {OK}, fail {FAIL}")
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
