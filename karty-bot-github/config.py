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
# Лестница из четырёх ступеней: попробовать → набрать остаток →
# разобрать свою историю до конца → жить в боте.
#
# Почему так, а не «подписка + разовый»:
#   · остаток купленных раскладов сам по себе повод вернуться, а исчерпанный
#     бесплатный лимит — повод уйти. Возврат у нас узкое место, не цена;
#   · у пакета расход растёт ровно вместе с продажей — исчезает хвост
#     безлимитного диалога, из-за которого месяц нельзя было опускать;
#   · пакет можно продавать в момент подтверждённой ценности (кнопка «Попало»),
#     а подписку в этот момент просить рано.
#
# Недельный тариф убран: при месяце в 299 ₽ он не отличим от пакета и путает
# витрину. Старые платежи с plan='week' всё равно обрабатываются — см. LEGACY_PLANS.
PRICE_SINGLE_RUB: int = _int("PRICE_SINGLE_RUB", 49)
PRICE_PACK_RUB: int = _int("PRICE_PACK_RUB", 149)
PRICE_BUNDLE_RUB: int = _int("PRICE_BUNDLE_RUB", 249)
PRICE_MONTH_RUB: int = _int("PRICE_MONTH_RUB", 299)

# ⚠️ Звёзды пересчитаны в прежней пропорции (≈1,41 ₽ за звезду) — то есть
# паритет со звёздами по-прежнему НЕ решён, см. «Открытые вопросы» в
# МОНЕТИЗАЦИЯ.md. Со звёзд, купленных с телефона, доходит ~2/3 (30% Apple/Google
# + комиссия Fragment), поэтому звёздный прайс должен быть выше рублёвого на
# 30–40%. Поднимать только после того, как цену 70 ⭐ посмотрят глазами со
# своего телефона: Telegram показывает её в локальной валюте.
STARS_SINGLE: int = _int("STARS_SINGLE", 35)
STARS_PACK: int = _int("STARS_PACK", 105)
STARS_BUNDLE: int = _int("STARS_BUNDLE", 175)
STARS_MONTH: int = _int("STARS_MONTH", 210)

# Сколько раскладов даёт пакет
PACK_READINGS: int = _int("PACK_READINGS", 5)

# Единый справочник тарифов.
#   kind: 'single' | 'pack' | 'bundle' | 'sub' — по нему apply_purchase решает,
#         что начислить. days нужен только подпискам.
# Названия видит человек в момент оплаты (счёт Telegram, ссылка ЮKassa) —
# поэтому здесь не должно быть обещаний, которых мы не выполняем.
PLANS: dict[str, dict] = {
    "single": {
        "title": "1 расклад", "rub": PRICE_SINGLE_RUB, "stars": STARS_SINGLE,
        "days": None, "kind": "single",
    },
    "pack5": {
        "title": f"{PACK_READINGS} раскладов", "rub": PRICE_PACK_RUB,
        "stars": STARS_PACK, "days": None, "kind": "pack",
        "readings": PACK_READINGS,
    },
    "bundle_him": {
        "title": "«Он и я» — разбор на две недели", "rub": PRICE_BUNDLE_RUB,
        "stars": STARS_BUNDLE, "days": None, "kind": "bundle", "bundle": "him",
    },
    "bundle_month": {
        "title": "«Месяц вперёд» — разбор на месяц", "rub": PRICE_BUNDLE_RUB,
        "stars": STARS_BUNDLE, "days": None, "kind": "bundle", "bundle": "month",
    },
    "month": {
        "title": "Подписка на месяц", "rub": PRICE_MONTH_RUB,
        "stars": STARS_MONTH, "days": 30, "kind": "sub",
    },
}

# Тарифы, которые сняты с продажи, но могли остаться в незакрытых платежах.
# Человек мог получить ссылку на оплату до обновления и нажать «Я оплатила»
# уже после — деньги списаны, и мы обязаны их отработать по старым условиям.
LEGACY_PLANS: dict[str, dict] = {
    "week": {"title": "Подписка на неделю", "rub": 249, "stars": 175,
             "days": 7, "kind": "sub"},
}


def plan(plan_key: str) -> dict | None:
    """Тариф по ключу, включая снятые с продажи."""
    return PLANS.get(plan_key) or LEGACY_PLANS.get(plan_key)


# Порядок ступеней на витрине «💳 Тарифы» — снизу вверх по чеку
LADDER: tuple[str, ...] = ("single", "pack5", "bundle_him", "bundle_month", "month")

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
# Реплика стоит ≈1 ₽ — это единственная статья, способная съесть выручку.
# Поэтому лимит привязан не к человеку, а к тому, ЧЕМ оплачен расклад:
# бесплатный — затравка, платный — заметно длиннее, бандл — длинный разговор
# и есть сам продукт.
DIALOGUE_MAX: int = _int("DIALOGUE_MAX", 5)                    # бесплатный расклад
DIALOGUE_MAX_PAID: int = _int("DIALOGUE_MAX_PAID", 10)         # разовый и пакет
DIALOGUE_MAX_BUNDLE: int = _int("DIALOGUE_MAX_BUNDLE", 25)     # бандл, день 0
DIALOGUE_MAX_BUNDLE_STEP: int = _int("DIALOGUE_MAX_BUNDLE_STEP", 10)  # шаги бандла

# С подпиской — потолок в сутки и бюджет на месяц. Смысл не в экономии на
# клиенте, а в том, чтобы у нас не было статьи расходов без потолка.
#
# ⚠️ Цифры уменьшены вместе с ценой месяца (599 → 299 ₽). Раньше потолок
# расходов в 300 реплик ещё оставлял маржу; при 299 ₽ он съедает выручку
# целиком. 120 реплик (≈120 ₽) плюс ~60 ₽ раскладов держат ту же структуру
# маржи, что была. На практике в эти лимиты не упирался никто — это защита
# от хвоста, а не нормальный опыт. Двигать по /stats. 0 = выключить.
DIALOGUE_MAX_SUB: int = _int("DIALOGUE_MAX_SUB", 15)           # с подпиской — в сутки
DIALOGUE_MAX_SUB_MONTH: int = _int("DIALOGUE_MAX_SUB_MONTH", 120)

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
# followup_job крутится каждый час — держим отправку в дневном окне по Москве,
# чтобы «как всё сложилось?» не приходило в четыре утра.
FOLLOWUP_HOUR_FROM: int = _int("FOLLOWUP_HOUR_FROM", 10)
FOLLOWUP_HOUR_TO: int = _int("FOLLOWUP_HOUR_TO", 21)
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


# --- Атмосферные картинки для напоминалок ---
# Банк одинаковых по стилю квадратов (mood_01.jpg … mood_NN.jpg) в репозитории
# миниаппа. Прикладываются к followup, nudge и понедельничному письму — у карты
# дня своя картинка, туда не лезем. Пусто или MOOD_COUNT=0 — шлём без картинки.
# Картинки залиты в корень репозитория миниаппа, а не в подпапку — поэтому
# база без /mood. Переложишь в папку — поменяй здесь или в .env, код не трогать.
MOOD_IMG_BASE: str = os.getenv(
    "MOOD_IMG_BASE", "https://gidks.github.io/karty-miniapp/").strip()
MOOD_COUNT: int = _int("MOOD_COUNT", 12)


def mood_img_url(index: int) -> str:
    """URL картинки напоминалки по индексу 0..MOOD_COUNT-1 или '' если выключены."""
    if not MOOD_IMG_BASE or MOOD_COUNT <= 0:
        return ""
    return MOOD_IMG_BASE.rstrip("/") + f"/mood_{index % MOOD_COUNT + 1:02d}.jpg"


# --- Картинки экранов продажи ---
# Горизонтальные (3:2) картинки под ступени лестницы: offer_pack.jpg,
# offer_him.jpg, offer_month.jpg, offer_sub.jpg в репозитории миниаппа.
# Товар за 249 ₽ невидим — картинка делает обещание вещью, а сообщение с фото
# занимает пол-экрана и останавливает пролистывание. Цену на картинку НЕ
# наносим: цены мы меняем, а перерисовывать нельзя.
# Пусто — экраны продажи уходят текстом, как раньше.
OFFER_IMG_BASE: str = os.getenv(
    "OFFER_IMG_BASE", "https://gidks.github.io/karty-miniapp/").strip()


def offer_img_url(name: str) -> str:
    """URL картинки экрана продажи ('pack', 'him', 'month', 'sub') или ''."""
    if not OFFER_IMG_BASE or not name:
        return ""
    return OFFER_IMG_BASE.rstrip("/") + f"/offer_{name}.jpg"


# --- Бандлы: тематические разборы с расписанием ---
# Бандл — не «N раскладов со скидкой», а одна ситуация, доведённая до конца:
# расклады внутри связаны, знают друг друга и приходят по расписанию.
# Главное, ради чего он существует: бот получает законный повод написать
# первым на 3-й и 7-й день — то есть бандл встроенно чинит возврат.
#
# 1 = продавать, 0 = скрыть с витрины (уже купленные доигрываются).
BUNDLES_ENABLED: int = _int("BUNDLES_ENABLED", 1)

# Дни шагов, через запятую, от дня покупки. Числа подобранные, не священные:
# три дня — достаточно, чтобы что-то успело произойти, и мало, чтобы она
# остыла. Правим по факту, код не трогаем.
BUNDLE_HIM_DAYS: str = os.getenv("BUNDLE_HIM_DAYS", "3,7,14").strip()
BUNDLE_MONTH_DAYS: str = os.getenv("BUNDLE_MONTH_DAYS", "7,14,21,28,30").strip()

# Сколько бандлов одного вида можно вести одновременно (1 = только один)
BUNDLE_MAX_ACTIVE: int = _int("BUNDLE_MAX_ACTIVE", 1)


def bundle_days(raw: str, fallback: list[int]) -> list[int]:
    """«3,7,14» -> [3, 7, 14]. Кривая строка -> значения по умолчанию."""
    try:
        days = [int(x) for x in raw.replace(" ", "").split(",") if x]
    except ValueError:
        return list(fallback)
    days = sorted({d for d in days if d > 0})
    return days or list(fallback)


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
