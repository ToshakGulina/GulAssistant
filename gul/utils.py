"""Мелкие полезные функции: время по-русски, предложения, HTTP, файлы."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

try:  # requests нужен всегда, но пусть импорт будет мягким
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore


# --------------------------------------------------------------------------
# Русский язык
# --------------------------------------------------------------------------

def plural_ru(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def num_ru(n: int) -> str:
    """Числа словами до 9999 — для аккуратной озвучки времени и дат."""
    ones = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь",
            "восемь", "девять", "десять", "одиннадцать", "двенадцать",
            "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать",
            "семнадцать", "восемнадцать", "девятнадцать"]
    tens = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят",
            "семьдесят", "восемьдесят", "девяносто"]
    hundreds = ["", "сто", "двести", "триста", "четыреста", "пятьсот",
                "шестьсот", "семьсот", "восемьсот", "девятьсот"]
    n = int(n)
    if n < 0:
        return "минус " + num_ru(-n)
    if n < 20:
        return ones[n]
    if n < 100:
        return (tens[n // 10] + (" " + ones[n % 10] if n % 10 else "")).strip()
    if n < 1000:
        return (hundreds[n // 100] + (" " + num_ru(n % 100) if n % 100 else "")).strip()
    if n < 10000:
        thousands = n // 1000
        word = ["", "одна тысяча", "две тысячи", "три тысячи", "четыре тысячи",
                "пять тысяч", "шесть тысяч", "семь тысяч", "восемь тысяч",
                "девять тысяч"][thousands]
        return (word + (" " + num_ru(n % 1000) if n % 1000 else "")).strip()
    return str(n)


def time_to_speech(dt: datetime) -> str:
    """«четырнадцать часов пять минут» — так TTS не запнётся."""
    h, m = dt.hour, dt.minute
    if m == 0:
        return f"{num_ru(h)} {plural_ru(h, 'час', 'часа', 'часов')} ровно"
    return f"{num_ru(h)} {plural_ru(h, 'час', 'часа', 'часов')} {num_ru(m)} {plural_ru(m, 'минута', 'минуты', 'минут')}"


def date_to_speech(dt: datetime) -> str:
    months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]
    weekdays = ["понедельник", "вторник", "среда", "четверг", "пятница",
                "суббота", "воскресенье"]
    return f"{weekdays[dt.weekday()]}, {num_ru(dt.day)} {months[dt.month - 1]} {num_ru(dt.year)} года"


def duration_to_speech(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts: list[str] = []
    if h:
        parts.append(f"{num_ru(h)} {plural_ru(h, 'час', 'часа', 'часов')}")
    if m:
        parts.append(f"{num_ru(m)} {plural_ru(m, 'минута', 'минуты', 'минут')}")
    if s or not parts:
        parts.append(f"{num_ru(s)} {plural_ru(s, 'секунда', 'секунды', 'секунд')}")
    return " ".join(parts)


def normalize_text(text: str) -> str:
    """Нижний регистр, ё→е, без пунктуации по краям — для разбора команд."""
    text = (text or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[\u2018\u2019\u201c\u201d«»]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .,!?;:—-")


def strip_wake_words(text: str, wake_words: Iterable[str]) -> str:
    """Убирает «Привет, Гуль» в начале, даже если распознано с запятыми."""
    norm = normalize_text(text)
    spaced = re.sub(r"[^0-9a-zа-я\s]+", " ", norm)
    spaced = re.sub(r"\s+", " ", spaced).strip()
    for word in sorted(wake_words, key=len, reverse=True):
        w = re.sub(r"\s+", " ", normalize_text(word))
        if spaced == w:
            return ""
        if spaced.startswith(w + " "):
            return spaced[len(w):].strip()
    return norm


# --------------------------------------------------------------------------
# Текст для озвучки
# --------------------------------------------------------------------------

_MD_RE = re.compile(r"(\*\*|__|\*|_|`{1,3}|~~)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_URL_RE = re.compile(r"https?://\S+")
_EMOJI_RE = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\U00002600-\U000027BF" "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF" "\U00002B00-\U00002BFF" "\uFE0F\u200D" "]",
    flags=re.UNICODE,
)

_SPEECH_REPLACEMENTS = [
    (re.compile(r"\bт\.\s*д\.?", re.I), "так далее"),
    (re.compile(r"\bт\.\s*п\.?", re.I), "тому подобное"),
    (re.compile(r"\bт\.\s*е\.?", re.I), "то есть"),
    (re.compile(r"\bт\.\s*к\.?", re.I), "так как"),
    (re.compile(r"\bдр\.", re.I), "другое"),
    (re.compile(r"\bруб\.?", re.I), "рублей"),
    (re.compile(r"\bкоп\.?", re.I), "копеек"),
    (re.compile(r"(\d)\s*°\s*C", re.I), r"\1 градусов"),
    (re.compile(r"(\d)\s*%"), r"\1 процентов"),
    (re.compile(r"№\s*"), "номер "),
    (re.compile(r"\s*—\s*"), ", "),
    (re.compile(r"\s*–\s*"), ", "),
]


def clean_for_speech(text: str, max_chars: int = 900) -> str:
    text = _LINK_RE.sub(r"\1", text or "")
    text = _URL_RE.sub("", text)
    text = _MD_RE.sub("", text)
    text = _EMOJI_RE.sub("", text)
    for pattern, repl in _SPEECH_REPLACEMENTS:
        text = pattern.sub(repl, text)
    text = re.sub(r"[#>*|]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        cut = text[:max_chars]
        # Обрезаем по последнему предложению, чтобы не рвать мысль.
        pos = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
        text = cut[: pos + 1] if pos > max_chars * 0.5 else cut.rsplit(" ", 1)[0] + "…"
    return text


_ABBREV = {
    "т.е", "т.д", "т.п", "т.к", "др", "г", "гг", "ул", "стр", "руб", "коп",
    "мин", "сек", "им", "пр", "пл", "рис", "табл", "напр", "см", "в", "вв",
    "н.э", "до н.э", "англ", "нем", "рус", "проф", "акад",
}


def _is_sentence_boundary(text: str, i: int) -> bool:
    ch = text[i]
    if ch not in ".!?…":
        return False
    if ch == ".":
        prev_word = text[:i].rsplit(" ", 1)[-1].lower().strip(".,!?()«»\"")
        if prev_word in _ABBREV or (len(prev_word) == 1 and prev_word.isalpha()):
            return False
        if i > 0 and i + 1 < len(text) and text[i - 1].isdigit() and text[i + 1].isdigit():
            return False  # десятичная дробь 3.5
    if i + 1 < len(text) and not text[i + 1].isspace():
        return False
    return True


class SentenceBuffer:
    """Собирает токены LLM в готовые к озвучке предложения.

    Нейросеть отдаёт текст кусками; чтобы не ждать весь ответ, озвучиваем
    первое предложение, пока генерируется второе. Это и есть «ответ за 2 секунды».
    """

    def __init__(self, max_chars: int = 220):
        self.buf = ""
        self.max_chars = max_chars

    def add(self, token: str) -> list[str]:
        self.buf += token or ""
        out: list[str] = []
        while True:
            split_at = self._find_boundary()
            if split_at is None:
                break
            sentence = self.buf[: split_at + 1].strip()
            self.buf = self.buf[split_at + 1:].lstrip()
            cleaned = clean_for_speech(sentence)
            if cleaned:
                out.append(cleaned)
        # Слишком длинное «предложение» (модель забыла про точки) режем по запятым.
        while len(self.buf) > self.max_chars:
            cut = self.buf.rfind(",", 0, self.max_chars)
            if cut < 60:
                cut = self.buf.rfind(" ", 0, self.max_chars)
            if cut < 40:
                cut = self.max_chars
            chunk = clean_for_speech(self.buf[:cut].strip())
            if chunk:
                out.append(chunk)
            self.buf = self.buf[cut + 1:].lstrip()
        return out

    def _find_boundary(self) -> int | None:
        if len(self.buf) < 15:
            return None
        for i in range(len(self.buf) - 1):
            if _is_sentence_boundary(self.buf, i):
                if i + 1 >= len(self.buf):
                    return None  # ждём следующий токен, чтобы не гадать
                return i
        return None

    def flush(self) -> list[str]:
        rest = clean_for_speech(self.buf.strip())
        self.buf = ""
        return [rest] if rest else []


# --------------------------------------------------------------------------
# Файлы
# --------------------------------------------------------------------------

def atomic_write_json(path: str | os.PathLike, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path: str | os.PathLike, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


# --------------------------------------------------------------------------
# Сеть
# --------------------------------------------------------------------------

class HttpError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def http_json(
    method: str,
    url: str,
    *,
    proxy: str = "",
    timeout: float = 12,
    headers: dict | None = None,
    params: dict | None = None,
    data: Any = None,
    json_body: Any = None,
    session: Any = None,
    verify: bool = True,
) -> Any:
    if requests is None:  # pragma: no cover
        raise HttpError("Библиотека requests не установлена")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        resp = (session or requests).request(
            method, url, timeout=timeout, headers=headers, params=params,
            data=data, json=json_body, proxies=proxies, verify=verify,
            allow_redirects=True,
        )
    except Exception as exc:  # requests.RequestException и ошибки прокси
        raise HttpError(f"Сеть недоступна: {exc.__class__.__name__}") from exc
    if resp.status_code >= 400:
        raise HttpError(f"HTTP {resp.status_code} от {url}", status=resp.status_code)
    try:
        return resp.json()
    except ValueError as exc:
        raise HttpError(f"Некорректный JSON от {url}") from exc


def human_uptime(seconds: float) -> str:
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d} д {h} ч"
    if h:
        return f"{h} ч {m} мин"
    if m:
        return f"{m} мин"
    return f"{s} с"


def cpu_info() -> dict:
    """Модель CPU и наличие критичных для бинарников инструкций."""
    info = {"model": "неизвестно", "flags": [], "avx": False, "sse4": False}
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            text = fh.read()
        m = re.search(r"model name\s*:\s*(.+)", text)
        if m:
            info["model"] = m.group(1).strip()
        f = re.search(r"flags\s*:\s*(.+)", text)
        if f:
            flags = set(f.group(1).split())
            info["flags"] = sorted(flags)
            info["avx"] = "avx" in flags
            info["sse4"] = "sse4_1" in flags
    except OSError:
        pass
    return info


def mem_info() -> dict:
    """Память в мегабайтах (без psutil)."""
    out = {"total": 0, "available": 0, "used_percent": 0.0}
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            values = {}
            for line in fh:
                key, _, rest = line.partition(":")
                values[key] = int(rest.strip().split()[0])  # kB
        total = values.get("MemTotal", 0) // 1024
        avail = values.get("MemAvailable", values.get("MemFree", 0)) // 1024
        out["total"] = total
        out["available"] = avail
        out["used_percent"] = round((total - avail) / total * 100, 1) if total else 0.0
    except OSError:
        pass
    return out


def truncate(text: str, limit: int = 300) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
