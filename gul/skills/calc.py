"""Калькулятор и конвертер единиц — безопасный разбор без eval()."""

from __future__ import annotations

import ast
import operator
import re

from ..utils import plural_ru
from .base import Skill, SkillContext, SkillResult

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _safe_eval(expression: str) -> float:
    node = ast.parse(expression, mode="eval").body

    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN_OPS:
            left, right = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(right) > 12:
                raise ValueError("слишком большая степень")
            return _BIN_OPS[type(n.op)](left, right)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UNARY_OPS:
            return _UNARY_OPS[type(n.op)](ev(n.operand))
        raise ValueError("недопустимое выражение")

    return float(ev(node))


def _fmt(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


# Единицы: (множитель к базовой единице, базовая группа)
_UNITS = {
    "мм": (0.001, "m"), "миллиметр": (0.001, "m"), "миллиметров": (0.001, "m"),
    "см": (0.01, "m"), "сантиметр": (0.01, "m"), "сантиметров": (0.01, "m"),
    "м": (1, "m"), "метр": (1, "m"), "метров": (1, "m"), "метра": (1, "m"),
    "км": (1000, "m"), "километр": (1000, "m"), "километров": (1000, "m"), "километра": (1000, "m"),
    "г": (0.001, "kg"), "грамм": (0.001, "kg"), "граммов": (0.001, "kg"),
    "кг": (1, "kg"), "килограмм": (1, "kg"), "килограммов": (1, "kg"), "килограмма": (1, "kg"),
    "т": (1000, "kg"), "тонна": (1000, "kg"), "тонн": (1000, "kg"),
    "мл": (0.001, "l"), "миллилитр": (0.001, "l"), "миллилитров": (0.001, "l"),
    "л": (1, "l"), "литр": (1, "l"), "литров": (1, "l"), "литра": (1, "l"),
    "сек": (1, "s"), "секунда": (1, "s"), "секунд": (1, "s"),
    "мин": (60, "s"), "минута": (60, "s"), "минут": (60, "s"), "минуты": (60, "s"),
    "час": (3600, "s"), "часов": (3600, "s"), "часа": (3600, "s"),
}

_UNIT_CANONICAL = {
    "мм": "миллиметр", "см": "сантиметр", "м": "метр", "км": "километр",
    "г": "грамм", "кг": "килограмм", "т": "тонна",
    "мл": "миллилитр", "л": "литр",
    "сек": "секунда", "мин": "минута", "час": "час",
}
_UNIT_FORMS = {
    "миллиметр": ("миллиметр", "миллиметра", "миллиметров"),
    "сантиметр": ("сантиметр", "сантиметра", "сантиметров"),
    "метр": ("метр", "метра", "метров"),
    "километр": ("километр", "километра", "километров"),
    "грамм": ("грамм", "грамма", "граммов"),
    "килограмм": ("килограмм", "килограмма", "килограммов"),
    "тонна": ("тонна", "тонны", "тонн"),
    "миллилитр": ("миллилитр", "миллилитра", "миллилитров"),
    "литр": ("литр", "литра", "литров"),
    "секунда": ("секунда", "секунды", "секунд"),
    "минута": ("минута", "минуты", "минут"),
    "час": ("час", "часа", "часов"),
}


_CANONICAL_STEMS = [
    ("миллиметр", "миллиметр"), ("сантиметр", "сантиметр"),
    ("километр", "километр"), ("метр", "метр"),
    ("миллилитр", "миллилитр"), ("литр", "литр"),
    ("килограмм", "килограмм"), ("грамм", "грамм"), ("тонн", "тонна"),
    ("секунд", "секунда"), ("минут", "минута"), ("час", "час"),
]


def _canonical_for(token: str) -> str | None:
    for stem, canonical in _CANONICAL_STEMS:
        if stem in token:
            return canonical
    return {
        "м": "метр", "км": "километр", "см": "сантиметр", "мм": "миллиметр",
        "кг": "килограмм", "г": "грамм", "т": "тонна",
        "л": "литр", "мл": "миллилитр", "сек": "секунда", "мин": "минута", "ч": "час",
    }.get(token)


def _lookup_unit(token: str) -> tuple[float, str, str] | None:
    """Понимает падежи: «километрах», «метрами», «литров» и т.п."""
    candidates = [token]
    for suffix in ("ами", "ями", "ах", "ях", "ов", "ев", "ой", "ей", "ом", "ем",
                   "а", "я", "ы", "и", "у", "ю", "е", "о"):
        if len(token) > len(suffix) and token.endswith(suffix):
            base = token[: -len(suffix)]
            candidates.extend([base, base + "а", base + "ь"])
    for candidate in candidates:
        if candidate in _UNITS:
            factor, group = _UNITS[candidate]
            canonical = _UNIT_CANONICAL.get(candidate) or _canonical_for(candidate)
            if canonical:
                return factor, group, canonical
    return None


_UNIT_NAMES = {
    "m": ("метр", "метра", "метров"),
    "kg": ("килограмм", "килограмма", "килограммов"),
    "l": ("литр", "литра", "литров"),
    "s": ("секунда", "секунды", "секунд"),
}


class CalcSkill(Skill):
    name = "calc"
    title = "Калькулятор и конвертер"
    description = "Считает примеры, проценты и переводит единицы измерения."
    examples = ["сколько будет 2 плюс 2", "15 процентов от 2000", "сколько метров в 5 километрах"]
    priority = 78

    def handle(self, text: str, ctx: SkillContext) -> SkillResult | None:
        # Не трогаем время и команды таймеров: «18:30» — это не деление.
        if re.search(r"\b\d{1,2}\s*[:.]\s*\d{2}\b", text):
            return None
        if re.search(r"(таймер|будильник|напомни|буди|погод|новост|курс|включи|выключи)", text):
            return None
        conversion = self._convert_units(text)
        if conversion:
            return conversion
        percent = self._percent(text)
        if percent:
            return percent
        expr = self._expression(text)
        if not expr:
            return None
        try:
            value = _safe_eval(expr)
        except Exception:
            return None
        return SkillResult(text=f"Получается {_fmt(value)}.")

    # -- проценты --------------------------------------------------------
    def _percent(self, text: str) -> SkillResult | None:
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:процент\w*|%)\s*от\s*(\d+(?:[.,]\d+)?)", text)
        if not m:
            return None
        try:
            pct = float(m.group(1).replace(",", "."))
            base = float(m.group(2).replace(",", "."))
        except ValueError:
            return None
        return SkillResult(text=f"{_fmt(pct)} процентов от {_fmt(base)} — это {_fmt(pct / 100 * base)}.")

    # -- единицы ---------------------------------------------------------
    def _convert_units(self, text: str) -> SkillResult | None:
        # Два порядка слов: «5 км в метрах» и «сколько метров в 5 километрах».
        m = re.search(
            r"(?:сколько|переведи|перевести)?\s*(\d+(?:[.,]\d+)?)\s*"
            r"([а-я]+)\s*(?:в|это сколько в)\s*([а-я]+)", text)
        if m:
            amount = float(m.group(1).replace(",", "."))
            source, target = m.group(2), m.group(3)
        else:
            m = re.search(
                r"(?:сколько|переведи|перевести)\s+([а-я]+)\s+в\s+"
                r"(\d+(?:[.,]\d+)?)\s*([а-я]+)", text)
            if not m:
                return None
            quantity = float(m.group(2).replace(",", "."))
            source, target = m.group(3), m.group(1)
            amount = quantity
        src_unit = _lookup_unit(source)
        dst_unit = _lookup_unit(target)
        if src_unit is None or dst_unit is None:
            return None
        src_factor, src_group, src_canonical = src_unit
        dst_factor, dst_group, dst_canonical = dst_unit
        if src_group != dst_group:
            return None
        result = amount * src_factor / dst_factor
        names = _UNIT_FORMS.get(dst_canonical, _UNIT_NAMES.get(dst_group, ("", "", "")))
        # Форму слова подбираем по результату: «0,5 килограмма», «5 килограммов».
        rounded = int(round(result))
        is_fraction = abs(result - rounded) > 1e-9
        if is_fraction:
            word = names[1]
        elif rounded % 10 == 1 and rounded % 100 != 11:
            word = names[0]
        elif 2 <= rounded % 10 <= 4 and not 12 <= rounded % 100 <= 14:
            word = names[1]
        else:
            word = names[2]
        # «в 5 километрах» → «5 километров»: предлог «в» уже не нужен.
        src_word = source
        if source.endswith(("ах", "ях")):
            src_forms = _UNIT_FORMS.get(src_canonical, (source, source, source))
            src_word = src_forms[2]
        return SkillResult(text=f"{_fmt(amount)} {src_word} — это {_fmt(result)} {word}.")

    # -- арифметика ------------------------------------------------------
    def _expression(self, text: str) -> str | None:
        t = text
        t = re.sub(r"^(сколько (будет|это|составит)|посчитай|вычисли|рассчитай|"
                   r"сколько|какой результат)\s*", "", t)
        t = re.sub(r"\b(пожалуйста|мне|скажи)\b", " ", t)
        replacements = [
            (r"\bумножить на\b|\bумнож(?:ить|ь) на\b|\bумножить\b|\bумнож\b", "*"),
            (r"\bразделить на\b|\bраздели на\b|\bделить на\b|\bподелить на\b", "/"),
            (r"\bплюс\b|\bприбавить\b", "+"),
            (r"\bминус\b|\bотнять\b|\bвычесть\b", "-"),
            (r"\bв квадрате\b", "**2"),
            (r"\bв кубе\b", "**3"),
            (r"\bна\b", "*"),
        ]
        for pattern, repl in replacements:
            t = re.sub(pattern, repl, t)
        t = t.replace("=", "").replace("х", "*").replace("x", "*").replace("×", "*")
        t = t.replace(":", "/").replace("÷", "/")
        t = re.sub(r"(\d),(\d)", r"\1.\2", t)
        # Оставляем только цифры, операторы, скобки и точку.
        t = re.sub(r"[^0-9+\-*/().^ ]", " ", t).replace("^", "**")
        t = re.sub(r"\s+", "", t)
        if not t or not re.search(r"\d", t):
            return None
        if not re.search(r"[+\-*/]", t):
            return None
        return t
