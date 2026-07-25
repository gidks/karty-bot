"""Инлайн-клавиатуры бота."""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

import config


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def name_default(first_name: str) -> InlineKeyboardMarkup:
    label = f"Зови меня {first_name}" if first_name else "Без имени"
    return _kb([[InlineKeyboardButton(text=label, callback_data="name_default")]])


def main_menu() -> InlineKeyboardMarkup:
    return _kb([
        [InlineKeyboardButton(text="🔮 Разложить карты", callback_data="new_reading")],
        [
            InlineKeyboardButton(text="🃏 Карта дня", callback_data="daily_card"),
            InlineKeyboardButton(text="🗂 Коллекция", callback_data="collection"),
        ],
        [
            InlineKeyboardButton(text="📖 Моя история", callback_data="my_readings"),
            InlineKeyboardButton(text="🌙 Разбор месяца", callback_data="review"),
        ],
        [
            InlineKeyboardButton(text="🎁 Подарить подруге", callback_data="share"),
            InlineKeyboardButton(text="💳 Тарифы", callback_data="tariffs"),
        ],
        [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")],
    ])


def draw_cards_webapp(spread: str) -> ReplyKeyboardMarkup:
    """Кнопка, открывающая Mini App с колодой (ритуал «вытяни сама»)."""
    url = config.MINIAPP_URL
    sep = "&" if "?" in url else "?"
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(
            text="🃏 Вытянуть карты",
            web_app=WebAppInfo(url=f"{url}{sep}spread={spread}"),
        )]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Жми «Вытянуть карты» 👆",
    )


def chips_reply(chips: list[str]) -> ReplyKeyboardMarkup:
    """Подсказки-реплики после расклада: тап отправляет текст как сообщение
    пользовательницы — и разговор продолжается сам."""
    rows = [[KeyboardButton(text=c)] for c in chips[:3]]
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Ответь — или напиши своё…",
    )


def spreads(is_sub: bool = False) -> InlineKeyboardMarkup:
    """Меню раскладов. Премиум-расклад показываем всем — но без подписки
    он с замком: витрина важнее, чем спрятанная кнопка."""
    celtic = ("🕯 Кельтский крест — 10 карт" if is_sub
              else "🕯 Кельтский крест — 10 карт 🔒")
    return _kb([
        [InlineKeyboardButton(text="🔮 Три карты — классика", callback_data="spread:classic")],
        [InlineKeyboardButton(text="⚡ Да или нет", callback_data="spread:yesno")],
        [InlineKeyboardButton(text="🔀 Выбор из двух", callback_data="spread:choice")],
        [InlineKeyboardButton(text="💔 Что он чувствует", callback_data="spread:feelings")],
        [InlineKeyboardButton(text="🗓 Неделя вперёд", callback_data="spread:week")],
        [InlineKeyboardButton(text=celtic, callback_data="spread:celtic")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")],
    ])


def sub_plans(back: str = "new_reading") -> InlineKeyboardMarkup:
    """Кнопки под «закрытым» экраном премиум-фичи: только подписки."""
    p = config.PLANS
    return _kb([
        [InlineKeyboardButton(
            text=f"🗓 Неделя — {p['week']['rub']} ₽", callback_data="buy:week")],
        [InlineKeyboardButton(
            text=f"🌙 Месяц — {p['month']['rub']} ₽", callback_data="buy:month")],
        [
            InlineKeyboardButton(text="💳 Все тарифы", callback_data="tariffs"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data=back),
        ],
    ])


def topics() -> InlineKeyboardMarkup:
    return _kb([
        [InlineKeyboardButton(text="💔 Отношения", callback_data="topic:relations")],
        [InlineKeyboardButton(text="💼 Работа и деньги", callback_data="topic:work")],
        [InlineKeyboardButton(text="🌱 Про себя", callback_data="topic:self")],
        [InlineKeyboardButton(text="✨ Свой вопрос", callback_data="topic:own")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")],
    ])


def after_reading(reading_id: int | None = None) -> InlineKeyboardMarkup:
    """Кнопки под раскладом. С reading_id — плюс строка оценки 👍/👎."""
    rows = []
    if reading_id is not None:
        rows.append([
            InlineKeyboardButton(text="👍 Попало", callback_data=f"rate:{reading_id}:up"),
            InlineKeyboardButton(text="👎 Мимо", callback_data=f"rate:{reading_id}:down"),
        ])
    rows.append([
        InlineKeyboardButton(text="🔮 Новый расклад", callback_data="new_reading"),
        InlineKeyboardButton(text="🏠 В меню", callback_data="menu"),
    ])
    return _kb(rows)


def to_reading() -> InlineKeyboardMarkup:
    return _kb([
        [InlineKeyboardButton(text="🔮 Разложить карты", callback_data="new_reading")],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu")],
    ])


def plans() -> InlineKeyboardMarkup:
    p = config.PLANS
    return _kb([
        [InlineKeyboardButton(
            text=f"🔮 1 расклад — {p['single']['rub']} ₽", callback_data="buy:single")],
        [InlineKeyboardButton(
            text=f"🗓 Неделя — {p['week']['rub']} ₽", callback_data="buy:week")],
        [InlineKeyboardButton(
            text=f"🌙 Месяц — {p['month']['rub']} ₽", callback_data="buy:month")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")],
    ])


def pay_methods(plan_key: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        text="⭐ Звёздами Telegram", callback_data=f"pay:stars:{plan_key}")]]
    if config.yookassa_available():
        rows.append([InlineKeyboardButton(
            text="💳 Картой (ЮKassa)", callback_data=f"pay:card:{plan_key}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к тарифам", callback_data="tariffs")])
    return _kb(rows)


def pay_link(url: str, price_rub: int, payment_id: str) -> InlineKeyboardMarkup:
    return _kb([
        [InlineKeyboardButton(text=f"💳 Оплатить {price_rub} ₽", url=url)],
        [InlineKeyboardButton(text="✅ Я оплатила", callback_data=f"check_pay:{payment_id}")],
        [InlineKeyboardButton(text="⬅️ Назад к тарифам", callback_data="tariffs")],
    ])


def daily_toggle(opted_in: bool) -> InlineKeyboardMarkup:
    if opted_in:
        first = InlineKeyboardButton(text="🔕 Отключить утренние", callback_data="daily_sub:off")
    else:
        first = InlineKeyboardButton(text="🌅 Присылать каждое утро", callback_data="daily_sub:on")
    return _kb([
        [first],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu")],
    ])


def collection_menu() -> InlineKeyboardMarkup:
    return _kb([
        [InlineKeyboardButton(text="🃏 Карта дня", callback_data="daily_card")],
        [
            InlineKeyboardButton(text="🔮 Разложить карты", callback_data="new_reading"),
            InlineKeyboardButton(text="🏠 В меню", callback_data="menu"),
        ],
    ])


def to_menu() -> InlineKeyboardMarkup:
    return _kb([[InlineKeyboardButton(text="🏠 В меню", callback_data="menu")]])


def broadcast_confirm() -> InlineKeyboardMarkup:
    return _kb([
        [
            InlineKeyboardButton(text="✅ Отправить всем", callback_data="bc_confirm"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="bc_cancel"),
        ],
    ])
