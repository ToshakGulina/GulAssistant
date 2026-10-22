"""Разбор русского времени и длительностей: «через полчаса», «в 7:30 утра»."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from ..utils import normalize_text

_WORD_NUMBERS = {
    "ноль": 0, "один": 1, "одну": 1, "одна": 1, "два": 2, "две": 2, "три": 3,
    "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
    "десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13,
    "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
    "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20, "тридцать": 30,
    "сорок": 40, "пятьдесят": 50, "девяносто": 90,
    "полтора": 1.5, "полторы": 1.5, "половина": 0.5,
}

_UNIT_SECONDS = {
    "секунда": 1, "секунду": 1, "секунды": 1, "секунд": 1, "сек": 1, "с": 1,
    "минута": 60, "минуту": 60, "минуты": 60, "минут": 60, "мин": 60, "м": 60,
    "час": 3600, "часа": 3600, "часов": 3600, "ч": 3600,
    "день": 86400, "дня": 86400, "дней": 86400, "сутки": 86400,
}

_UNIT_WORD_MAP = {
    "секунд": "секунд", "секунды": "секунд", "секунду": "секунд", "сек": "секунд",
    "минут": "минут", "минуты": "минут", "минуту": "минут", "мин": "минут",
    "час": "часов", "часа": "часов", "часов": "часов", "ч": "часов",
}


def _num(token: str) -> float | None:
    token = token.strip().replace(",", ".")
    if re.fullmatch(r"\d+(?:\.\d+)?", token):
        return float(token)
    return _WORD_NUMBERS.get(token)


def parse_duration_seconds(text: str) -> int | None:
    """«5 минут», «полтора часа», «1 час 20 минут», «полчаса» → секунды."""
    return extract_duration(text)[0]


_SPECIAL_DURATIONS = {
    "полчаса": 1800, "пол часа": 1800, "полминуты": 30, "пол минуты": 30,
    "полторы минуты": 90, "сутки": 86400, "суток": 86400,
}

_ITEM_RE = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)?|полтора|полторы|половина|"
    + "|".join(sorted(_WORD_NUMBERS, key=len, reverse=True))
    + r")\s*(?P<unit>секунд\w*|сек|минут\w*|мин|час\w*|ч|день|дня|дней|сутки|суток)\b"
)


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < b and a < span[1] for a, b in spans)


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + " " + text[end:]
    return re.sub(r"\s+", " ", text).strip(" .,-—")


def extract_duration(text: str) -> tuple[int | None, str]:
    """Возвращает (секунды, остаток текста без длительности)."""
    t = normalize_text(text)
    if not t:
        return None, ""
    total = 0
    spans: list[tuple[int, int]] = []
    for phrase, seconds in _SPECIAL_DURATIONS.items():
        for m in re.finditer(rf"\b{re.escape(phrase)}\b", t):
            if not _overlaps(m.span(), spans):
                total += seconds
                spans.append(m.span())
    for m in _ITEM_RE.finditer(t):
        if _overlaps(m.span(), spans):
            continue
        number = _num(m.group("num"))
        if number is None:
            continue
        unit = m.group("unit")
        multiplier = None
        for key, value in _UNIT_SECONDS.items():
            if unit.startswith(key) or key.startswith(unit):
                multiplier = value
                break
        if multiplier is None:
            continue
        total += number * multiplier
        spans.append(m.span())
    return (int(total) if total else None), _remove_spans(t, spans)


def _parse_parts(t: str) -> int | None:
    pattern = re.compile(
        r"(?P<num>\d+(?:[.,]\d+)?|полтора|полторы|половина|"
        + "|".join(sorted(_WORD_NUMBERS, key=len, reverse=True))
        + r")\s*(?P<unit>секунд\w*|сек|минут\w*|мин|час\w*|ч|день|дня|дней|сутки)\b"
    )
    total = 0
    found = False
    for m in pattern.finditer(t):
        number = _num(m.group("num"))
        unit = m.group("unit")
        if number is None:
            continue
        multiplier = None
        for key, value in _UNIT_SECONDS.items():
            if unit.startswith(key) or key.startswith(unit):
                multiplier = value
                break
        if multiplier is None:
            continue
        total += number * multiplier
        found = True
    return int(total) if found else None


def duration_label(seconds: int) -> str:
    """«5 минут», «1 час 20 минут» — для подтверждения голосом."""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} {_plural(h, 'час', 'часа', 'часов')}")
    if m:
        parts.append(f"{m} {_plural(m, 'минуту', 'минуты', 'минут')}")
    if s and not h:
        parts.append(f"{s} {_plural(s, 'секунду', 'секунды', 'секунд')}")
    return " ".join(parts) or "0 секунд"


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def parse_clock(text: str, now: datetime | None = None) -> datetime | None:
    """«в 7:30», «в 7.30», «в 19 30», «в семь утра», «в 7 вечера».

    Возвращает ближайшее будущее время (сегодня или завтра).
    """
    now = now or datetime.now()
    t = normalize_text(text)

    period = None
    if re.search(r"\bутра\b|\bутром\b", t):
        period = "утро"
    elif re.search(r"\bвечера\b|\bвечером\b", t):
        period = "вечер"
    elif re.search(r"\bдня\b|\bднем\b", t):
        period = "день"
    elif re.search(r"\bночи\b|\bночью\b", t):
        period = "ночь"

    hour: int | None = None
    minute = 0

    m = re.search(r"\b(?:в|на)\s*(\d{1,2})\s*[:.\s]\s*(\d{2})\b", t)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
    if hour is None:
        m = re.search(r"\b(\d{1,2})\s*[:.]\s*(\d{2})\b", t)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))
    if hour is None:
        m = re.search(r"\b(?:в|на)\s+(\d{1,2})(?:\s*(?:час(?:ов|а)?|ч))?\b", t)
        if m:
            hour = int(m.group(1))
    if hour is None:
        word = r"(" + "|".join(_WORD_NUMBERS) + r")"
        m = re.search(rf"\b(?:в|на)\s+{word}(?:\s*(?:час(?:ов|а)?|ч))?\b", t)
        if m:
            value = _WORD_NUMBERS.get(m.group(1))
            hour = int(value) if value is not None else None
    if hour is None:
        return None

    if hour > 23 or minute > 59:
        return None

    # «в 7 вечера» → 19:00; «в 12 ночи» → 0:00; «в 7 утра» → 7:00.
    if period == "вечер" and hour < 12:
        hour += 12
    elif period == "день" and hour < 12 and hour != 12:
        hour += 12
    elif period == "ночь" and hour == 12:
        hour = 0
    elif period is None and 0 < hour <= 7:
        # Без уточнения «в 7» скорее значит утро, но если сейчас вечер —
        # логичнее завтрашнее утро. Оставляем 7:00 и решаем по «сегодня/завтра».
        pass

    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now + timedelta(seconds=5):
        candidate += timedelta(days=1)
    return candidate


def clock_label(dt: datetime, now: datetime | None = None) -> str:
    """«в 7:30» / «завтра в 7:30» — человеческая подпись."""
    now = now or datetime.now()
    hhmm = dt.strftime("%H:%M")
    if dt.date() == now.date():
        return f"в {hhmm}"
    if (dt.date() - now.date()).days == 1:
        return f"завтра в {hhmm}"
    return f"{dt.day} числа в {hhmm}"


_CLOCK_PATTERNS = [
    r"\b(?:в|на)\s*\d{1,2}\s*[:.]\s*\d{2}\b",
    r"\b\d{1,2}\s*[:.]\s*\d{2}\b",
    r"\b(?:в|на)\s*\d{1,2}\s*(?:час(?:ов|а)?|ч)?\s*(?:утра|вечера|дня|ночи)?\b",
    r"\b(?:в|на)\s+(?:" + "|".join(_WORD_NUMBERS) + r")(?:\s*(?:час(?:ов|а)?|ч))?\s*(?:утра|вечера|дня|ночи)?\b",
    r"\b\d{1,2}\s*(?:утра|вечера|дня|ночи)\b",
]


def extract_clock(text: str, now: datetime | None = None) -> tuple[datetime | None, str]:
    """Возвращает (время, остаток текста без выражения времени)."""
    t = normalize_text(text)
    if not t:
        return None, ""
    for pattern in _CLOCK_PATTERNS:
        for m in re.finditer(pattern, t):
            when = parse_clock(m.group(0), now)
            if when is not None:
                return when, _remove_spans(t, [m.span()])
    return None, t
