"""Сквозной прогон бандлов без Telegram и без нейросети.

Юнит-тесты проверяют расписание и промпты по отдельности, но самое опасное
здесь — сам путь: день 0 → вопрос про маркер → шаг на её ответе → шаг без
ответа → закрывающее письмо. Он выполняется только вживую, и именно в нём
ломаются такие вещи, как незакрытая транзакция или расклад, выданный дважды.

Подменяем ровно две вещи: bot (пишет в список вместо Telegram) и llm.chat
(отдаёт заготовленную разметку). Всё остальное — настоящее, включая SQLite.

Запуск: python3 test_bundles_e2e.py
"""

import asyncio
import os
import sys
import tempfile

os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("LLM_API_KEY", "test")
os.environ.setdefault("ADMIN_IDS", "1")
DB_FILE = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ["DB_PATH"] = DB_FILE

from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402

import bundle_run  # noqa: E402
import bundles  # noqa: E402
import config  # noqa: E402
import database as db  # noqa: E402
import handlers  # noqa: E402
import llm  # noqa: E402
import scheduler  # noqa: E402

# Серийная подача держит паузы «печатает…» — вживую это ритуал, в тесте
# это минуты ожидания. Гасим сон, всё остальное работает как в проде.
_real_sleep = asyncio.sleep


async def _no_sleep(_sec, *a, **kw):
    return await _real_sleep(0)


asyncio.sleep = _no_sleep

OK, FAIL = 0, 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name} {extra}")


# ---------- Заглушки ----------

class FakePhoto:
    file_id = "cached-file-id"


class FakeMsg:
    def __init__(self, text=None, photo=False):
        self.text = text
        self.photo = [FakePhoto()] if photo else None


class FakeBot:
    """Пишет всё в self.sent вместо Telegram."""

    id = 1

    def __init__(self):
        self.sent: list[str] = []
        self.photos: list[str] = []
        self.markups: list[object] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)
        self.markups.append(kw.get("reply_markup"))
        return FakeMsg(text)

    async def send_photo(self, chat_id, ref, **kw):
        self.photos.append(str(ref))
        return FakeMsg(photo=True)

    async def send_chat_action(self, *a, **kw):
        return True

    def dump(self) -> str:
        return "\n".join(self.sent)


def _serial(n_cards: int, marker: str | None = None) -> str:
    body = "\n".join(f"===КАРТА {i}===\nразбор карты {i}" for i in range(1, n_cards + 1))
    out = body + "\n===ИТОГ===\nобщий вывод\n===ПОДСКАЗКИ===\nда, это про меня\nи что теперь?"
    if marker:
        out += f"\n===МАРКЕР===\n{marker}"
    return out


class FakeLLM:
    """Отдаёт разметку под тот расклад, который просят, и запоминает промпты."""

    def __init__(self):
        self.calls: list[list[dict]] = []
        self.fail_next = False

    async def chat(self, messages, max_tokens=None, temperature=None):
        self.calls.append(messages)
        if self.fail_next:
            self.fail_next = False
            raise llm.LLMError("провайдер лёг")
        user = messages[-1]["content"]
        # Финальное письмо — единственный вызов без блока карт
        if "Выпавшие карты" not in user:
            return "Письмо о том, что это было. " * 20
        n = _count_cards(user)
        need_marker = "===МАРКЕР===" in messages[0]["content"][0]["text"]
        return _serial(n, "посмотри, напишет ли он первым до выходных"
                       if need_marker else None)


def _count_cards(user_block: str) -> int:
    """Сколько карт передали в промпт — считаем строки нумерованного списка."""
    tail = user_block.split("Выпавшие карты:")[1]
    n = 0
    for line in tail.splitlines():
        line = line.strip()
        if line[:2].rstrip(".").isdigit() and ". " in line:
            n += 1
    return n


async def _fresh() -> None:
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    await db.init()


async def _user(uid: int, name="Аня") -> None:
    await db.create_user(uid, "anya", name, None, None)
    await db.set_display_name(uid, name)


# ---------- Сценарии ----------

async def run_him() -> None:
    print("\n[«Он и я» целиком: день 0 → 3 → 7 → 14]")
    await _fresh()
    await _user(1)
    storage = MemoryStorage()
    bundle_run.bind(storage, handlers.Reading.in_dialogue)

    bot = FakeBot()
    fake = FakeLLM()
    llm.chat = fake.chat

    bid = await db.apply_purchase(1, "bundle_him")
    row = await db.get_bundle(bid)
    intro = {"Его зовут": "Артём", "Что между ними сейчас": "непонятно что",
             "Что она хочет понять": "нужна ли я ему"}

    ok = await bundle_run.deliver_day0(bot, 1, 1, row, intro, "Аня")
    check("день 0 прошёл", ok is True)
    dump = bot.dump()
    check("день 0 отдал все десять карт",
          sum(1 for t in bot.sent if "разбор карты" in t) == 10,
          f"({sum(1 for t in bot.sent if 'разбор карты' in t)})")
    check("итог пришёл", "общий вывод" in dump)
    check("маркер пришёл отдельным сообщением", "напишет ли он первым" in dump)
    check("маркер подан как наблюдение, а не задание",
          "Делать ничего не нужно" in dump)
    check("сказано, когда придёт следующий шаг", "через 3 дня" in dump)
    check("её слова уехали в промпт", "Артём" in fake.calls[0][1]["content"])

    b = await db.get_bundle(bid)
    check("бандл стал активным", b["status"] == "active")
    check("маркер сохранён в базе", "напишет ли он" in (b["marker"] or ""))
    check("расклад дня 0 записан на бандл",
          len(await db.bundle_readings(bid)) == 1)

    st = await storage.get_state(_key(bot, 1))
    check("после дня 0 человек в разговоре", st is not None)
    data = await storage.get_data(_key(bot, 1))
    check("разговор длинный, как и обещали",
          data.get("dlg_max") == config.DIALOGUE_MAX_BUNDLE)
    check("внутри бандла разговор помечен как бандловый",
          data.get("bundle_id") == bid)

    # --- шаг 3-го дня: сначала спрашиваем ---
    far = (db.now_utc().replace(microsecond=0)).strftime("%Y-%m-%d")
    from datetime import timedelta
    far = (db.now_utc() + timedelta(days=4)).strftime("%Y-%m-%d")
    due = await db.due_bundle_steps(far)
    check("третий день подошёл", len(due) >= 1)

    bot.sent.clear()
    await bundle_run.ask_step(bot, due[0])
    ask = bot.dump()
    check("бот спросил про маркер", "напишет ли он первым" in ask)
    check("вопрос не требует ответа", "Просто разложи" in str(bot.markups[-1]))
    check("к вопросу приложена картинка", bot.photos)
    check("состояние сброшено, чтобы ответ ушёл в бандл",
          await storage.get_state(_key(bot, 1)) is None)
    check("шаг ждёт ответа", (await db.awaiting_step(1)) is not None)

    # --- она отвечает текстом ---
    step = await db.awaiting_step(1)
    bot.sent.clear()
    fake.calls.clear()
    ok = await bundle_run.run_step(bot, 1, 1, step, "написал сам в среду")
    check("шаг выдан", ok is True)
    check("шаг — расклад на три карты",
          sum(1 for t in bot.sent if "разбор карты" in t) == 3)
    prompt = fake.calls[0][1]["content"]
    check("её ответ уехал в промпт", "написал сам в среду" in prompt)
    check("прошлый расклад уехал в промпт как контекст",
          "Что уже было в этом разборе" in prompt)
    check("прошлый маркер уехал в промпт", "Маркер наблюдения" in prompt)
    check("новый маркер сохранён",
          (await db.get_bundle(bid))["marker"] is not None)
    check("расклад шага привязан к бандлу",
          len(await db.bundle_readings(bid)) == 2)
    data = await storage.get_data(_key(bot, 1))
    check("после шага разговор короче, чем в день 0",
          data.get("dlg_max") == config.DIALOGUE_MAX_BUNDLE_STEP)
    check("повторно тот же шаг не выдаётся",
          await bundle_run.run_step(bot, 1, 1, step, None) is False)

    # --- шаг 7-го дня: она молчит, раскладываем без ответа ---
    far = (db.now_utc() + timedelta(days=8)).strftime("%Y-%m-%d")
    due = await db.due_bundle_steps(far)
    check("седьмой день подошёл", due and due[0]["step_no"] == 1)
    await bundle_run.ask_step(bot, due[0])
    tomorrow = (db.now_utc() + timedelta(days=9)).strftime("%Y-%m-%d")
    overdue = await db.overdue_bundle_steps(tomorrow)
    check("назавтра шаг считается просроченным", len(overdue) == 1)

    bot.sent.clear()
    fake.calls.clear()
    ok = await bundle_run.run_step(bot, 1, 1, overdue[0])
    check("молчание не остановило разбор", ok is True)
    prompt = fake.calls[0][1]["content"]
    check("модели сказано, что ответа не было",
          "Она ничего не рассказала" in prompt)

    # --- финал ---
    far = (db.now_utc() + timedelta(days=15)).strftime("%Y-%m-%d")
    due = await db.due_bundle_steps(far)
    check("четырнадцатый день подошёл", due and due[0]["step_no"] == 2)
    bot.sent.clear()
    fake.calls.clear()
    ok = await bundle_run.ask_step(bot, due[0])
    check("финалу вопрос не задаётся, письмо приходит сразу", ok is True)
    dump = bot.dump()
    check("письмо пришло", "Что это было" in dump)
    check("письмо закрывает разбор", "разбор закончен" in dump)
    check("карты в финале не тянулись",
          "Выпавшие карты" not in fake.calls[0][1]["content"])
    check("повторяющаяся карта уехала в письмо",
          "выпадала больше одного раза" in fake.calls[0][1]["content"])
    check("бандл доигран", (await db.get_bundle(bid))["status"] == "done")
    check("больше шагов нет",
          not await db.due_bundle_steps(
              (db.now_utc() + timedelta(days=90)).strftime("%Y-%m-%d")))


def _key(bot, uid):
    from aiogram.fsm.storage.base import StorageKey
    return StorageKey(bot_id=bot.id, chat_id=uid, user_id=uid)


async def run_month() -> None:
    print("\n[«Месяц вперёд»: день 0 и недельная сверка]")
    await _fresh()
    await _user(2, "Оля")
    bundle_run.bind(MemoryStorage(), handlers.Reading.in_dialogue)
    bot, fake = FakeBot(), FakeLLM()
    llm.chat = fake.chat

    bid = await db.apply_purchase(2, "bundle_month")
    row = await db.get_bundle(bid)
    ok = await bundle_run.deliver_day0(
        bot, 2, 2, row, {"Что сейчас занимает больше всего": "работа"}, "Оля")
    check("день 0 прошёл", ok is True)
    check("отдано шесть карт",
          sum(1 for t in bot.sent if "разбор карты" in t) == 6)
    check("маркера здесь нет — его роль играют карты недель",
          "За чем посмотреть" not in bot.dump())
    check("но сказано, когда придёт следующий шаг", "через неделю" in bot.dump())

    from datetime import timedelta
    far = (db.now_utc() + timedelta(days=8)).strftime("%Y-%m-%d")
    due = await db.due_bundle_steps(far)
    bot.sent.clear()
    fake.calls.clear()
    ok = await bundle_run.run_step(bot, 2, 2, due[0], "неделя вышла тяжёлая")
    check("недельная сверка прошла", ok is True)
    check("сверка — одна свежая карта",
          sum(1 for t in bot.sent if "разбор карты" in t) == 1)
    prompt = fake.calls[0][1]["content"]
    check("карта этой недели из дня 0 передана модели",
          "стояла на этой неделе ещё в первый день" in prompt)
    check("модели сказано активировать её, а не открывать заново",
          "активировать, а не открывать заново" in prompt)


async def run_scheduler() -> None:
    print("\n[планировщик: два прохода за утро]")
    await _fresh()
    await _user(3)
    bundle_run.bind(MemoryStorage(), handlers.Reading.in_dialogue)
    bot, fake = FakeBot(), FakeLLM()
    llm.chat = fake.chat

    bid = await db.apply_purchase(3, "bundle_him")
    row = await db.get_bundle(bid)
    await bundle_run.deliver_day0(bot, 3, 3, row, {"Его зовут": "Артём"}, "Аня")

    # Сдвигаем даты шагов в прошлое — как будто дни прошли
    import aiosqlite
    conn = await aiosqlite.connect(DB_FILE)
    await conn.execute("UPDATE bundle_steps SET due_date = '2020-01-01'")
    await conn.commit()
    await conn.close()

    bot.sent.clear()
    await scheduler.bundle_job(bot)
    check("за первое утро задан ровно один вопрос",
          sum(1 for t in bot.sent if "Три дня прошло" in t) == 1)
    asked = await db.awaiting_step(3)
    check("шаг ждёт ответа", asked is not None)

    # Второе утро: ответа не было
    conn = await aiosqlite.connect(DB_FILE)
    await conn.execute("UPDATE bundle_steps SET asked_at = '2020-01-02T09:00:00+00:00' "
                       "WHERE asked_at IS NOT NULL")
    await conn.commit()
    await conn.close()

    bot.sent.clear()
    await scheduler.bundle_job(bot)
    check("на второе утро расклад пришёл и без ответа",
          any("разбор карты" in t for t in bot.sent))
    check("и заодно задан вопрос следующего шага",
          any("неделя" in t.lower() for t in bot.sent))
    check("один и тот же шаг дважды не выдан",
          sum(1 for t in bot.sent if "Что он чувствует" in t) <= 1)


async def run_failure() -> None:
    print("\n[сбой провайдера не съедает шаг]")
    await _fresh()
    await _user(4)
    bundle_run.bind(MemoryStorage(), handlers.Reading.in_dialogue)
    bot, fake = FakeBot(), FakeLLM()
    llm.chat = fake.chat

    bid = await db.apply_purchase(4, "bundle_him")
    await bundle_run.deliver_day0(
        bot, 4, 4, await db.get_bundle(bid), {"Его зовут": "Артём"}, "Аня")

    from datetime import timedelta
    far = (db.now_utc() + timedelta(days=4)).strftime("%Y-%m-%d")
    step = (await db.due_bundle_steps(far))[0]

    fake.fail_next = True
    bot.sent.clear()
    ok = await bundle_run.run_step(bot, 4, 4, step, "рассказ")
    check("при сбое шаг не считается выданным", ok is False)
    check("человеку сказали честно", any("молчат" in t for t in bot.sent))
    back = [r["id"] for r in await db.due_bundle_steps(far)]
    check("шаг вернулся в очередь", step["id"] in back)

    bot.sent.clear()
    ok = await bundle_run.run_step(bot, 4, 4, step, "рассказ")
    check("со второй попытки всё вышло", ok is True)
    check("её ответ не потерялся",
          "рассказ" in (await db.bundle_answers(bid))[0])


async def run_day0_no_markup() -> None:
    print("\n[модель не разметила ответ — не теряем расклад]")
    await _fresh()
    await _user(5)
    bundle_run.bind(MemoryStorage(), handlers.Reading.in_dialogue)
    bot = FakeBot()

    async def broken(messages, max_tokens=None, temperature=None):
        return "Просто сплошной текст без всяких маркеров. " * 40

    llm.chat = broken
    bid = await db.apply_purchase(5, "bundle_him")
    ok = await bundle_run.deliver_day0(
        bot, 5, 5, await db.get_bundle(bid), {"Его зовут": "Артём"}, "Аня")
    check("день 0 всё равно доехал", ok is True)
    check("текст дошёл до человека", "сплошной текст" in bot.dump())
    check("бандл всё равно запустился",
          (await db.get_bundle(bid))["status"] == "active")
    import aiosqlite
    conn = await aiosqlite.connect(DB_FILE)
    n = (await (await conn.execute(
        "SELECT COUNT(*) FROM bundle_steps WHERE bundle_id = ?", (bid,))).fetchone())[0]
    await conn.close()
    check("расписание всё равно создано целиком",
          n == len(bundles.schedule("him")), f"({n})")
    check("но за раз приедет только первый шаг",
          len(await db.due_bundle_steps("2099-01-01")) == 1)


async def run_unstarted() -> None:
    print("\n[оплатила бандл и не начала]")
    await _fresh()
    await _user(6)
    bundle_run.bind(MemoryStorage(), handlers.Reading.in_dialogue)
    bot = FakeBot()

    bid = await db.apply_purchase(6, "bundle_him")
    check("сразу после оплаты не напоминаем",
          not await db.unstarted_bundles(hours=20))

    import aiosqlite
    conn = await aiosqlite.connect(DB_FILE)
    await conn.execute("UPDATE bundles SET created_at = '2020-01-01T00:00:00+00:00'")
    await conn.commit()
    await conn.close()

    await scheduler.bundle_job(bot)
    check("на следующий день зовём начать",
          any("так и не начали" in t for t in bot.sent))
    check("кнопка ведёт в начало разбора",
          f"bundle:start:{bid}" in str(bot.markups[-1]))

    bot.sent.clear()
    await scheduler.bundle_job(bot)
    check("второй раз не долбим", not bot.sent)
    check("бандл всё ещё ждёт её",
          (await db.get_bundle(bid))["status"] == "new")


async def main() -> None:
    await run_him()
    await run_month()
    await run_scheduler()
    await run_failure()
    await run_day0_no_markup()
    await run_unstarted()
    print(f"\nИтого: ok {OK}, fail {FAIL}")
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
