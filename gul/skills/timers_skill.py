"""Голосовое управление таймерами, будильниками и напоминаниями."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from ..utils import duration_to_speech, plural_ru
from .base import Skill, SkillContext, SkillResult
from .timeparse import clock_label, duration_label, extract_clock, extract_duration


class TimerSkill(Skill):
    name = "timers"
    title = "Таймеры, будильники, напоминания"
    description = "Ставит и отменяет таймеры и будильники, напоминает о делах."
    examples = [
        "поставь таймер на 10 минут",
        "будильник на 7:30",
        "напомни через час позвонить маме",
        "какие у меня будильники",
        "отмени все таймеры",
    ]
    priority = 85

    _LIST_RE = re.compile(
        r"(какие|мои|покажи|список|что).{0,20}(таймер|будильник|напомин)|"
        r"(таймер|будильник|напомин).{0,15}(какие|список|статус)|"
        r"^мои (таймеры|будильники|напоминания)$"
    )
    _CANCEL_RE = re.compile(
        r"(отмени|удали|убери|выключи|сними|останови).{0,25}(таймер|будильник|напомин)"
    )

    def handle(self, text: str, ctx: SkillContext) -> SkillResult | None:
        if ctx.timers is None:
            return None

        if self._LIST_RE.search(text):
            return self._list(ctx)

        if self._CANCEL_RE.search(text):
            return self._cancel(text, ctx)

        if re.search(r"\bнапомни", text):
            return self._reminder(text, ctx)

        if re.search(r"\b(будильник|разбуди)\b", text):
            return self._alarm(text, ctx)

        if re.search(r"\bтаймер\b", text) or re.search(r"\bзасеки\b", text):
            return self._timer(text, ctx)
        return None

    # -- создание --------------------------------------------------------
    def _timer(self, text: str, ctx: SkillContext) -> SkillResult:
        seconds, rest = extract_duration(text)
        if not seconds:
            return SkillResult(
                text="На сколько поставить таймер? Скажите, например: «таймер на 10 минут»."
            )
        label = self._clean_label(rest, ["поставь", "поставить", "таймер", "на", "засеки", "мне", "пожалуйста"])
        event = ctx.timers.add_timer(seconds, label)
        suffix = f" для «{label}»" if label else ""
        return SkillResult(
            text=f"Таймер на {duration_label(seconds)}{suffix} поставлен (id {event.id}).",
            speak=f"Хорошо, таймер на {duration_label(seconds)}.",
        )

    def _alarm(self, text: str, ctx: SkillContext) -> SkillResult:
        when, rest = extract_clock(text, ctx.now())
        if when is None:
            return SkillResult(
                text="На какое время поставить будильник? Скажите, например: «будильник на 7:30»."
            )
        daily = bool(re.search(r"(каждый день|ежедневно|каждое утро|по утрам)", text))
        label = self._clean_label(
            rest, ["поставь", "поставить", "будильник", "разбуди", "меня", "на", "в",
                   "каждый", "день", "ежедневно", "каждое", "утро", "по", "утрам", "мне"]
        )
        ctx.timers.add_alarm(when, daily=daily, label=label or "Будильник")
        repeat = ", каждый день" if daily else ""
        return SkillResult(
            text=f"Будильник {clock_label(when, ctx.now())}{repeat} поставлен.",
            speak=f"Будильник {clock_label(when, ctx.now())}{repeat}. Не проспите.",
        )

    def _reminder(self, text: str, ctx: SkillContext) -> SkillResult:
        when, rest = extract_clock(text, ctx.now())
        seconds = None
        if when is None:
            seconds, rest = extract_duration(text)
            if seconds:
                when = ctx.now() + timedelta(seconds=seconds)
        if when is None:
            return SkillResult(
                text="Когда напомнить? Скажите, например: «напомни через 15 минут выключить плиту» "
                     "или «напомни в 18:30 позвонить маме»."
            )
        label = self._clean_label(
            rest, ["напомни", "мне", "пожалуйста", "что", "чтобы", "о том", "про", "через", "в", "на"]
        )
        if not label:
            label = "Напоминание"
        ctx.timers.add_reminder(when, label)
        if seconds:
            return SkillResult(
                text=f"Напомню через {duration_label(seconds)}: {label}.",
                speak=f"Напомню через {duration_label(seconds)}: {label}.",
            )
        return SkillResult(
            text=f"Напомню {clock_label(when, ctx.now())}: {label}.",
            speak=f"Напомню {clock_label(when, ctx.now())}: {label}.",
        )

    # -- список и отмена --------------------------------------------------
    def _list(self, ctx: SkillContext) -> SkillResult:
        now = ctx.now()
        timers = ctx.timers.list("timer")
        alarms = ctx.timers.list("alarm")
        reminders = ctx.timers.list("reminder")
        all_events = sorted(timers + alarms + reminders, key=lambda e: e.fire_at)
        if not all_events:
            return SkillResult(text="Сейчас нет ни таймеров, ни будильников, ни напоминаний.")

        parts: list[str] = []
        for event in all_events[:6]:
            when = event.fire_datetime
            if event.kind == "timer":
                left = max(0, int((when - now).total_seconds()))
                parts.append(f"таймер на {duration_label(left)}")
            elif event.kind == "alarm":
                daily = ", каждый день" if event.daily else ""
                parts.append(f"будильник {clock_label(when, now)}{daily}")
            else:
                parts.append(f"напоминание {clock_label(when, now)}: {event.text}")
        summary = "; ".join(parts)
        count = len(all_events)
        summary = summary[:1].upper() + summary[1:] if summary else summary
        return SkillResult(
            text=f"Всего {count} {plural_ru(count, 'событие', 'события', 'событий')}. {summary}.",
            speak=". ".join(parts[:3]).capitalize() + ".",
        )

    def _cancel(self, text: str, ctx: SkillContext) -> SkillResult:
        kinds: list[str] = []
        if "таймер" in text:
            kinds.append("timer")
        if "будильник" in text:
            kinds.append("alarm")
        if "напомин" in text:
            kinds.append("reminder")
        if not kinds and re.search(r"отмени все", text):
            kinds = ["timer", "alarm", "reminder"]
        if not kinds:
            kinds = ["timer"]

        removed = 0
        for kind in kinds:
            removed += ctx.timers.cancel(kind)
        if not removed:
            return SkillResult(text="Нечего отменять — таких событий нет.")
        names = {"timer": "таймер", "alarm": "будильник", "reminder": "напоминание"}
        what = ", ".join(names[k] for k in kinds)
        return SkillResult(
            text=f"Отменила {what}. Удалено событий: {removed}.",
            speak=f"Отменила. Удалено событий: {removed}.",
        )

    @staticmethod
    def _clean_label(text: str, stop_words: list[str]) -> str:
        result = text
        for word in sorted(stop_words, key=len, reverse=True):
            result = re.sub(rf"\b{re.escape(word)}\b", " ", result, flags=re.I)
        result = re.sub(r"\s+", " ", result).strip(" .,!?;:—-")
        return result


def format_timer_fired(event) -> str:
    """Фраза, которую ассистент скажет при срабатывании."""
    from ..utils import time_to_speech

    if event.kind == "timer":
        extra = f" по задаче «{event.text}»" if event.text and event.text != "Таймер" else ""
        return f"Таймер сработал{extra}."
    if event.kind == "alarm":
        return f"Будильник! Сейчас {time_to_speech(event.fire_datetime)}. Доброе утро!"
    if event.kind == "reminder":
        return f"Напоминание: {event.text}."
    return "Событие сработало."
