"""Информационные скиллы: новости, курсы валют, Википедия."""

from __future__ import annotations

import logging
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

from ..utils import plural_ru, read_json, truncate
from .base import Skill, SkillContext, SkillResult

log = logging.getLogger(__name__)

_UA = {"User-Agent": "GulAssistant/1.0 (personal voice assistant; +https://github.com/)"}


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&#\d+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class NewsSkill(Skill):
    name = "news"
    title = "Новости"
    description = "Свежие заголовки из RSS-лент, можно спросить про спорт или технологии."
    examples = ["новости", "что нового в мире", "новости спорта"]
    priority = 72

    def __init__(self):
        self._lock = threading.Lock()
        self._cache: tuple[float, list[dict]] = (0, [])

    def _fetch(self, sources: list[str], proxy: str) -> list[dict]:
        items: list[dict] = []
        for url in sources:
            try:
                resp = requests.get(url, timeout=7, headers=_UA,
                                    proxies={"http": proxy, "https": proxy} if proxy else None)
                resp.raise_for_status()
                root = ET.fromstring(resp.content)
            except Exception as exc:
                log.debug("Новости: источник %s не ответил (%s)", url, exc)
                continue
            for node in root.iter():
                tag = node.tag.split("}")[-1]
                if tag not in ("item", "entry"):
                    continue
                title = ""
                description = ""
                for child in node:
                    ctag = child.tag.split("}")[-1]
                    if ctag == "title":
                        title = _strip_html(child.text or "")
                    elif ctag in ("description", "summary"):
                        description = _strip_html(child.text or "")
                if title:
                    items.append({"title": title, "description": description})
        return items

    def handle(self, text: str, ctx: SkillContext) -> SkillResult | None:
        if not re.search(r"(новост|что нового|что происходит|последние событ|сводка)", text):
            return None
        if re.search(r"(что такое|кто такой)", text):
            return None

        sources = ctx.get("skills.news.sources") or []
        max_items = int(ctx.get("skills.news.max_items", 4))
        if not sources:
            return None
        proxy = ctx.get("network.proxy", "") or ""
        now = time.time()
        with self._lock:
            cached_at, cached = self._cache
        if now - cached_at > 600 or not cached:
            fresh = self._fetch(sources, proxy)
            if fresh:
                cached = fresh
                with self._lock:
                    self._cache = (now, cached)
        if not cached:
            return SkillResult(text="Не удалось загрузить новости: источники недоступны. "
                                     "Проверьте интернет.")

        topic = None
        for word, key in [("спорт", "спорт"), ("технолог", "технолог"), ("наук", "наук"),
                          ("политик", "политик"), ("экономик", "экономик"),
                          ("культур", "культур"), ("мир", "мир")]:
            if word in text:
                topic = key
                break
        items = cached
        if topic and topic != "мир":
            items = [i for i in items if topic in (i["title"] + " " + i["description"]).lower()]
        if not items:
            return SkillResult(text="В свежих заголовках этой темы пока ничего нет.")

        titles = [i["title"] for i in items[:max_items]]
        spoken = ". ".join(t.rstrip(".") for t in titles[:3]) + "."
        text_out = "Свежие заголовки:\n• " + "\n• ".join(titles)
        return SkillResult(text=text_out, speak=spoken)


class CurrencySkill(Skill):
    name = "currency"
    title = "Курсы валют"
    description = "Официальные курсы ЦБ РФ и перевод сумм."
    examples = ["курс доллара", "курс валют", "сколько 100 долларов в рублях"]
    priority = 74

    _NAMES = {
        "доллар": "USD", "доллары": "USD", "долларов": "USD", "доллара": "USD",
        "бакс": "USD", "баксов": "USD", "бакса": "USD", "usd": "USD",
        "евро": "EUR", "eur": "EUR", "еврик": "EUR",
        "юань": "CNY", "юаня": "CNY", "юаней": "CNY", "юани": "CNY", "cny": "CNY",
        "фунт": "GBP", "фунтов": "GBP", "стерлинг": "GBP", "стерлингов": "GBP", "gbp": "GBP",
        "тенге": "KZT", "kzt": "KZT", "лира": "TRY", "лир": "TRY", "try": "TRY",
        "йена": "JPY", "йен": "JPY", "jpy": "JPY", "франк": "CHF", "франков": "CHF",
        "гривна": "UAH", "гривен": "UAH", "uah": "UAH",
    }
    _NOMINATIVE = {
        "USD": "Доллар", "EUR": "Евро", "CNY": "Юань", "GBP": "Фунт",
        "KZT": "Тенге", "TRY": "Лира", "JPY": "Йена", "CHF": "Франк", "UAH": "Гривна",
    }

    def __init__(self):
        self._lock = threading.Lock()
        self._cache: dict = {}

    def _load(self, ctx: SkillContext) -> dict | None:
        path = ctx.cfg.path("data/currency.json")
        with self._lock:
            if self._cache:
                return self._cache
        cached = read_json(path, {})
        ttl = float(ctx.get("skills.currency.cache_hours", 6)) * 3600
        if cached.get("fetched") and time.time() - cached["fetched"] < ttl:
            with self._lock:
                self._cache = cached
            return cached
        try:
            resp = requests.get("https://www.cbr.ru/scripts/XML_daily.asp", timeout=8, headers=_UA)
            resp.raise_for_status()
            resp.encoding = "windows-1251"
            root = ET.fromstring(resp.text)
            rates = {}
            for valute in root.findall("Valute"):
                code = valute.findtext("CharCode", "").strip()
                value_text = (valute.findtext("Value", "0") or "0").replace(",", ".")
                try:
                    value = float(value_text)
                except ValueError:
                    continue
                nominal = int(valute.findtext("Nominal", "1") or 1)
                rates[code] = {
                    "value": value / max(1, nominal),
                    "nominal": nominal,
                    "name": valute.findtext("Name", code),
                }
            data = {"fetched": time.time(), "date": root.get("Date", ""), "rates": rates}
            from ..utils import atomic_write_json

            atomic_write_json(path, data)
            with self._lock:
                self._cache = data
            return data
        except Exception as exc:
            log.warning("Курсы ЦБ недоступны: %s", exc)
            if cached:
                return cached
            return None

    @staticmethod
    def _rub(value: float) -> str:
        rub = int(value)
        kop = int(round((value - rub) * 100))
        if kop >= 100:
            rub += 1
            kop = 0
        text = f"{rub} {plural_ru(rub, 'рубль', 'рубля', 'рублей')}"
        if kop:
            text += f" {kop} {plural_ru(kop, 'копейка', 'копейки', 'копеек')}"
        return text

    def _find_code(self, text: str) -> str | None:
        for name, code in sorted(self._NAMES.items(), key=lambda kv: len(kv[0]), reverse=True):
            if re.search(rf"\b{re.escape(name)}\b", text):
                return code
        return None

    def handle(self, text: str, ctx: SkillContext) -> SkillResult | None:
        if not re.search(r"(курс|валют|сколько стоит|переведи|обмен|рубл)", text):
            return None
        code = self._find_code(text)
        if not code and "курс валют" not in text and "валют" not in text:
            return None

        data = self._load(ctx)
        if not data or not data.get("rates"):
            return SkillResult(text="Не удалось получить курсы валют: сайт Центробанка недоступен.")

        rates = data["rates"]
        stamp = data.get("date", "")
        source_note = f" Курс ЦБ на {stamp}." if stamp else ""

        # Перевод суммы: «100 долларов в рублях».
        amount_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:[а-я]+\s+)?", text)
        wants_conversion = bool(re.search(r"(переведи|сколько|в рубл|в рубл[а-я]*)", text))
        if code and wants_conversion:
            amount = None
            m = re.search(rf"(\d+(?:[.,]\d+)?)\s*[а-я]*\s*{re.escape(code.lower())}", text, re.I)
            if not m:
                m = re.search(r"(\d+(?:[.,]\d+)?)", text)
            if m:
                try:
                    amount = float(m.group(1).replace(",", "."))
                except ValueError:
                    amount = None
            if amount is not None and code in rates:
                rub = amount * rates[code]["value"]
                name = self._NOMINATIVE.get(code, code)
                answer = f"{amount:g} {name.lower()} — это примерно {self._rub(rub)}."
                if re.search(r"сколько (рубл|денег)|в рубл", text):
                    return SkillResult(text=answer + source_note)
                return SkillResult(text=answer + source_note)

        if code:
            if code not in rates:
                return SkillResult(text=f"Курс этой валюты ЦБ не публикует.")
            value = rates[code]["value"]
            name = self._NOMINATIVE.get(code, code)
            return SkillResult(
                text=f"{name} стоит {self._rub(value)}.{source_note}",
                speak=f"{name} стоит {int(value)} {plural_ru(int(value), 'рубль', 'рубля', 'рублей')}"
                      + (f" {int(round((value % 1) * 100))} копеек." if round((value % 1) * 100) else "."),
            )

        # Общий курс.
        parts = []
        for code in ("USD", "EUR", "CNY"):
            if code in rates:
                parts.append(f"{self._NOMINATIVE.get(code, code).lower()} — {self._rub(rates[code]['value'])}")
        if not parts:
            return SkillResult(text="Курсы валют сейчас недоступны.")
        return SkillResult(
            text="Курсы ЦБ: " + "; ".join(parts) + "." + source_note,
            speak="Курс ЦБ: " + "; ".join(parts) + ".",
        )


class WikiSkill(Skill):
    name = "wiki"
    title = "Википедия"
    description = "Краткая справка о человеке, месте или понятии."
    examples = ["кто такой Юрий Гагарин", "что такое чёрная дыра", "расскажи про Байкал"]
    priority = 70

    def __init__(self):
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, str]] = {}

    def _query(self, text: str) -> str | None:
        patterns = [
            r"^(?:а\s+)?кто так(?:ой|ая|ие)\s+(.+)$",
            r"^(?:а\s+)?что такое\s+(.+)$",
            r"^(?:расскажи|скажи|поведай)(?:\s+мне)?\s+(?:о|про)\s+(.+)$",
            r"^что (?:ты )?знаешь (?:о|про)\s+(.+)$",
            r"^(?:найди|поищи) в википедии\s+(.+)$",
        ]
        for pattern in patterns:
            m = re.match(pattern, text)
            if m:
                query = m.group(1).strip(" ?.!")
                query = re.sub(r"^(?:это|все про|всё про)\s+", "", query)
                if query and query not in ("себе", "себя", "тебе"):
                    return query
        return None

    def handle(self, text: str, ctx: SkillContext) -> SkillResult | None:
        if not ctx.get("skills.wiki.enabled", True):
            return None
        if re.search(r"(анекдот|погод|новост|курс|таймер|будильник|напомни)", text):
            return None
        query = self._query(text)
        if not query:
            return None
        with self._lock:
            cached = self._cache.get(query.lower())
        if cached and time.time() - cached[0] < 3600:
            return SkillResult(text=cached[1], speak=cached[1])

        proxy = ctx.get("network.proxy", "") or ""
        proxies = {"http": proxy, "https": proxy} if proxy else None
        try:
            search_url = (
                "https://ru.wikipedia.org/w/api.php?action=query&list=search&format=json"
                "&srlimit=1&utf8=1&srsearch=" + urllib.parse.quote(query)
            )
            search = requests.get(search_url, headers=_UA, timeout=6, proxies=proxies).json()
            results = (search.get("query") or {}).get("search") or []
            if not results:
                return None
            title = results[0]["title"]
            summary_url = ("https://ru.wikipedia.org/api/rest_v1/page/summary/"
                           + urllib.parse.quote(title.replace(" ", "_")))
            summary = requests.get(summary_url, headers=_UA, timeout=6, proxies=proxies).json()
            extract = _strip_html(summary.get("extract", ""))
            if not extract or len(extract) < 30:
                return None
            answer = truncate(extract, 420)
            with self._lock:
                self._cache[query.lower()] = (time.time(), answer)
            return SkillResult(text=answer, source="wiki")
        except Exception as exc:
            log.debug("Википедия недоступна: %s", exc)
            return None
