import pytest

from gul.config import load_config
from gul.skills import SkillContext
from gul.skills.calc import CalcSkill, _safe_eval, _fmt


@pytest.fixture()
def skill():
    return CalcSkill()


@pytest.fixture()
def context():
    return SkillContext(cfg=load_config())


def test_arithmetic(skill, context):
    assert "8" in skill.handle("сколько будет 2 плюс 2 умножить на 3", context).text
    assert "14" in skill.handle("посчитай 10 + 4", context).text
    assert "4" in skill.handle("2 в квадрате", context).text


def test_percent(skill, context):
    result = skill.handle("15 процентов от 2000", context)
    assert "300" in result.text
    assert "процентов" in result.text


def test_units(skill, context):
    assert "5000 метров" in skill.handle("5 километров в метрах", context).text
    assert "2000 граммов" in skill.handle("2 кг в граммах", context).text
    assert "0,5 килограмма" in skill.handle("500 граммов в килограммах", context).text


def test_does_not_hijack_time_or_timers(skill, context):
    assert skill.handle("напомни в 18:30 позвонить маме", context) is None
    assert skill.handle("поставь таймер на 5 минут", context) is None
    assert skill.handle("просто текст без чисел", context) is None


def test_safe_eval_rejects_code():
    assert _safe_eval("2+2") == 4
    with pytest.raises(Exception):
        _safe_eval("__import__('os').system('ls')")
    with pytest.raises(Exception):
        _safe_eval("2**999")


def test_fmt():
    assert _fmt(4.0) == "4"
    assert _fmt(2.5) == "2,5"
    assert _fmt(1.234567) == "1,2346"
