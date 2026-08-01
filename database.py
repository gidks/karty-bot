"""SQLite-хранилище (aiosqlite). Пользователи, расклады, платежи, статистика.
Все даты храним в ISO 8601 в UTC."""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiosqlite

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    display_name TEXT,
    source TEXT,
    created_at TEXT NOT NULL,
    last_seen TEXT,
    free_readings_left INTEGER NOT NULL DEFAULT 0,
    paid_readings_left INTEGER NOT NULL DEFAULT 0,
    subscription_until TEXT,
    readings_count INTEGER NOT NULL DEFAULT 0,
    -- Утренняя карта дня включена по умолчанию: опт-ин показывался только тем,
    -- кто сам нажал «карта дня», и до него не доходил почти никто.
    daily_opt_in INTEGER NOT NULL DEFAULT 1,
    daily_default_applied INTEGER NOT NULL DEFAULT 0,
    last_daily_date TEXT,
    referrer_id INTEGER,
    referral_bonus_given INTEGER NOT NULL DEFAULT 0,
    nudge_sent INTEGER NOT NULL DEFAULT 0,
    sub_dialogue_date TEXT,
    sub_dialogue_count INTEGER NOT NULL DEFAULT 0,
    sub_dialogue_month TEXT,
    sub_dialogue_month_count INTEGER NOT NULL DEFAULT 0,
    last_review TEXT
);

CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    topic TEXT,
    question TEXT,
    cards TEXT,
    reading_text TEXT,
    created_at TEXT NOT NULL,
    followup_sent INTEGER NOT NULL DEFAULT 0,
    rating INTEGER,
    bundle_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_readings_user ON readings(user_id);
-- ⚠️ Индекс по readings.bundle_id создаётся НЕ здесь, а в _migrate: на старой
-- базе таблица readings уже существует (CREATE TABLE IF NOT EXISTS её не
-- трогает), колонки bundle_id в ней ещё нет, и CREATE INDEX падает раньше,
-- чем миграция успевает её добавить. То есть бот просто не стартует.

-- Бандл: одна ситуация, доведённая до конца, с расписанием.
-- status: 'new' — оплачен, но она ещё не начала (не ответила на вопросы входа);
--         'active' — день 0 сделан, шаги ждут своих дат;
--         'done' — финальное письмо отправлено.
CREATE TABLE IF NOT EXISTS bundles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    intro TEXT,                 -- JSON: ответы на вопросы входа
    marker TEXT,                -- маркер наблюдения из последнего расклада
    day0_reading_id INTEGER,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    reminded_at TEXT            -- когда позвали начать оплаченный, но не начатый
);
CREATE INDEX IF NOT EXISTS idx_bundles_user ON bundles(user_id, status);

-- Шаг бандла. Живёт в трёх состояниях:
--   asked_at IS NULL           — ещё не спрашивали;
--   asked_at задан, done = 0   — спросили про маркер, ждём её ответа;
--   done = 1                   — расклад шага выдан.
-- Ждём не бесконечно: на следующий день расклад уходит и без ответа —
-- он не должен блокироваться её молчанием, иначе это форма, а не разговор.
CREATE TABLE IF NOT EXISTS bundle_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    step_no INTEGER NOT NULL,
    due_date TEXT NOT NULL,     -- YYYY-MM-DD по Москве
    asked_at TEXT,
    answer TEXT,
    done INTEGER NOT NULL DEFAULT 0,
    reading_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_bundle_steps_due ON bundle_steps(done, due_date);
CREATE INDEX IF NOT EXISTS idx_bundle_steps_user ON bundle_steps(user_id, done);

CREATE TABLE IF NOT EXISTS payments (
    payment_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    plan TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    succeeded_at TEXT
);

CREATE TABLE IF NOT EXISTS seen_cards (
    user_id INTEGER NOT NULL,
    card_id INTEGER NOT NULL,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (user_id, card_id)
);

-- «Неделя»-сериал: расклад недели нарезан по дням, куски уходят по утрам
CREATE TABLE IF NOT EXISTS week_serials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    reading_id INTEGER NOT NULL,
    day_date TEXT NOT NULL,     -- YYYY-MM-DD по Москве
    day_label TEXT NOT NULL,    -- «Понедельник» и т.п.
    card_name TEXT,
    body TEXT NOT NULL,
    is_last INTEGER NOT NULL DEFAULT 0,
    sent INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_week_serials_due ON week_serials(sent, day_date);
"""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


async def init() -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.executescript(SCHEMA)
        await _migrate(db)
        cur = await db.execute("PRAGMA journal_mode=WAL")
        await cur.fetchall()  # PRAGMA возвращает строку — забираем, иначе commit падает
        await cur.close()
        await db.commit()


async def _migrate(db: aiosqlite.Connection) -> None:
    """Добавляет недостающие колонки в существующую базу. Безопасно запускать повторно."""
    async def ensure(table: str, column: str, ddl: str) -> None:
        cur = await db.execute(f"PRAGMA table_info({table})")
        cols = {r[1] for r in await cur.fetchall()}
        if column not in cols:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

    await ensure("readings", "rating", "rating INTEGER")
    await ensure("users", "nudge_sent", "nudge_sent INTEGER NOT NULL DEFAULT 0")
    await ensure("users", "daily_streak", "daily_streak INTEGER NOT NULL DEFAULT 0")
    await ensure("users", "best_streak", "best_streak INTEGER NOT NULL DEFAULT 0")
    # Суточный счётчик реплик у подписчиц и дата последнего «разбора месяца»
    await ensure("users", "sub_dialogue_date", "sub_dialogue_date TEXT")
    await ensure("users", "sub_dialogue_count",
                 "sub_dialogue_count INTEGER NOT NULL DEFAULT 0")
    await ensure("users", "sub_dialogue_month", "sub_dialogue_month TEXT")
    await ensure("users", "sub_dialogue_month_count",
                 "sub_dialogue_month_count INTEGER NOT NULL DEFAULT 0")
    await ensure("users", "last_review", "last_review TEXT")
    await ensure("users", "daily_default_applied",
                 "daily_default_applied INTEGER NOT NULL DEFAULT 0")
    # Связь расклада с бандлом: по ней собирается история разбора и ищется
    # повторяющаяся карта для финального письма
    await ensure("readings", "bundle_id", "bundle_id INTEGER")
    # Только теперь, когда колонка точно есть (см. комментарий в SCHEMA)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_readings_bundle ON readings(bundle_id)")

    # Разовое включение утренней карты дня тем, кто зарегистрировался до того,
    # как она стала опцией по умолчанию. Флаг daily_default_applied ставится
    # здесь же и при регистрации, поэтому осознанное «отключить» не отменится
    # при следующем рестарте.
    #
    # ⚠️ Пишем только когда есть кого чинить, и сразу коммитим: незакрытая
    # транзакция роняет следующий за миграцией PRAGMA journal_mode=WAL
    # («cannot change into wal mode from within a transaction») — то есть бот
    # просто не стартует на втором запуске.
    cur = await db.execute("SELECT COUNT(*) FROM users WHERE daily_default_applied = 0")
    pending = (await cur.fetchall())[0][0]
    await cur.close()
    if pending:
        await db.execute(
            """UPDATE users SET daily_opt_in = 1, daily_default_applied = 1
               WHERE daily_default_applied = 0"""
        )
        await db.commit()

    # Бэкфилл коллекции из уже сделанных раскладов (один раз, безопасно повторять)
    cur = await db.execute("SELECT COUNT(*) FROM seen_cards")
    count = (await cur.fetchall())[0][0]
    await cur.close()
    if count == 0:
        cur = await db.execute("SELECT user_id, cards, created_at FROM readings")
        rows = await cur.fetchall()
        await cur.close()
        for r in rows:
            try:
                for c in json.loads(r[1] or "[]"):
                    await db.execute(
                        """INSERT OR IGNORE INTO seen_cards (user_id, card_id, first_seen)
                           VALUES (?, ?, ?)""",
                        (r[0], int(c["id"]), r[2]),
                    )
            except (ValueError, KeyError, TypeError):
                continue


async def _conn() -> aiosqlite.Connection:
    db = await aiosqlite.connect(config.DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


# ---------- Пользователи ----------

async def get_user(user_id: int) -> aiosqlite.Row | None:
    db = await _conn()
    try:
        cur = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return await cur.fetchone()
    finally:
        await db.close()


async def create_user(
    user_id: int,
    username: str | None,
    first_name: str | None,
    referrer_id: int | None,
    source: str | None,
) -> None:
    db = await _conn()
    try:
        await db.execute(
            """INSERT OR IGNORE INTO users
               (user_id, username, first_name, source, created_at, last_seen,
                free_readings_left, referrer_id, daily_opt_in, daily_default_applied)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1)""",
            (user_id, username, first_name, source, now_iso(), now_iso(),
             config.FREE_READINGS, referrer_id),
        )
        await db.commit()
    finally:
        await db.close()


async def set_display_name(user_id: int, name: str) -> None:
    db = await _conn()
    try:
        await db.execute(
            "UPDATE users SET display_name = ? WHERE user_id = ?", (name, user_id))
        await db.commit()
    finally:
        await db.close()


async def touch(user_id: int, username: str | None = None) -> None:
    db = await _conn()
    try:
        await db.execute(
            "UPDATE users SET last_seen = ?, username = COALESCE(?, username) WHERE user_id = ?",
            (now_iso(), username, user_id),
        )
        await db.commit()
    finally:
        await db.close()


# ---------- Баланс раскладов ----------

def sub_active(row: aiosqlite.Row) -> bool:
    """Публичный помощник: активна ли подписка у строки пользователя."""
    until = row["subscription_until"]
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > now_utc()
    except ValueError:
        return False


_sub_active = sub_active  # обратная совместимость


async def readings_available(user_id: int) -> tuple[bool, str]:
    """Возвращает (доступен ли расклад, источник: 'sub' | 'paid' | 'free' | '')."""
    row = await get_user(user_id)
    if row is None:
        return False, ""
    if _sub_active(row):
        return True, "sub"
    if row["paid_readings_left"] > 0:
        return True, "paid"
    if row["free_readings_left"] > 0:
        return True, "free"
    return False, ""


async def consume_reading(user_id: int, source: str) -> None:
    if source == "sub":
        return
    col = "paid_readings_left" if source == "paid" else "free_readings_left"
    db = await _conn()
    try:
        await db.execute(
            f"UPDATE users SET {col} = MAX({col} - 1, 0) WHERE user_id = ?", (user_id,))
        await db.commit()
    finally:
        await db.close()


async def add_free_readings(user_id: int, n: int = 1) -> None:
    db = await _conn()
    try:
        await db.execute(
            "UPDATE users SET free_readings_left = free_readings_left + ? WHERE user_id = ?",
            (n, user_id),
        )
        await db.commit()
    finally:
        await db.close()


async def topup_notify_targets(days: int) -> list[aiosqlite.Row]:
    """Кому сказать про недельное пополнение: прошли онбординг, свободных
    раскладов не осталось, подписки нет, заходила не позже N дней назад.
    Вызывать ДО weekly_topup — иначе баланс уже не нулевой."""
    cutoff = (now_utc() - timedelta(days=days)).isoformat()
    db = await _conn()
    try:
        cur = await db.execute(
            """SELECT user_id, COALESCE(display_name, first_name, 'привет') AS name
               FROM users
               WHERE display_name IS NOT NULL
                 AND free_readings_left <= 0
                 AND paid_readings_left <= 0
                 AND (subscription_until IS NULL OR subscription_until <= ?)
                 AND last_seen >= ?""",
            (now_iso(), cutoff),
        )
        return list(await cur.fetchall())
    finally:
        await db.close()


async def weekly_topup(amount: int, cap: int) -> int:
    """Недельное пополнение бесплатных раскладов. Начисляем только тем, у кого
    меньше потолка и нет активной подписки — накопленное сверх потолка
    (рефералка, серия карты дня) не отбираем. Возвращает число пополненных."""
    if amount <= 0 or cap <= 0:
        return 0
    db = await _conn()
    try:
        cur = await db.execute(
            """UPDATE users
                  SET free_readings_left = MIN(free_readings_left + ?, ?)
                WHERE display_name IS NOT NULL
                  AND free_readings_left < ?
                  AND (subscription_until IS NULL OR subscription_until <= ?)""",
            (amount, cap, cap, now_iso()),
        )
        await db.commit()
        return cur.rowcount
    finally:
        await db.close()


# ---------- Разговор после расклада: суточный лимит у подписчиц ----------

async def sub_dialogue_check(
    user_id: int, date_str: str, limit_day: int, limit_month: int = 0,
) -> tuple[str, int]:
    """Проверка лимитов реплик подписчицы — БЕЗ списания (дата «YYYY-MM-DD»
    по Москве приходит снаружи). Списывает потом sub_dialogue_add, уже после
    успешного ответа модели: сорванная генерация не должна съедать бюджет.

    Возвращает (статус, использовано):
      "ok"    — можно отвечать;
      "day"   — исчерпан суточный потолок (использовано = за сутки);
      "month" — исчерпан месячный бюджет (использовано = за месяц)."""
    used_day, used_month = await sub_dialogue_used(user_id, date_str)
    if limit_month > 0 and used_month >= limit_month:
        return "month", used_month
    if limit_day > 0 and used_day >= limit_day:
        return "day", used_day
    return "ok", used_day


async def sub_dialogue_used(user_id: int, date_str: str) -> tuple[int, int]:
    """Сколько реплик израсходовано за сутки и за календарный месяц.
    Счётчики обнуляются сами при смене даты и месяца."""
    month_str = date_str[:7]
    db = await _conn()
    try:
        row = await (await db.execute(
            """SELECT sub_dialogue_date, sub_dialogue_count,
                      sub_dialogue_month, sub_dialogue_month_count
               FROM users WHERE user_id = ?""",
            (user_id,),
        )).fetchone()
        if row is None:
            return 0, 0
        used_day = (row["sub_dialogue_count"] or 0)
        if row["sub_dialogue_date"] != date_str:
            used_day = 0
        used_month = (row["sub_dialogue_month_count"] or 0)
        if row["sub_dialogue_month"] != month_str:
            used_month = 0
        return used_day, used_month
    finally:
        await db.close()


async def sub_dialogue_add(user_id: int, date_str: str) -> tuple[int, int]:
    """Списывает одну реплику из суточного и месячного счётчиков.
    Возвращает новые значения (за сутки, за месяц)."""
    month_str = date_str[:7]
    used_day, used_month = await sub_dialogue_used(user_id, date_str)
    db = await _conn()
    try:
        await db.execute(
            """UPDATE users SET sub_dialogue_date = ?, sub_dialogue_count = ?,
                                sub_dialogue_month = ?, sub_dialogue_month_count = ?
               WHERE user_id = ?""",
            (date_str, used_day + 1, month_str, used_month + 1, user_id),
        )
        await db.commit()
        return used_day + 1, used_month + 1
    finally:
        await db.close()


# ---------- «Разбор месяца» (по подписке) ----------

async def review_readings(user_id: int, n: int) -> list[aiosqlite.Row]:
    """Последние расклады для «разбора месяца» — от старых к новым."""
    db = await _conn()
    try:
        cur = await db.execute(
            """SELECT topic, question, cards, created_at, rating FROM readings
               WHERE user_id = ? ORDER BY id DESC LIMIT ?""",
            (user_id, n),
        )
        return list(reversed(await cur.fetchall()))
    finally:
        await db.close()


async def set_review_done(user_id: int) -> None:
    db = await _conn()
    try:
        await db.execute(
            "UPDATE users SET last_review = ? WHERE user_id = ?", (now_iso(), user_id))
        await db.commit()
    finally:
        await db.close()


def review_days_left(row: aiosqlite.Row, cooldown_days: int) -> int:
    """Сколько дней осталось до следующего разбора. 0 — можно прямо сейчас."""
    last = row["last_review"] if "last_review" in row.keys() else None
    if not last:
        return 0
    try:
        passed = (now_utc() - datetime.fromisoformat(last)).days
    except ValueError:
        return 0
    return max(0, cooldown_days - passed)


async def apply_purchase(user_id: int, plan_key: str) -> int | None:
    """Начисляет купленное. Возвращает id созданного бандла — или None.

    Тариф ищем через config.plan(): в незакрытых платежах могут остаться
    ключи, снятые с продажи (например 'week'). Человек мог получить ссылку
    до обновления и нажать «Я оплатила» после — деньги списаны, и мы обязаны
    отработать их по старым условиям, а не упасть с KeyError."""
    plan = config.plan(plan_key)
    if plan is None:
        return None
    kind = plan.get("kind") or ("single" if plan["days"] is None else "sub")

    # Бандл — не баланс, а запущенный сценарий: создаём его и отдаём id,
    # чтобы бот тут же предложил начать.
    if kind == "bundle":
        return await create_bundle(user_id, plan["bundle"])

    db = await _conn()
    try:
        if plan["days"] is None:
            n = int(plan.get("readings") or 1)
            await db.execute(
                "UPDATE users SET paid_readings_left = paid_readings_left + ? "
                "WHERE user_id = ?",
                (n, user_id),
            )
        else:
            row = await (await db.execute(
                "SELECT subscription_until FROM users WHERE user_id = ?", (user_id,)
            )).fetchone()
            base = now_utc()
            if row and row["subscription_until"]:
                try:
                    current = datetime.fromisoformat(row["subscription_until"])
                    if current > base:
                        base = current
                except ValueError:
                    pass
            new_until = base + timedelta(days=plan["days"])
            await db.execute(
                "UPDATE users SET subscription_until = ? WHERE user_id = ?",
                (new_until.isoformat(), user_id),
            )
        await db.commit()
        return None
    finally:
        await db.close()


# ---------- Бандлы ----------

async def create_bundle(user_id: int, kind: str) -> int:
    db = await _conn()
    try:
        cur = await db.execute(
            "INSERT INTO bundles (user_id, kind, status, created_at) "
            "VALUES (?, ?, 'new', ?)",
            (user_id, kind, now_iso()),
        )
        await db.commit()
        return cur.lastrowid
    finally:
        await db.close()


async def get_bundle(bundle_id: int) -> aiosqlite.Row | None:
    db = await _conn()
    try:
        cur = await db.execute("SELECT * FROM bundles WHERE id = ?", (bundle_id,))
        return await cur.fetchone()
    finally:
        await db.close()


async def open_bundles(user_id: int, kind: str | None = None) -> list[aiosqlite.Row]:
    """Незавершённые бандлы человека (оплаченные, но не доигранные)."""
    db = await _conn()
    try:
        sql = "SELECT * FROM bundles WHERE user_id = ? AND status != 'done'"
        args: tuple = (user_id,)
        if kind:
            sql += " AND kind = ?"
            args += (kind,)
        cur = await db.execute(sql + " ORDER BY id", args)
        return list(await cur.fetchall())
    finally:
        await db.close()


async def start_bundle(
    bundle_id: int, intro: dict, day0_reading_id: int,
    schedule: list[tuple[int, int]], marker: str | None = None,
) -> None:
    """День 0 сделан: фиксируем ответы входа, расклад и расписание шагов.

    schedule — [(step_no, дней_от_сегодня), …]. Даты считаем один раз здесь,
    чтобы сдвиг настроек в .env не переписывал расписание уже идущих разборов."""
    today = datetime.now(timezone.utc)
    db = await _conn()
    try:
        await db.execute(
            """UPDATE bundles SET status = 'active', intro = ?, marker = ?,
                                  day0_reading_id = ?, started_at = ?
               WHERE id = ?""",
            (json.dumps(intro, ensure_ascii=False), marker, day0_reading_id,
             now_iso(), bundle_id),
        )
        row = await (await db.execute(
            "SELECT user_id FROM bundles WHERE id = ?", (bundle_id,))).fetchone()
        user_id = row["user_id"] if row else 0
        # Пересоздаём шаги: повторный запуск того же бандла не должен
        # оставлять два расписания
        await db.execute("DELETE FROM bundle_steps WHERE bundle_id = ?", (bundle_id,))
        for step_no, days in schedule:
            due = (today + timedelta(days=days)).astimezone(
                ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d")
            await db.execute(
                """INSERT INTO bundle_steps (bundle_id, user_id, step_no, due_date)
                   VALUES (?, ?, ?, ?)""",
                (bundle_id, user_id, step_no, due),
            )
        await db.commit()
    finally:
        await db.close()


async def unstarted_bundles(hours: int = 20) -> list[aiosqlite.Row]:
    """Оплачен, но так и не начат. Самый дорогой из возможных провалов:
    человек отдал деньги и не получил ничего — напоминаем ровно один раз."""
    cutoff = (now_utc() - timedelta(hours=hours)).isoformat()
    db = await _conn()
    try:
        cur = await db.execute(
            """SELECT b.*, COALESCE(u.display_name, u.first_name, 'дорогая') AS name
               FROM bundles b JOIN users u ON u.user_id = b.user_id
               WHERE b.status = 'new' AND b.reminded_at IS NULL
                 AND b.created_at <= ?""",
            (cutoff,),
        )
        return list(await cur.fetchall())
    finally:
        await db.close()


async def mark_bundle_reminded(bundle_id: int) -> None:
    db = await _conn()
    try:
        await db.execute(
            "UPDATE bundles SET reminded_at = ? WHERE id = ?", (now_iso(), bundle_id))
        await db.commit()
    finally:
        await db.close()


async def set_bundle_marker(bundle_id: int, marker: str | None) -> None:
    db = await _conn()
    try:
        await db.execute(
            "UPDATE bundles SET marker = ? WHERE id = ?", (marker, bundle_id))
        await db.commit()
    finally:
        await db.close()


async def finish_bundle(bundle_id: int) -> None:
    db = await _conn()
    try:
        await db.execute(
            "UPDATE bundles SET status = 'done', finished_at = ? WHERE id = ?",
            (now_iso(), bundle_id),
        )
        await db.commit()
    finally:
        await db.close()


async def due_bundle_steps(today: str) -> list[aiosqlite.Row]:
    """Шаги, до которых дошла дата и о которых ещё не спрашивали.

    ⚠️ Не больше одного шага на разбор за раз (условие по MIN(step_no)).
    Иначе после суток простоя бот вываливал бы человеку весь бандл за одно
    утро: и вопрос третьего дня, и седьмого, и сразу закрывающее письмо.
    С этим условием расписание само нагоняет отставание — по шагу в день,
    и порядок никогда не нарушается."""
    db = await _conn()
    try:
        cur = await db.execute(
            """SELECT s.*, b.kind, b.intro, b.marker, b.status,
                      COALESCE(u.display_name, u.first_name, 'дорогая') AS name
               FROM bundle_steps s
               JOIN bundles b ON b.id = s.bundle_id
               JOIN users u ON u.user_id = s.user_id
               WHERE s.done = 0 AND s.asked_at IS NULL AND s.due_date <= ?
                 AND b.status = 'active'
                 AND s.step_no = (SELECT MIN(step_no) FROM bundle_steps s2
                                   WHERE s2.bundle_id = s.bundle_id AND s2.done = 0)
               ORDER BY s.due_date, s.step_no""",
            (today,),
        )
        return list(await cur.fetchall())
    finally:
        await db.close()


async def overdue_bundle_steps(today: str) -> list[aiosqlite.Row]:
    """Шаги, про которые спросили раньше сегодняшнего дня, а ответа нет.
    Их пора раскладывать без ответа: разбор не должен вставать из-за молчания."""
    db = await _conn()
    try:
        cur = await db.execute(
            """SELECT s.*, b.kind, b.intro, b.marker, b.status,
                      COALESCE(u.display_name, u.first_name, 'дорогая') AS name
               FROM bundle_steps s
               JOIN bundles b ON b.id = s.bundle_id
               JOIN users u ON u.user_id = s.user_id
               WHERE s.done = 0 AND s.asked_at IS NOT NULL
                 AND substr(s.asked_at, 1, 10) < ?
                 AND b.status = 'active'
                 AND s.step_no = (SELECT MIN(step_no) FROM bundle_steps s2
                                   WHERE s2.bundle_id = s.bundle_id AND s2.done = 0)
               ORDER BY s.due_date, s.step_no""",
            (today,),
        )
        return list(await cur.fetchall())
    finally:
        await db.close()


async def get_bundle_step(step_id: int) -> aiosqlite.Row | None:
    db = await _conn()
    try:
        cur = await db.execute(
            """SELECT s.*, b.kind, b.intro, b.marker, b.status
               FROM bundle_steps s JOIN bundles b ON b.id = s.bundle_id
               WHERE s.id = ?""",
            (step_id,),
        )
        return await cur.fetchone()
    finally:
        await db.close()


async def awaiting_step(user_id: int) -> aiosqlite.Row | None:
    """Шаг, про который человека спросили и ждут ответа. Нужен, чтобы поймать
    свободный текст в чате: она отвечает боту, а не жмёт кнопку."""
    db = await _conn()
    try:
        cur = await db.execute(
            """SELECT s.*, b.kind, b.intro, b.marker
               FROM bundle_steps s JOIN bundles b ON b.id = s.bundle_id
               WHERE s.user_id = ? AND s.done = 0 AND s.asked_at IS NOT NULL
                 AND b.status = 'active'
               ORDER BY s.asked_at DESC LIMIT 1""",
            (user_id,),
        )
        return await cur.fetchone()
    finally:
        await db.close()


async def mark_step_asked(step_id: int) -> None:
    db = await _conn()
    try:
        await db.execute(
            "UPDATE bundle_steps SET asked_at = ? WHERE id = ?", (now_iso(), step_id))
        await db.commit()
    finally:
        await db.close()


async def set_step_answer(step_id: int, answer: str) -> None:
    db = await _conn()
    try:
        await db.execute(
            "UPDATE bundle_steps SET answer = ? WHERE id = ?",
            (answer[:1000], step_id),
        )
        await db.commit()
    finally:
        await db.close()


async def complete_step(step_id: int, reading_id: int | None = None) -> bool:
    """Шаг закрыт. Возвращает True только при первом закрытии — защита от
    двойной выдачи, если она нажала кнопку и одновременно сработала рассылка."""
    db = await _conn()
    try:
        cur = await db.execute(
            "UPDATE bundle_steps SET done = 1, reading_id = ? WHERE id = ? AND done = 0",
            (reading_id, step_id),
        )
        await db.commit()
        return cur.rowcount == 1
    finally:
        await db.close()


async def claim_step(step_id: int) -> bool:
    """Атомарно занимает шаг под выдачу: помечает done=1 до генерации.
    Иначе долгая генерация успевает пересечься со следующим тиком рассылки,
    и человек получает один и тот же расклад дважды."""
    return await complete_step(step_id, None)


async def release_step(step_id: int) -> None:
    """Вернуть шаг в очередь, если генерация сорвалась."""
    db = await _conn()
    try:
        await db.execute(
            "UPDATE bundle_steps SET done = 0 WHERE id = ? AND reading_id IS NULL",
            (step_id,),
        )
        await db.commit()
    finally:
        await db.close()


async def attach_reading_to_step(step_id: int, reading_id: int) -> None:
    db = await _conn()
    try:
        await db.execute(
            "UPDATE bundle_steps SET reading_id = ? WHERE id = ?",
            (reading_id, step_id),
        )
        await db.commit()
    finally:
        await db.close()


async def bundle_readings(bundle_id: int) -> list[aiosqlite.Row]:
    """Все расклады разбора по порядку — материал для следующего шага
    и для финального письма."""
    db = await _conn()
    try:
        cur = await db.execute(
            """SELECT id, topic, question, cards, reading_text, created_at
               FROM readings WHERE bundle_id = ? ORDER BY id""",
            (bundle_id,),
        )
        return list(await cur.fetchall())
    finally:
        await db.close()


async def bundle_answers(bundle_id: int) -> list[str]:
    """Что она рассказывала между шагами, по порядку."""
    db = await _conn()
    try:
        cur = await db.execute(
            """SELECT answer FROM bundle_steps
               WHERE bundle_id = ? AND answer IS NOT NULL AND answer != ''
               ORDER BY step_no""",
            (bundle_id,),
        )
        return [r["answer"] for r in await cur.fetchall()]
    finally:
        await db.close()


async def bundle_repeat_card(bundle_id: int) -> str | None:
    """Карта, выпавшая за разбор больше одного раза. Именно она делает
    финальное письмо тем, чего не даст ни один отдельный расклад."""
    rows = await bundle_readings(bundle_id)
    counts: dict[str, int] = {}
    for r in rows:
        try:
            for c in json.loads(r["cards"] or "[]"):
                name = str(c.get("name") or "").strip()
                if name:
                    counts[name] = counts.get(name, 0) + 1
        except (ValueError, TypeError, KeyError):
            continue
    if not counts:
        return None
    name, n = max(counts.items(), key=lambda x: x[1])
    return f"{name} — {n} раза" if n > 1 else None


# ---------- Расклады ----------

async def add_reading(
    user_id: int, topic: str, question: str, drawn: list[dict], reading_text: str,
    bundle_id: int | None = None,
) -> tuple[int, int]:
    """Сохраняет расклад. Возвращает (reading_id, порядковый номер расклада у пользователя)."""
    cards_json = json.dumps(
        [{"id": c["id"], "name": c["name"], "rev": c["rev"]} for c in drawn],
        ensure_ascii=False,
    )
    db = await _conn()
    try:
        cur = await db.execute(
            """INSERT INTO readings (user_id, topic, question, cards, reading_text,
                                     created_at, bundle_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, topic, question[:1000], cards_json, reading_text, now_iso(),
             bundle_id),
        )
        reading_id = cur.lastrowid
        await db.execute(
            "UPDATE users SET readings_count = readings_count + 1 WHERE user_id = ?",
            (user_id,),
        )
        for c in drawn:  # коллекция встреченных карт
            await db.execute(
                """INSERT OR IGNORE INTO seen_cards (user_id, card_id, first_seen)
                   VALUES (?, ?, ?)""",
                (user_id, int(c["id"]), now_iso()),
            )
        row = await (await db.execute(
            "SELECT readings_count FROM users WHERE user_id = ?", (user_id,)
        )).fetchone()
        await db.commit()
        return reading_id, (row["readings_count"] if row else 0)
    finally:
        await db.close()


async def set_reading_rating(reading_id: int, user_id: int, value: int) -> bool:
    """Оценка расклада (1 = 👍, -1 = 👎). user_id в WHERE — чужой расклад не оценить."""
    db = await _conn()
    try:
        cur = await db.execute(
            "UPDATE readings SET rating = ? WHERE id = ? AND user_id = ?",
            (value, reading_id, user_id),
        )
        await db.commit()
        return cur.rowcount == 1
    finally:
        await db.close()


async def get_reading(reading_id: int) -> aiosqlite.Row | None:
    db = await _conn()
    try:
        cur = await db.execute("SELECT * FROM readings WHERE id = ?", (reading_id,))
        return await cur.fetchone()
    finally:
        await db.close()


async def spent_rub(user_id: int) -> float:
    """Сколько человек уже заплатил рублями. Нужно, чтобы предлагать подписку
    арифметикой («ты взяла на 300 ₽, месяц стоит 299»), а не витриной."""
    db = await _conn()
    try:
        row = await (await db.execute(
            """SELECT COALESCE(SUM(amount), 0) FROM payments
               WHERE user_id = ? AND status = 'succeeded' AND currency = 'RUB'""",
            (user_id,),
        )).fetchone()
        return float(row[0] or 0)
    finally:
        await db.close()


async def paid_count(user_id: int) -> int:
    db = await _conn()
    try:
        row = await (await db.execute(
            "SELECT COUNT(*) FROM payments WHERE user_id = ? AND status = 'succeeded'",
            (user_id,),
        )).fetchone()
        return int(row[0] or 0)
    finally:
        await db.close()


async def last_readings(user_id: int, n: int = 5) -> list[aiosqlite.Row]:
    db = await _conn()
    try:
        cur = await db.execute(
            "SELECT * FROM readings WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, n),
        )
        return list(await cur.fetchall())
    finally:
        await db.close()


async def history(user_id: int) -> dict:
    """Личная история: сколько раскладов, самая частая карта, любимая тема,
    сколько 👍 и последние 5 раскладов."""
    db = await _conn()
    try:
        cur = await db.execute(
            """SELECT topic, cards, created_at, rating FROM readings
               WHERE user_id = ? ORDER BY id DESC""",
            (user_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
    finally:
        await db.close()

    card_counts: dict[int, int] = {}
    topic_counts: dict[str, int] = {}
    rate_up = 0
    for r in rows:
        try:
            for c in json.loads(r["cards"] or "[]"):
                cid = int(c["id"])
                card_counts[cid] = card_counts.get(cid, 0) + 1
        except (ValueError, KeyError, TypeError):
            pass
        if r["topic"]:
            topic_counts[r["topic"]] = topic_counts.get(r["topic"], 0) + 1
        if r["rating"] == 1:
            rate_up += 1

    top_card = max(card_counts.items(), key=lambda x: x[1]) if card_counts else None
    top_topic = max(topic_counts.items(), key=lambda x: x[1]) if topic_counts else None
    return {
        "total": len(rows),
        "first_date": rows[-1]["created_at"] if rows else None,
        "top_card": top_card,      # (card_id, count) | None
        "top_topic": top_topic,    # (topic, count) | None
        "rate_up": rate_up,
        "last": rows[:5],
    }


# ---------- Коллекция встреченных карт ----------

async def record_seen(user_id: int, card_ids: list[int]) -> None:
    db = await _conn()
    try:
        for cid in card_ids:
            await db.execute(
                """INSERT OR IGNORE INTO seen_cards (user_id, card_id, first_seen)
                   VALUES (?, ?, ?)""",
                (user_id, int(cid), now_iso()),
            )
        await db.commit()
    finally:
        await db.close()


async def collection(user_id: int) -> dict:
    """Прогресс коллекции: какие id встречены + три последних новых."""
    db = await _conn()
    try:
        cur = await db.execute(
            "SELECT card_id FROM seen_cards WHERE user_id = ?", (user_id,))
        seen = {r["card_id"] for r in await cur.fetchall()}
        cur = await db.execute(
            """SELECT card_id FROM seen_cards WHERE user_id = ?
               ORDER BY first_seen DESC LIMIT 3""",
            (user_id,),
        )
        recent = [r["card_id"] for r in await cur.fetchall()]
        return {"seen": seen, "recent": recent}
    finally:
        await db.close()


# ---------- Карта дня ----------

async def set_daily_opt(user_id: int, value: bool) -> None:
    db = await _conn()
    try:
        await db.execute(
            "UPDATE users SET daily_opt_in = ? WHERE user_id = ?",
            (1 if value else 0, user_id),
        )
        await db.commit()
    finally:
        await db.close()


async def set_last_daily(user_id: int, date_str: str) -> None:
    db = await _conn()
    try:
        await db.execute(
            "UPDATE users SET last_daily_date = ? WHERE user_id = ?", (date_str, user_id))
        await db.commit()
    finally:
        await db.close()


async def record_daily(
    user_id: int, date_str: str, card_id: int, reward_every: int = 0,
) -> dict:
    """Карта дня получена (сама или утренней рассылкой): серия, рекорд, коллекция.
    reward_every > 0 — за каждые N дней подряд начисляется +1 бесплатный расклад.
    Возвращает {'streak', 'best', 'is_new', 'reward'}."""
    db = await _conn()
    try:
        row = await (await db.execute(
            """SELECT last_daily_date, daily_streak, best_streak
               FROM users WHERE user_id = ?""",
            (user_id,),
        )).fetchone()
        if row is None:
            return {"streak": 0, "best": 0, "is_new": False, "reward": False}

        streak = row["daily_streak"] or 0
        best = row["best_streak"] or 0
        is_new = row["last_daily_date"] != date_str
        if is_new:
            try:
                yesterday = (
                    datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)
                ).strftime("%Y-%m-%d")
            except ValueError:
                yesterday = ""
            streak = streak + 1 if row["last_daily_date"] == yesterday else 1
            best = max(best, streak)

        reward = bool(
            is_new and reward_every > 0 and streak > 0 and streak % reward_every == 0
        )
        await db.execute(
            """UPDATE users SET last_daily_date = ?, daily_streak = ?, best_streak = ?,
                                free_readings_left = free_readings_left + ?
               WHERE user_id = ?""",
            (date_str, streak, best, 1 if reward else 0, user_id),
        )
        await db.execute(
            """INSERT OR IGNORE INTO seen_cards (user_id, card_id, first_seen)
               VALUES (?, ?, ?)""",
            (user_id, int(card_id), now_iso()),
        )
        await db.commit()
        return {"streak": streak, "best": best, "is_new": is_new, "reward": reward}
    finally:
        await db.close()


async def daily_optin_users() -> list[aiosqlite.Row]:
    db = await _conn()
    try:
        cur = await db.execute(
            """SELECT user_id, display_name, first_name, last_daily_date,
                      subscription_until
               FROM users
               WHERE daily_opt_in = 1 AND readings_count > 0
               ORDER BY (subscription_until IS NOT NULL) DESC"""
        )
        return list(await cur.fetchall())
    finally:
        await db.close()


# ---------- «Неделя»-сериал ----------

async def save_week_serial(user_id: int, reading_id: int, rows: list[dict]) -> None:
    """Сохраняет нарезанный по дням расклад недели. Новый расклад недели
    заменяет прежний: его неотправленные дни снимаются с рассылки."""
    db = await _conn()
    try:
        await db.execute(
            "DELETE FROM week_serials WHERE user_id = ? AND sent = 0", (user_id,))
        for r in rows:
            await db.execute(
                """INSERT INTO week_serials
                   (user_id, reading_id, day_date, day_label, card_name, body,
                    is_last, sent)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, reading_id, r["day_date"], r["day_label"],
                 r.get("card_name"), r["body"], r.get("is_last", 0),
                 r.get("sent", 0)),
            )
        await db.commit()
    finally:
        await db.close()


async def due_week_serials(today: str) -> list[aiosqlite.Row]:
    """Неотправленные куски с датой не позже сегодняшней.
    Куски со старой датой рассылка молча помечает отправленными (не спамим
    задним числом, если бот был выключен)."""
    db = await _conn()
    try:
        cur = await db.execute(
            """SELECT id, user_id, day_date, day_label, card_name, body, is_last
               FROM week_serials WHERE sent = 0 AND day_date <= ?
               ORDER BY day_date""",
            (today,),
        )
        return list(await cur.fetchall())
    finally:
        await db.close()


async def mark_week_serial_sent(row_id: int) -> None:
    db = await _conn()
    try:
        await db.execute(
            "UPDATE week_serials SET sent = 1 WHERE id = ?", (row_id,))
        await db.commit()
    finally:
        await db.close()


# ---------- Рефералы ----------

async def mark_referral_bonus_given(user_id: int) -> None:
    db = await _conn()
    try:
        await db.execute(
            "UPDATE users SET referral_bonus_given = 1 WHERE user_id = ?", (user_id,))
        await db.commit()
    finally:
        await db.close()


# ---------- Платежи ----------

async def create_payment_row(
    payment_id: str, user_id: int, plan: str, amount: float,
    currency: str, provider: str, status: str,
) -> bool:
    """Возвращает True, если строка создана впервые (для дедупликации Stars)."""
    db = await _conn()
    try:
        cur = await db.execute(
            """INSERT OR IGNORE INTO payments
               (payment_id, user_id, plan, amount, currency, provider, status, created_at,
                succeeded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (payment_id, user_id, plan, amount, currency, provider, status, now_iso(),
             now_iso() if status == "succeeded" else None),
        )
        await db.commit()
        return cur.rowcount == 1
    finally:
        await db.close()


async def mark_succeeded_once(payment_id: str) -> bool:
    """Атомарно переводит платёж в succeeded. True — только при первом успехе:
    защита от двойного начисления, если «Я оплатила» нажали несколько раз."""
    db = await _conn()
    try:
        cur = await db.execute(
            """UPDATE payments SET status = 'succeeded', succeeded_at = ?
               WHERE payment_id = ? AND status != 'succeeded'""",
            (now_iso(), payment_id),
        )
        await db.commit()
        return cur.rowcount == 1
    finally:
        await db.close()


async def get_payment(payment_id: str) -> aiosqlite.Row | None:
    db = await _conn()
    try:
        cur = await db.execute(
            "SELECT * FROM payments WHERE payment_id = ?", (payment_id,))
        return await cur.fetchone()
    finally:
        await db.close()


# ---------- Follow-up через N дней ----------

async def due_followups(days: int) -> list[aiosqlite.Row]:
    cutoff = (now_utc() - timedelta(days=days)).isoformat()
    db = await _conn()
    try:
        cur = await db.execute(
            """SELECT r.id, r.user_id, r.topic, r.cards,
                      COALESCE(u.display_name, u.first_name, 'привет') AS name
               FROM readings r
               JOIN users u ON u.user_id = r.user_id
               WHERE r.followup_sent = 0
                 AND r.created_at <= ?
                 AND r.id = (SELECT MAX(id) FROM readings r2 WHERE r2.user_id = r.user_id)""",
            (cutoff,),
        )
        return list(await cur.fetchall())
    finally:
        await db.close()


async def mark_followup_sent(reading_id: int) -> None:
    db = await _conn()
    try:
        await db.execute(
            "UPDATE readings SET followup_sent = 1 WHERE id = ?", (reading_id,))
        await db.commit()
    finally:
        await db.close()


# ---------- Напоминание о неиспользованных бесплатных раскладах ----------

async def due_nudges(days: int) -> list[aiosqlite.Row]:
    """Прошли онбординг, есть бесплатные расклады, не заходили N дней, не напоминали."""
    cutoff = (now_utc() - timedelta(days=days)).isoformat()
    db = await _conn()
    try:
        cur = await db.execute(
            """SELECT user_id,
                      COALESCE(display_name, first_name, 'привет') AS name,
                      free_readings_left
               FROM users
               WHERE nudge_sent = 0
                 AND free_readings_left > 0
                 AND display_name IS NOT NULL
                 AND last_seen <= ?""",
            (cutoff,),
        )
        return list(await cur.fetchall())
    finally:
        await db.close()


async def mark_nudge_sent(user_id: int) -> None:
    db = await _conn()
    try:
        await db.execute(
            "UPDATE users SET nudge_sent = 1 WHERE user_id = ?", (user_id,))
        await db.commit()
    finally:
        await db.close()


# ---------- Админка ----------

async def all_user_ids() -> list[int]:
    db = await _conn()
    try:
        cur = await db.execute("SELECT user_id FROM users")
        return [r["user_id"] for r in await cur.fetchall()]
    finally:
        await db.close()


async def stats_snapshot() -> dict:
    day_ago = (now_utc() - timedelta(days=1)).isoformat()
    week_ago = (now_utc() - timedelta(days=7)).isoformat()
    db = await _conn()
    try:
        async def one(sql: str, args: tuple = ()) -> float:
            row = await (await db.execute(sql, args)).fetchone()
            return row[0] if row and row[0] is not None else 0

        users = await one("SELECT COUNT(*) FROM users")
        users_24h = await one("SELECT COUNT(*) FROM users WHERE created_at >= ?", (day_ago,))
        users_7d = await one("SELECT COUNT(*) FROM users WHERE created_at >= ?", (week_ago,))
        readings = await one("SELECT COUNT(*) FROM readings")
        readings_24h = await one(
            "SELECT COUNT(*) FROM readings WHERE created_at >= ?", (day_ago,))
        pay_count = await one(
            "SELECT COUNT(*) FROM payments WHERE status = 'succeeded'")
        rub = await one(
            "SELECT SUM(amount) FROM payments WHERE status='succeeded' AND currency='RUB'")
        stars = await one(
            "SELECT SUM(amount) FROM payments WHERE status='succeeded' AND currency='XTR'")
        subs = await one(
            "SELECT COUNT(*) FROM users WHERE subscription_until > ?", (now_iso(),))
        daily = await one("SELECT COUNT(*) FROM users WHERE daily_opt_in = 1")
        # Отписки от утренней карты: карта дня теперь включена по умолчанию,
        # поэтому важна не доля подписанных, а доля тех, кто выключил руками.
        daily_off = await one(
            """SELECT COUNT(*) FROM users
               WHERE daily_opt_in = 0 AND daily_default_applied = 1"""
        )
        rate_up = await one("SELECT COUNT(*) FROM readings WHERE rating = 1")
        rate_down = await one("SELECT COUNT(*) FROM readings WHERE rating = -1")
        # Бандлы: куплено всего, идёт сейчас, доиграно до финального письма.
        # 'new' отдельно — это оплаченные, но так и не начатые: если их много,
        # значит экран «начнём?» после оплаты не работает.
        b_total = await one("SELECT COUNT(*) FROM bundles")
        b_new = await one("SELECT COUNT(*) FROM bundles WHERE status = 'new'")
        b_active = await one("SELECT COUNT(*) FROM bundles WHERE status = 'active'")
        b_done = await one("SELECT COUNT(*) FROM bundles WHERE status = 'done'")

        # По источнику важны не старты, а поведение: шесть человек, сделавших
        # по три расклада, и шесть отвалившихся после первого — разные шесть.
        cur = await db.execute(
            """SELECT COALESCE(source, 'organic')                   AS src,
                      COUNT(*)                                      AS n,
                      COALESCE(SUM(readings_count), 0)              AS reads,
                      SUM(CASE WHEN readings_count > 0 THEN 1 ELSE 0 END)      AS active,
                      SUM(CASE WHEN free_readings_left = 0
                                AND readings_count > 0 THEN 1 ELSE 0 END)      AS spent
               FROM users GROUP BY src ORDER BY n DESC LIMIT 8"""
        )
        sources = [
            (r["src"], r["n"], r["reads"], r["active"], r["spent"])
            for r in await cur.fetchall()
        ]

        return {
            "users": int(users), "users_24h": int(users_24h), "users_7d": int(users_7d),
            "readings": int(readings), "readings_24h": int(readings_24h),
            "pay_count": int(pay_count), "rub": float(rub), "stars": int(stars),
            "subs": int(subs), "daily": int(daily), "daily_off": int(daily_off),
            "sources": sources,
            "rate_up": int(rate_up), "rate_down": int(rate_down),
            "b_total": int(b_total), "b_new": int(b_new),
            "b_active": int(b_active), "b_done": int(b_done),
        }
    finally:
        await db.close()
