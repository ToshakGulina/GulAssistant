"""Погода через бесплатный Open-Meteo (без ключей и регистрации).

Данные кэшируются, чтобы ответ на «какая погода» звучал мгновенно.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import urllib.parse
from datetime import datetime

import requests

from ..utils import HttpError, http_json, plural_ru
from .base import Skill, SkillContext, SkillResult

log = logging.getLogger(__name__)

# Коды погоды Open-Meteo (WMO) → по-русски.
_CODES = {
    0: "ясно", 1: "в основном ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "изморозь",
    51: "слабая морось", 53: "морось", 55: "сильная морось",
    56: "ледяная морось", 57: "сильная ледяная морось",
    61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
    66: "ледяной дождь", 67: "сильный ледяной дождь",
    71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежная крупа",
    80: "небольшие ливни", 81: "ливни", 82: "сильные ливни",
    85: "снежные заряды", 86: "сильные снежные заряды",
    95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом",
}

_KNOWN_CITIES = {
    "москва": (55.7558, 37.6173, "Москве"),
    "москве": (55.7558, 37.6173, "Москве"),
    "питер": (59.9343, 30.3351, "Петербурге"),
    "питере": (59.9343, 30.3351, "Петербурге"),
    "москве": (55.7558, 37.6173, "Москве"),
    "петербург": (59.9343, 30.3351, "Петербурге"),
    "санкт-петербург": (59.9343, 30.3351, "Петербурге"),
    "санкт петербург": (59.9343, 30.3351, "Петербурге"),
    "новосибирск": (55.0084, 82.9357, "Новосибирске"),
    "екатеринбург": (56.8389, 60.6057, "Екатеринбурге"),
    "казань": (55.7963, 49.1088, "Казани"),
    "нижний новгород": (56.3269, 44.0059, "Нижнем Новгороде"),
    "челябинск": (55.1644, 61.4368, "Челябинске"),
    "самара": (53.1959, 50.1002, "Самаре"),
    "омск": (54.9885, 73.3242, "Омске"),
    "ростов-на-дону": (47.2357, 39.7015, "Ростове-на-Дону"),
    "уфа": (54.7388, 55.9721, "Уфе"),
    "красноярск": (56.0153, 92.8932, "Красноярске"),
    "воронеж": (51.6720, 39.1843, "Воронеже"),
    "пермь": (58.0105, 56.2502, "Перми"),
    "волгоград": (48.7080, 44.5133, "Волгограде"),
    "краснодар": (45.0355, 38.9753, "Краснодаре"),
    "саратов": (51.5336, 46.0343, "Саратове"),
    "тюмень": (57.1522, 65.5272, "Тюмени"),
    "иркутск": (52.2870, 104.3050, "Иркутске"),
    "хабаровск": (48.4827, 135.0838, "Хабаровске"),
    "владивосток": (43.1155, 131.8855, "Владивостоке"),
    "ярославль": (57.6261, 39.8845, "Ярославле"),
    "томск": (56.4846, 84.9482, "Томске"),
    "калининград": (54.7104, 20.4522, "Калининграде"),
    "сочи": (43.5855, 39.7231, "Сочи"),
    "мурманск": (68.9585, 33.0827, "Мурманске"),
    "сургут": (61.2540, 73.3962, "Сургуте"),
    "владимир": (56.1290, 40.4070, "Владимире"),
    "тула": (54.1961, 37.6182, "Туле"),
    "тверь": (56.8587, 35.9176, "Твери"),
    "архангельск": (64.5393, 40.5187, "Архангельске"),
    "якутск": (62.0355, 129.6755, "Якутске"),
}

_CITY_STOPWORDS = {
    "завтра", "сегодня", "сейчас", "погода", "погоду", "на", "в", "во", "у",
    "мне", "скажи", "какая", "какой", "будет", "ли", "дождь", "снег", "температура",
    "воздуха", "на", "улице", "сейчас", "днем", "ночью", "утром", "вечером",
}


class WeatherService:
    def __init__(self, cfg):
        self.cfg = cfg
        self.proxy = cfg.get("network.proxy", "") or ""
        self.city = cfg.get("skills.weather.city", "Москва")
        self.lat = float(cfg.get("skills.weather.lat", 55.7558))
        self.lon = float(cfg.get("skills.weather.lon", 37.6173))
        self.ttl = float(cfg.get("skills.weather.cache_minutes", 10)) * 60
        self._lock = threading.Lock()
        self._cache: dict[tuple, tuple[float, dict]] = {}
        self._geo_cache: dict[str, tuple[float, float, str]] = {}

    def resolve_city(self, query: str | None) -> tuple[float, float, str, str]:
        """Возвращает (lat, lon, «в Москве», ключ кэша)."""
        if not query:
            return self.lat, self.lon, f"в {_CITY_PREPOSITIONAL.get(self.city.lower(), self.city)}", "default"
        q = query.strip().lower().replace("ё", "е")
        if q in _KNOWN_CITIES:
            lat, lon, where = _KNOWN_CITIES[q]
            return lat, lon, f"в {where}", q
        if q in self._geo_cache:
            lat, lon, where = self._geo_cache[q]
            return lat, lon, f"в {where}", q
        try:
            url = ("https://geocoding-api.open-meteo.com/v1/search?name="
                   + urllib.parse.quote(query)
                   + "&count=1&language=ru&format=json")
            data = http_json("GET", url, proxy=self.proxy, timeout=6)
            results = (data or {}).get("results") or []
            if results:
                item = results[0]
                name = item.get("name", query)
                # Простое склонение: «в Москве» для большинства названий.
                where = _city_prepositional(name)
                self._geo_cache[q] = (float(item["latitude"]), float(item["longitude"]), where)
                return float(item["latitude"]), float(item["longitude"]), f"в {where}", q
        except Exception as exc:
            log.debug("Геокодинг не сработал: %s", exc)
        # Не смогли распознать — считаем погоду по городу из конфига.
        return self.lat, self.lon, f"в {_CITY_PREPOSITIONAL.get(self.city.lower(), self.city)}", "default"

    def get(self, query: str | None = None) -> dict:
        lat, lon, where, key = self.resolve_city(query)
        now = time.time()
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] < self.ttl:
                return cached[1]
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
            "weather_code,wind_speed_10m,precipitation"
            "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max,precipitation_sum"
            "&timezone=auto&forecast_days=3&wind_speed_unit=ms"
        )
        try:
            data = http_json("GET", url, proxy=self.proxy, timeout=7)
        except HttpError as exc:
            if cached:
                log.warning("Погода: сеть недоступна, отдаю кэш (%s)", exc)
                return cached[1]
            raise
        data["_where"] = where
        data["_city"] = key
        with self._lock:
            self._cache[key] = (now, data)
        return data

    def prewarm(self) -> None:
        try:
            self.get(None)
        except Exception:
            pass


_CITY_PREPOSITIONAL = {
    "москва": "Москве", "санкт-петербург": "Петербурге", "казань": "Казани",
    "уфа": "Уфе", "пермь": "Перми", "омск": "Омске", "томск": "Томске",
    "самара": "Самаре", "тула": "Туле", "тверь": "Твери", "сочи": "Сочи",
}


def _city_prepositional(name: str) -> str:
    low = name.lower()
    if low in _CITY_PREPOSITIONAL:
        return _CITY_PREPOSITIONAL[low]
    if low.endswith("а"):
        return name[:-1] + "е"
    if low.endswith("ь"):
        return name[:-1] + "и"
    return name + "е"


def _temp_label(value: float | int | None) -> str:
    if value is None:
        return "неизвестно"
    t = int(round(float(value)))
    if t < 0:
        return f"минус {abs(t)} {plural_ru(abs(t), 'градус', 'градуса', 'градусов')}"
    if t == 0:
        return "ноль градусов"
    return f"{t} {plural_ru(t, 'градус', 'градуса', 'градусов')}"


def _wind_label(value: float | None) -> str:
    if value is None:
        return ""
    v = float(value)
    if v < 0.5:
        return "ветра почти нет"
    return f"ветер {int(round(v))} {plural_ru(int(round(v)), 'метр', 'метра', 'метров')} в секунду"


class WeatherSkill(Skill):
    name = "weather"
    title = "Погода"
    description = "Текущая погода и прогноз на 2 дня, в любом городе."
    examples = ["какая погода", "погода на завтра", "погода в Питере", "нужен ли зонт"]
    priority = 80

    def __init__(self):
        self._service: WeatherService | None = None

    def service(self, ctx: SkillContext) -> WeatherService:
        if self._service is None:
            self._service = WeatherService(ctx.cfg)
        return self._service

    def _city_query(self, text: str) -> str | None:
        m = re.search(r"\b(?:в|во)\s+([а-яa-z][а-яa-z\- ]{2,25}?)(?:\s+(?:завтра|сегодня|сейчас|будет|на|погода))?$", text)
        if m:
            candidate = m.group(1).strip()
            words = [w for w in candidate.split() if w not in _CITY_STOPWORDS]
            if words:
                return " ".join(words)
        m = re.search(r"\bпогод\w*\s+(?:в|во)\s+([а-яa-z][а-яa-z\- ]{2,25})", text)
        if m:
            return m.group(1).strip()
        return None

    def handle(self, text: str, ctx: SkillContext) -> SkillResult | None:
        if not re.search(r"(погод|температур|зонт|дожд|снег|ветер|тепло ли|холодно ли)", text):
            return None
        # «Что такое снег» — это не погода, а вопрос к нейросети/Википедии.
        if re.search(r"(что такое|кто такой|расскажи о|почему)", text):
            return None

        city = self._city_query(text)
        try:
            data = self.service(ctx).get(city)
        except Exception as exc:
            return SkillResult(
                text="Не могу получить погоду: нет связи с сервисом прогноза. "
                     "Проверьте интернет и попробуйте ещё раз."
            )

        current = data.get("current") or {}
        daily = data.get("daily") or {}
        where = data.get("_where", "на улице")
        code = current.get("weather_code")
        desc = _CODES.get(int(code), "без осадков") if code is not None else "без осадков"
        temp = current.get("temperature_2m")
        feels = current.get("apparent_temperature")
        wind = _wind_label(current.get("wind_speed_10m"))

        day_index = 0
        if "послезавтра" in text:
            day_index = 2
        elif "завтра" in text:
            day_index = 1

        try:
            max_temp = (daily.get("temperature_2m_max") or [])[day_index]
            min_temp = (daily.get("temperature_2m_min") or [])[day_index]
            day_code = (daily.get("weather_code") or [])[day_index]
            rain_prob = (daily.get("precipitation_probability_max") or [None])[day_index]
            rain_sum = (daily.get("precipitation_sum") or [None])[day_index]
        except (IndexError, TypeError):
            max_temp = min_temp = day_code = rain_prob = rain_sum = None

        day_desc = _CODES.get(int(day_code), "без осадков") if day_code is not None else None

        if day_index > 0:
            label = "Завтра" if day_index == 1 else "Послезавтра"
            parts = [f"{label} {where} {desc if day_index == 0 else day_desc}"]
            if min_temp is not None and max_temp is not None:
                parts.append(f"ночью {_temp_label(min_temp)}, днём {_temp_label(max_temp)}")
            if rain_prob is not None:
                parts.append(f"вероятность осадков {int(rain_prob)} процентов")
            text_out = ", ".join(parts) + "."
            if rain_prob and rain_prob >= 50:
                text_out += " Лучше взять зонт."
            return SkillResult(text=text_out, speak=text_out)

        parts = [f"Сейчас {where} {_temp_label(temp)}, {desc}"]
        if feels is not None and abs(float(feels) - float(temp or 0)) >= 2:
            parts.append(f"ощущается как {_temp_label(feels)}")
        if wind:
            parts.append(wind)
        if min_temp is not None and max_temp is not None:
            parts.append(f"днём до {_temp_label(max_temp)}, ночью до {_temp_label(min_temp)}")
        if rain_prob is not None:
            parts.append(f"вероятность осадков {int(rain_prob)} процентов")
        answer = ", ".join(parts) + "."
        if (rain_prob and rain_prob >= 55) or (rain_sum and float(rain_sum) >= 0.5):
            answer += " Возьмите зонт."
        elif "зонт" in text:
            answer += " Зонт, скорее всего, не понадобится."
        return SkillResult(text=answer, speak=answer)
