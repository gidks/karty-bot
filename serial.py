"""Разбор серийного вывода нейросети на блоки: карты/дни, итог, подсказки.

Модель размечает ответ маркерами вида ===КАРТА 1=== (см. prompts.py),
бот шлёт блоки отдельными сообщениями с паузами — «раскрытие по одной карте».
Любая ошибка формата -> parse() возвращает None, и бот отправляет разбор
одним сообщением, как раньше. Ничего не ломается.
"""

import re

# Маркер: строка вида ===ИМЯ=== или ===ИМЯ 3===. Терпим лишние пробелы,
# markdown-обёртки (**...**) и однобуквенные хвосты, которые модель может добавить.
_MARKER = re.compile(
    r"^[ \t]*[*_#>\-–—]*[ \t]*={2,}[ \t]*([А-ЯЁA-Z]+)[ \t]*(\d+)?[ \t]*={2,}[ \t]*[*_]*[ \t]*$",
    re.MULTILINE,
)

# Максимальная длина текста кнопки-подсказки
CHIP_MAX_LEN = 40


def parse(text: str, n_cards: int) -> dict | None:
    """Разбирает вывод модели.

    Возвращает {"intro": str|None, "cards": [str]*n_cards,
                "summary": str|None, "chips": [str]} или None при кривой разметке.
    """
    if not text or not text.strip():
        return None
    matches = list(_MARKER.finditer(text))
    if not matches:
        return None

    cards: dict[int, str] = {}
    intro: str | None = None
    summary: str | None = None
    chips_raw: str | None = None
    marker: str | None = None

    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        name = (m.group(1) or "").upper()
        num = int(m.group(2)) if m.group(2) else None

        if name in ("КАРТА", "ДЕНЬ", "CARD", "DAY") and num:
            cards[num] = body
        elif name in ("ОТВЕТ", "ANSWER"):
            cards[1] = body
        elif name in ("НЕДЕЛЯ", "ВСТУПЛЕНИЕ", "WEEK"):
            intro = body
        elif name in ("ИТОГ", "ВЫВОД", "SUMMARY"):
            summary = body
        elif name in ("ПОДСКАЗКИ", "ПОДСКАЗКА", "CHIPS"):
            chips_raw = body
        elif name in ("МАРКЕР", "MARKER"):
            # Маркер наблюдения в бандле: одна фраза, за чем смотреть до
            # следующего шага. Ставим его отдельным блоком, а не хвостом итога,
            # чтобы бот мог показать его особо и сохранить для следующего
            # расклада. Многострочный ответ модели сжимаем в одну строку.
            marker = " ".join(body.split()) or None
        # незнакомые блоки молча пропускаем

    # Все карты на месте и непустые?
    if set(cards.keys()) != set(range(1, n_cards + 1)):
        return None
    if any(not cards[i] for i in range(1, n_cards + 1)):
        return None

    # Текст до первого маркера (модель ослушалась): не теряем,
    # приклеиваем к вступлению или к первой карте.
    preamble = text[: matches[0].start()].strip()
    if preamble:
        if intro:
            intro = preamble + "\n\n" + intro
        else:
            cards[1] = preamble + "\n\n" + cards[1]

    return {
        "intro": intro or None,
        "cards": [cards[i] for i in range(1, n_cards + 1)],
        "summary": summary or None,
        "chips": _parse_chips(chips_raw),
        "marker": marker,
    }


def _parse_chips(raw: str | None) -> list[str]:
    """Строки блока подсказок -> список текстов кнопок (до 3)."""
    if not raw:
        return []
    chips: list[str] = []
    for line in raw.splitlines():
        s = line.strip().lstrip("-–—•*·0123456789.)").strip().strip("«»\"'").strip()
        if not s:
            continue
        if len(s) > CHIP_MAX_LEN:
            s = s[: CHIP_MAX_LEN - 1].rstrip() + "…"
        if s not in chips:
            chips.append(s)
        if len(chips) == 3:
            break
    return chips


def plain_text(parsed: dict, labels: list[str] | None = None) -> str:
    """Склеивает блоки обратно в цельный текст без маркеров и подсказок —
    для базы данных и контекста диалога."""
    parts: list[str] = []
    if parsed.get("intro"):
        parts.append(parsed["intro"])
    for i, body in enumerate(parsed.get("cards", [])):
        if labels and i < len(labels) and len(parsed["cards"]) > 1:
            parts.append(f"{labels[i]}: {body}")
        else:
            parts.append(body)
    if parsed.get("summary"):
        parts.append(parsed["summary"])
    if parsed.get("marker"):
        # Маркер идёт в сохранённый текст: следующий шаг бандла раскладывает
        # именно на том, сбылся он или нет, и должен видеть исходную формулировку
        parts.append(parsed["marker"])
    return "\n\n".join(parts)
