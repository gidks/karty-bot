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
PRICE_SINGLE_RUB: int = _int("PRICE_SINGLE_RUB", 79)
PRICE_WEEK_RUB: int = _int("PRICE_WEEK_RUB", 249)
PRICE_MONTH_RUB: int = _int("PRICE_MONTH_RUB", 599)
STARS_SINGLE: int = _int("STARS_SINGLE", 56)
STARS_WEEK: int = _int("STARS_WEEK", 175)
STARS_MONTH: int = _int("STARS_MONTH", 420)

# Единый справочник тарифов. days: None = разовый расклад.
# Названия видит человек в момент оплаты (счёт Telegram, ссылка ЮKassa) —
# поэтому здесь не должно быть обещаний, которых мы не выполняем.
PLANS: dict[str, dict] = {
    "single": {"title": "1 расклад", "rub": PRICE_SINGLE_RUB, "stars": STARS_SINGLE, "days": None},
    "week": {"title": "Подписка на неделю", "rub": PRICE_WEEK_RUB, "stars": STARS_WEEK, "days": 7},
    "month": {"title": "Подписка на месяц", "rub": PRICE_MONTH_RUB, "stars": STARS_MONTH, "days": 30},
}

# --- Логика продукта ---
# Сколько бесплатных раскладов даём на входе (одноразовый стартовый запас).
FREE_READINGS: int = _int("FREE_READINGS", 3)

# --- Обновляемый бесплатный лимит ---
# Раз в неделю добавляем FREE_WEEKLY_TOPUP бесплатный расклад тем, у кого их
# меньше, чем FREE_TOPUP_CAP. Уже накопленное (рефералка, серия) не отбираем:
# начисление идёт только тем, кто ниже потолка. 0 = выключить пополнение.
FREE_WEEKLY_TOPUP: int = _int("FREE_WEEKLY_TOPUP", 1)
FREE_TOPUP_CAP: int = _int("FREE_TOPUP_CAP", 2)
TOPUP_WEEKDAY: int = _int("TOPUP_WEEKDAY", 0)   # 0 = понедельник
TOPUP_HOUR: int = _int("TOPUP_HOUR", 11)
# Кому сообщать о начислении: тем, у кого расклады кончились и кто заходила
# не позже чем N дней назад (0 = никому не писать, начислять молча).
TOPUP_NOTIFY_DAYS: int = _int("TOPUP_NOTIFY_DAYS", 30)

# --- Лимиты разговора после расклада ---
DIALOGUE_MAX: int = _int("DIALOGUE_MAX", 5)           # без подписки — на расклад
# С подпиской — потолок в сутки. Смысл не в экономии на клиенте, а в том, чтобы
# у нас не было статьи расходов без потолка: реплика стоит ~1 ₽, и без лимита
# один человек, который говорит целыми днями, съедает всю выручку месяца.
# 20 в сутки — это в 4 раза больше бесплатного и заведомо больше, чем нужно
# в живом разговоре. Двигать только по /stats, когда будут живые цифры.
DIALOGUE_MAX_SUB: int = _int("DIALOGUE_MAX_SUB", 20)  # с подпиской — в сутки
# И месячный бюджет реплик. Суточный потолок нужен, чтобы вечер разговора был
# длинным (20 — это много), а месячный — чтобы 30 таких вечеров подряд не съели
# всю выручку. 300 в месяц = в среднем 10 в день; живой человек столько не
# наговорит, а патологический случай теперь конечен. 0 = выключить.
DIALOGUE_MAX_SUB_MONTH: int = _int("DIALOGUE_MAX_SUB_MONTH", 300)

# --- Что входит в подписку ---
# Расклады только для подписчиц (ключи из texts.SPREAD_TITLES).
PREMIUM_SPREADS: set[str] = {"celtic"}
# Личная карта дня подписчицам (1 = включено). LIMIT — предохранитель:
# больше этого числа персональных генераций за одно утро не делаем.
DAILY_PERSONAL: int = _int("DAILY_PERSONAL", 1)
DAILY_PERSONAL_LIMIT: int = _int("DAILY_PERSONAL_LIMIT", 300)
# «Разбор месяца»: сколько раскладов нужно, пауза между разборами,
# сколько последних раскладов берём в разбор.
REVIEW_MIN_READINGS: int = _int("REVIEW_MIN_READINGS", 3)
REVIEW_COOLDOWN_DAYS: int = _int("REVIEW_COOLDOWN_DAYS", 25)
REVIEW_MAX_READINGS: int = _int("REVIEW_MAX_READINGS", 12)

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

# --- Картинки карт для чата (карта дня и т.п.) ---
# Публичный базовый URL, где лежат картинки карт для отправки в чат:
# <base>/<id>.jpg (напр. cards_chat/0.jpg … 77.jpg в репозитории миниаппа).
# Пусто — карта дня шлётся без картинки, как раньше.
CARD_IMG_BASE: str = os.getenv(
    "CARD_IMG_BASE", "https://gidks.github.io/karty-miniapp/cards_chat/").strip()


def card_img_url(card_id: int) -> str:
    """URL картинки карты для чата или '' если картинки отключены."""
    if not CARD_IMG_BASE:
        return ""
    return CARD_IMG_BASE.rstrip("/") + f"/{card_id}.jpg"


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
