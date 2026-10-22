from datetime import datetime

from gul.utils import (
    SentenceBuffer, atomic_write_json, clean_for_speech, date_to_speech,
    duration_to_speech, num_ru, plural_ru, read_json, strip_wake_words,
    time_to_speech, truncate,
)


def test_plural_ru():
    assert plural_ru(1, "час", "часа", "часов") == "час"
    assert plural_ru(3, "час", "часа", "часов") == "часа"
    assert plural_ru(5, "час", "часа", "часов") == "часов"
    assert plural_ru(11, "час", "часа", "часов") == "часов"
    assert plural_ru(21, "час", "часа", "часов") == "час"


def test_num_ru():
    assert num_ru(0) == "ноль"
    assert num_ru(7) == "семь"
    assert num_ru(21) == "двадцать один"
    assert num_ru(105) == "сто пять"
    assert num_ru(2026) == "две тысячи двадцать шесть"


def test_time_and_date_speech():
    dt = datetime(2026, 10, 22, 14, 5)
    assert time_to_speech(dt) == "четырнадцать часов пять минут"
    assert "четверг" in date_to_speech(dt)
    assert "октября" in date_to_speech(dt)
    assert "пять минут" in duration_to_speech(300)


def test_clean_for_speech():
    assert clean_for_speech("**Привет**, мир!") == "Привет, мир!"
    assert "100 процентов" in clean_for_speech("Скидка 100%")
    assert "градусов" in clean_for_speech("На улице +5°C")
    assert "так далее" in clean_for_speech("яблоки, груши и т.д.")
    assert "http" not in clean_for_speech("Смотри https://example.com/page")
    assert "😊" not in clean_for_speech("Привет 😊")


def test_clean_for_speech_truncates():
    text = "Первое предложение. " * 100
    result = clean_for_speech(text, max_chars=80)
    assert len(result) <= 120
    assert result.endswith((".", "…"))


def test_sentence_buffer_streaming():
    buffer = SentenceBuffer()
    out = []
    for token in ["При", "вет. ", "Как ", "дела? ", "Всё ", "хорошо!"]:
        out.extend(buffer.add(token))
    out.extend(buffer.flush())
    assert out[0] == "Привет."
    assert any("Как дела?" in sentence for sentence in out)
    assert any("хорошо" in sentence for sentence in out)


def test_strip_wake_words():
    words = ["привет гуль", "слушай гуль"]
    assert strip_wake_words("Привет, Гуль, какая погода?", words) == "какая погода"
    assert strip_wake_words("просто вопрос", words) == "просто вопрос"


def test_atomic_json(tmp_path):
    path = tmp_path / "nested" / "data.json"
    atomic_write_json(path, {"ключ": "значение"})
    assert read_json(path, {}) == {"ключ": "значение"}
    assert read_json(tmp_path / "missing.json", {"x": 1}) == {"x": 1}


def test_truncate():
    assert truncate("коротко", 10) == "коротко"
    assert truncate("длинный текст", 8).endswith("…")
