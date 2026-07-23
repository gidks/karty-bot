"""SQLite-хранилище (aiosqlite). Пользователи, расклады, платежи, статистика.
Все даты храним в ISO 8601 в UTC."""

import json
from datetime import datetime, timedelta, timezone

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
    daily_opt_in INTEGER NOT NULL DEFAULT 0,
    last_daily_date TEXT,
    referrer_id INTEGER,
    referral_bonus_given INTEGER NOT NULL DEFAULT 0,
    nudge_sent INTEGER NOT NULL DEFAULT 0
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
    rating INTEGER
);
CREATE INDEX IF NOT EXISTS idx_readings_user ON readings(user_id);

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
                free_readings_left, referrer_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
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


async def apply_purchase(user_id: int, plan_key: str) -> None:
    plan = config.PLANS[plan_key]
    db = await _conn()
    try:
        if plan["days"] is None:
            await db.execute(
                "UPDATE users SET paid_readings_left = paid_readings_left + 1 WHERE user_id = ?",
                (user_id,),
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
    finally:
        await db.close()


# ---------- Расклады ----------

async def add_reading(
    user_id: int, topic: str, question: str, drawn: list[dict], reading_text: str
) -> tuple[int, int]:
    """Сохраняет расклад. Возвращает (reading_id, порядковый номер расклада у пользователя)."""
    cards_json = json.dumps(
        [{"id": c["id"], "name": c["name"], "rev": c["rev"]} for c in drawn],
        ensure_ascii=False,
    )
    db = await _conn()
    try:
        cur = await db.execute(
            """INSERT INTO readings (user_id, topic, question, cards, reading_text, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, topic, question[:1000], cards_json, reading_text, now_iso()),
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
            """SELECT user_id, display_name, first_name, last_daily_date
               FROM users WHERE daily_opt_in = 1"""
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
        rate_up = await one("SELECT COUNT(*) FROM readings WHERE rating = 1")
        rate_down = await one("SELECT COUNT(*) FROM readings WHERE rating = -1")

        cur = await db.execute(
            """SELECT COALESCE(source, 'organic') AS src, COUNT(*) AS n
               FROM users GROUP BY src ORDER BY n DESC LIMIT 8"""
        )
        sources = [(r["src"], r["n"]) for r in await cur.fetchall()]

        return {
            "users": int(users), "users_24h": int(users_24h), "users_7d": int(users_7d),
            "readings": int(readings), "readings_24h": int(readings_24h),
            "pay_count": int(pay_count), "rub": float(rub), "stars": int(stars),
            "subs": int(subs), "daily": int(daily), "sources": sources,
            "rate_up": int(rate_up), "rate_down": int(rate_down),
        }
    finally:
        await db.close()
