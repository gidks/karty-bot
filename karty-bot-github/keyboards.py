"""Инлайн-клавиатуры бота."""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

import bundles
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
            InlineKeyboardButton(text="🪞 Разбор месяца", callback_data="review"),
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


def spreads(is_sub: bool = False, bundle_keys: list[str] | None = None,
            active: set[str] | None = None) -> InlineKeyboardMarkup:
    """Меню раскладов. Премиум-расклад показываем всем — но без подписки
    он с замком: витрина важнее, чем спрятанная кнопка.

    Бандлы стоят здесь же, а не в «Тарифах»: «хочу разобраться с ним» —
    это намерение выбрать расклад, а не совершить покупку. Она выбирает
    продукт, оплата идёт следствием."""
    celtic = ("🕯 Кельтский крест — 10 карт" if is_sub
              else "🕯 Кельтский крест — 10 карт 🔒")
    rows = [
        [InlineKeyboardButton(text="🔮 Три карты — классика", callback_data="spread:classic")],
        [InlineKeyboardButton(text="⚡ Да или нет", callback_data="spread:yesno")],
        [InlineKeyboardButton(text="🔀 Выбор из двух", callback_data="spread:choice")],
        [InlineKeyboardButton(text="💔 Что он чувствует", callback_data="spread:feelings")],
        [InlineKeyboardButton(text="🗓 Неделя вперёд", callback_data="spread:week")],
        [InlineKeyboardButton(text=celtic, callback_data="spread:celtic")],
    ]
    active = active or set()
    for key in (bundle_keys or []):
        b = bundles.get(key)
        if not b:
            continue
        if key in active:
            label = f"{b['emoji']} {b['title']} — идёт"
        else:
            label = f"{b['emoji']} {b['title']} — {config.PRICE_BUNDLE_RUB} ₽"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"bundle:show:{key}")])
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")])
    return _kb(rows)


def sub_plans(back: str = "new_reading") -> InlineKeyboardMarkup:
    """Кнопки под «закрытым» экраном фичи из подписки. Недельного тарифа
    больше нет — при месяце в 299 ₽ он не отличим от пакета."""
    p = config.PLANS
    return _kb([
        [InlineKeyboardButton(
            text=f"🖤 Со мной без счёта — {p['month']['rub']} ₽", callback_data="buy:month")],
        [
            InlineKeyboardButton(text="💳 Все тарифы", callback_data="tariffs"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data=back),
        ],
    ])


def pack_offer(back: str = "menu") -> InlineKeyboardMarkup:
    """Пейволл и «кончился остаток»: продаём пакет, а не подписку."""
    p = config.PLANS
    return _kb([
        [InlineKeyboardButton(
            text=f"🔮 {config.PACK_READINGS} раскладов — {p['pack5']['rub']} ₽",
            callback_data="buy:pack5")],
        [InlineKeyboardButton(
            text=f"✨ 1 расклад — {p['single']['rub']} ₽", callback_data="buy:single")],
        [
            InlineKeyboardButton(text="💳 Все тарифы", callback_data="tariffs"),
            InlineKeyboardButton(text="⬅️ В меню", callback_data=back),
        ],
    ])


def continue_offer() -> InlineKeyboardMarkup:
    """Реплики кончились посреди разговора — единственная кнопка, которая
    здесь нужна: продолжить именно этот разговор."""
    return _kb([
        [InlineKeyboardButton(
            text=f"💬 Продолжить — {config.PRICE_SINGLE_RUB} ₽",
            callback_data="buy:single")],
        [
            InlineKeyboardButton(text="🔮 Новый расклад", callback_data="new_reading"),
            InlineKeyboardButton(text="🏠 В меню", callback_data="menu"),
        ],
    ])


def bundle_offer(key: str, back: str = "new_reading") -> InlineKeyboardMarkup:
    """Экран объяснения бандла: одна кнопка покупки, без списка альтернатив."""
    b = bundles.get(key)
    plan_key = b["plan"] if b else "bundle_him"
    title = b["title"] if b else "разбор"
    return _kb([
        [InlineKeyboardButton(
            text=f"Взять «{title}» — {config.PRICE_BUNDLE_RUB} ₽",
            callback_data=f"buy:{plan_key}")],
        [
            InlineKeyboardButton(text="💳 Все тарифы", callback_data="tariffs"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data=back),
        ],
    ])


def bundle_start(bundle_id: int) -> InlineKeyboardMarkup:
    return _kb([
        [InlineKeyboardButton(text="Начнём 🖤", callback_data=f"bundle:start:{bundle_id}")],
    ])


def bundle_step(step_id: int) -> InlineKeyboardMarkup:
    """Шаг бандла: рассказать или сразу раскладывать. Расклад не должен
    зависеть от её ответа — иначе это форма, а не разговор."""
    return _kb([
        [InlineKeyboardButton(text="🔮 Просто разложи",
                              callback_data=f"bundle:go:{step_id}")],
    ])


def bundle_done(key: str) -> InlineKeyboardMarkup:
    """После финального письма. Для «Месяца вперёд» — тот же бандл ещё раз:
    единственный, у которого есть естественный повтор."""
    rows = []
    b = bundles.get(key)
    if b and config.BUNDLES_ENABLED:
        if key == "month":
            rows.append([InlineKeyboardButton(
                text=f"🌙 Ещё месяц — {config.PRICE_BUNDLE_RUB} ₽",
                callback_data="bundle:show:month")])
        else:
            rows.append([InlineKeyboardButton(
                text="🌙 Посмотреть «Месяц вперёд»",
                callback_data="bundle:show:month")])
    rows.append([
        InlineKeyboardButton(text="🔮 Разложить карты", callback_data="new_reading"),
        InlineKeyboardButton(text="🏠 В меню", callback_data="menu"),
    ])
    return _kb(rows)


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
    """Полная лестница — только на экране «💳 Тарифы», куда человек пришёл сам.
    Во всех остальных точках показываем одно предложение под момент."""
    p = config.PLANS
    rows = [
        [InlineKeyboardButton(
            text=f"✨ 1 расклад — {p['single']['rub']} ₽", callback_data="buy:single")],
        [InlineKeyboardButton(
            text=f"🔮 {config.PACK_READINGS} раскладов — {p['pack5']['rub']} ₽",
            callback_data="buy:pack5")],
    ]
    if config.BUNDLES_ENABLED:
        rows += [
            [InlineKeyboardButton(
                text=f"💞 «Он и я» — {p['bundle_him']['rub']} ₽",
                callback_data="bundle:show:him")],
            [InlineKeyboardButton(
                text=f"🌙 «Месяц вперёд» — {p['bundle_month']['rub']} ₽",
                callback_data="bundle:show:month")],
        ]
    rows += [
        [InlineKeyboardButton(
            text=f"🖤 Со мной без счёта — {p['month']['rub']} ₽", callback_data="buy:month")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")],
    ]
    return _kb(rows)


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


def daily_morning() -> InlineKeyboardMarkup:
    """Клавиатура утренней рассылки: приглашение разложить + отписка в один тап.
    Карта дня теперь включена по умолчанию, поэтому выход должен быть на виду."""
    return _kb([
        [InlineKeyboardButton(text="🔮 Разложить карты", callback_data="new_reading")],
        [InlineKeyboardButton(text="🔕 Отключить утренние", callback_data="daily_sub:off")],
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
