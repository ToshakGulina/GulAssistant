from datetime import datetime

from gul.skills.timeparse import (
    clock_label, duration_label, extract_clock, extract_duration,
    parse_clock, parse_duration_seconds,
)


def test_duration_simple():
    assert parse_duration_seconds("5 минут") == 300
    assert parse_duration_seconds("через 1 час 20 минут") == 4800
    assert parse_duration_seconds("полчаса") == 1800
    assert parse_duration_seconds("полтора часа") == 5400
    assert parse_duration_seconds("45 секунд") == 45
    assert parse_duration_seconds("два часа") == 7200


def test_extract_duration_leaves_text():
    seconds, rest = extract_duration("напомни через 10 минут полить чай")
    assert seconds == 600
    assert "полить чай" in rest
    assert "минут" not in rest


def test_clock_formats():
    now = datetime(2026, 10, 22, 6, 0)
    assert parse_clock("в 7:30", now) == datetime(2026, 10, 22, 7, 30)
    assert parse_clock("в 7 вечера", now) == datetime(2026, 10, 22, 19, 0)
    assert parse_clock("в 12 ночи", now) == datetime(2026, 10, 23, 0, 0)
    assert parse_clock("на 19 30", now) == datetime(2026, 10, 22, 19, 30)
    assert parse_clock("в семь утра", now) == datetime(2026, 10, 22, 7, 0)


def test_extract_clock():
    when, rest = extract_clock("будильник на 7:30", datetime(2026, 10, 22, 6, 0))
    assert when == datetime(2026, 10, 22, 7, 30)
    assert "будильник" in rest
    when, rest = extract_clock("напомни в 18:30 позвонить маме", datetime(2026, 10, 22, 9, 0))
    assert when.hour == 18 and when.minute == 30
    assert "позвонить маме" in rest


def test_labels():
    assert duration_label(300) == "5 минут"
    assert duration_label(5400) == "1 час 30 минут"
    now = datetime(2026, 10, 22, 9, 0)
    assert clock_label(datetime(2026, 10, 22, 19, 30), now) == "в 19:30"
    assert clock_label(datetime(2026, 10, 23, 7, 0), now) == "завтра в 07:00"
