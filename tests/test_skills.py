from datetime import datetime

import pytest

from gul.skills import build_registry
from gul.skills.info import CurrencySkill
from gul.skills.music import MusicPlayer
from gul.skills.weather import WeatherService


@pytest.fixture()
def registry(ctx):
    return build_registry(ctx.cfg)


def test_greeting(registry, ctx):
    result = registry.dispatch("привет", ctx)
    assert result is not None
    assert result.source == "skill"
    assert "привет" in result.text.lower()


def test_time(registry, ctx):
    ctx.now = lambda: datetime(2026, 10, 22, 14, 5)
    result = registry.dispatch("который час", ctx)
    assert "14:05" in result.text
    result = registry.dispatch("какое сегодня число", ctx)
    assert "четверг" in result.text.lower()


def test_help(registry, ctx):
    result = registry.dispatch("что ты умеешь", ctx)
    assert "таймер" in result.text.lower()
    assert result.speak


def test_stop(registry, ctx):
    result = registry.dispatch("стоп", ctx)
    assert result.stop_audio is True


def test_timers_create_and_cancel(registry, ctx, timers):
    ctx.now = lambda: datetime(2026, 10, 22, 6, 0)
    result = registry.dispatch("поставь таймер на 10 минут", ctx)
    assert "10 минут" in result.text
    assert len(timers.list("timer")) == 1

    result = registry.dispatch("поставь будильник на 7:30", ctx)
    assert "07:30" in result.text
    assert timers.list("alarm")[0].daily is False

    result = registry.dispatch("будильник на 7:30 каждый день", ctx)
    assert timers.list("alarm")[-1].daily is True

    result = registry.dispatch("напомни через 15 минут выключить плиту", ctx)
    assert any("плиту" in event.text for event in timers.list("reminder"))

    result = registry.dispatch("какие у меня будильники", ctx)
    assert "будильник" in result.text.lower()

    registry.dispatch("отмени все таймеры", ctx)
    assert timers.list("timer") == []
    assert len(timers.list("alarm")) == 2


def test_notes_and_memory(registry, ctx):
    result = registry.dispatch("добавь в список покупок молоко", ctx)
    assert "молоко" in result.text
    result = registry.dispatch("что в списке покупок", ctx)
    assert "молоко" in result.text

    result = registry.dispatch("запомни, что я люблю кофе без сахара", ctx)
    assert "Запомнила" in result.text
    result = registry.dispatch("что ты обо мне помнишь", ctx)
    assert "кофе" in result.text

    result = registry.dispatch("запиши заметку позвонить в сервис", ctx)
    assert "сервис" in result.text
    result = registry.dispatch("прочитай заметки", ctx)
    assert "сервис" in result.text


def test_weather_with_fake_data(registry, ctx, monkeypatch):
    data = {
        "current": {
            "temperature_2m": -3,
            "apparent_temperature": -6,
            "weather_code": 71,
            "wind_speed_10m": 2.5,
        },
        "daily": {
            "temperature_2m_max": [1, 0, -2],
            "temperature_2m_min": [-7, -8, -9],
            "weather_code": [73, 75, 71],
            "precipitation_probability_max": [80, 70, 60],
            "precipitation_sum": [1.2, 0.8, 0.4],
        },
        "_where": "в Москве",
    }
    monkeypatch.setattr(WeatherService, "get", lambda self, query=None: data)
    result = registry.dispatch("какая погода", ctx)
    assert "Москве" in result.text
    assert "минус 3" in result.text
    assert "снег" in result.text.lower()
    assert "зонт" in result.text.lower()

    result = registry.dispatch("погода на завтра", ctx)
    assert "Завтра" in result.text


def test_currency_with_fake_rates(ctx, monkeypatch):
    skill = CurrencySkill()
    monkeypatch.setattr(skill, "_load", lambda context: {
        "date": "22.10.2026",
        "rates": {
            "USD": {"value": 92.15, "nominal": 1, "name": "Доллар США"},
            "EUR": {"value": 100.5, "nominal": 1, "name": "Евро"},
            "CNY": {"value": 12.8, "nominal": 1, "name": "Юань"},
        },
    })
    result = skill.handle("курс доллара", ctx)
    assert "92" in result.text and "15" in result.text
    result = skill.handle("сколько 100 долларов в рублях", ctx)
    assert "9215" in result.text


def test_music_without_player(ctx, monkeypatch):
    monkeypatch.setattr(MusicPlayer, "available", lambda self: False)
    skill = __import__("gul.skills.music", fromlist=["MusicSkill"]).MusicSkill()
    result = skill.handle("включи радио европа плюс", ctx)
    assert result is not None
    assert "mpv" in result.text


def test_translate_needs_llm(registry, ctx):
    result = registry.dispatch("переведи на английский доброе утро", ctx)
    assert result.needs_llm
    assert "английский" in result.needs_llm


def test_system_shutdown_disabled(registry, ctx):
    result = registry.dispatch("выключи компьютер", ctx)
    assert "отключено" in result.text
