"""Конфигурация бота «Карты Не Врут». Все настройки берутся из файла .env."""

import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


# --- Telegram ---
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME: str = os.getenv("BOT_USERNAME", "karty_ne_vrut_bot").strip().lstrip("@")
# Прокси для соединения с Telegram (нужен, если провайдер блокирует api.telegram.org
# напрямую). Формат: socks5://127.0.0.1:10808 или http://127.0.0.1:10809.
# Оставь пустым, если Telegram доступен без прокси.
BOT_PROXY: str = os.getenv("BOT_PROXY", "").strip()
ADMIN_IDS: set[int] = {
    int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()
}

# --- Нейросеть (OpenAI-совместимый API) ---
LLM_API_KEY: str = os.getenv("LLM_API_KEY", "").strip()
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.aitunnel.ru/v1/").strip()
LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini").strip()

# --- ЮKassa ---
YOOKASSA_SHOP_ID: str = os.getenv("YOOKASSA_SHOP_ID", "").strip()
YOOKASSA_SECRET_KEY: str = os.getenv("YOOKASSA_SECRET_KEY", "").strip()

# --- Цены ---
PRICE_SINGLE_RUB: int = _int("PRICE_SINGLE_RUB", 99)
PRICE_WEEK_RUB: int = _int("PRICE_WEEK_RUB", 249)
PRICE_MONTH_RUB: int = _int("PRICE_MONTH_RUB", 599)
STARS_SINGLE: int = _int("STARS_SINGLE", 70)
STARS_WEEK: int = _int("STARS_WEEK", 175)
STARS_MONTH: int = _int("STARS_MONTH", 420)

# Единый справочник тарифов. days: None = разовый расклад.
PLANS: dict[str, dict] = {
    "single": {"title": "1 расклад", "rub": PRICE_SINGLE_RUB, "stars": STARS_SINGLE, "days": None},
    "week": {"title": "Неделя без ограничений", "rub": PRICE_WEEK_RUB, "stars": STARS_WEEK, "days": 7},
    "month": {"title": "Месяц без ограничений", "rub": PRICE_MONTH_RUB, "stars": STARS_MONTH, "days": 30},
}

# --- Логика продукта ---
FREE_READINGS: int = _int("FREE_READINGS", 3)
DIALOGUE_MAX: int = _int("DIALOGUE_MAX", 5)
# +1 бесплатный расклад за каждые N дней серии карты дня (0 = выключить)
STREAK_REWARD_DAYS: int = _int("STREAK_REWARD_DAYS", 7)
FOLLOWUP_DAYS: int = _int("FOLLOWUP_DAYS", 3)
# Через сколько дней тишины напомнить о неиспользованных бесплатных раскладах (0 = выключить)
NUDGE_DAYS: int = _int("NUDGE_DAYS", 2)
DAILY_HOUR: int = _int("DAILY_HOUR", 9)
TIMEZONE: str = os.getenv("TIMEZONE", "Europe/Moscow").strip()

SUPPORT_CONTACT: str = os.getenv("SUPPORT_CONTACT", "@your_support").strip()
DB_PATH: str = os.getenv("DB_PATH", "tarot.db").strip()

# --- Mini App «вытяни карты сама» ---
# HTTPS-адрес страницы miniapp/index.html (GitHub Pages, VPS и т.п.).
# Пусто — бот тянет карты сам, как раньше (Mini App выключена).
MINIAPP_URL: str = os.getenv("MINIAPP_URL", "").strip()


def validate() -> None:
    """Проверка обязательных настроек при старте. Понятные ошибки по-русски."""
    problems = []
    if not BOT_TOKEN:
        problems.append("BOT_TOKEN пуст — возьми токен у @BotFather и впиши в .env")
    if not LLM_API_KEY:
        problems.append("LLM_API_KEY пуст — нужен ключ агрегатора (AITunnel/ProxyAPI/VseGPT)")
    if not ADMIN_IDS:
        problems.append("ADMIN_IDS пуст — впиши свой Telegram ID (узнать: @userinfobot)")
    if problems:
        raise RuntimeError("Не хватает настроек в .env:\n- " + "\n- ".join(problems))


def yookassa_available() -> bool:
    return bool(YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY)
