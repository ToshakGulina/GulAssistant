"""Быстрые скиллы: время, приветствие, помощь, «стоп»."""

from __future__ import annotations

import re
from datetime import datetime

from ..utils import date_to_speech, time_to_speech
from .base import Skill, SkillContext, SkillResult


class StopSkill(Skill):
    name = "stop"
    title = "Стоп"
    description = "Останавливает ответ, музыку и будильник."
    examples = ["стоп", "замолчи", "выключи музыку"]
    priority = 100

    def handle(self, text: str, ctx: SkillContext) -> SkillResult | None:
        if text not in {"стоп", "стоп стоп", "замолчи", "помолчи", "тише", "хватит",
                        "отмена", "отбой", "прекрати"}:
            return None
        stopped_music = False
        if ctx.music is not None:
            try:
                stopped_music = ctx.music.stop()
            except Exception:
                pass
        if ctx.speaker is not None:
            ctx.speaker.stop()
        return SkillResult(
            text="Хорошо." if not stopped_music else "Выключила музыку.",
            stop_audio=True,
        )


class GreetingSkill(Skill):
    name = "greeting"
    title = "Приветствие и болтовня ни о чём"
    description = "Здоровается, отвечает «как дела», представляется."
    examples = ["привет", "как дела", "кто ты", "как тебя зовут", "спасибо"]
    priority = 92

    _REPLIES = [
        (r"^(привет|здравствуй|здравствуйте|добрый день|добрый вечер|доброе утро|хай)$",
         "Привет! Я на связи. Чем помочь?"),
        (r"^(как дела|как ты|как настроение|как жизнь)$",
         "Всё отлично: работаю быстро и почти не греюсь. А у вас как дела?"),
        (r"^(кто ты|как тебя зовут|твое имя|что ты такое|ты кто)$",
         "Я Гуль — голосовой ассистент, которого вы собрали сами. "
         "Слышу вас локально, думаю в бесплатном облаке, а команды выполняю мгновенно."),
        (r"^(спасибо|благодарю|спасибо большое)$", "Всегда пожалуйста!"),
        (r"^(пока|до свидания|до встречи|спокойной ночи)$",
         "До встречи! Если что — просто позовите: «Привет, Гуль»."),
        (r"^(ты человек|ты робот|ты живой)$",
         "Я программа, но стараюсь быть полезной и не надоедать."),
    ]

    def handle(self, text: str, ctx: SkillContext) -> SkillResult | None:
        if len(text.split()) > 6:
            return None
        for pattern, reply in self._REPLIES:
            if re.search(pattern, text):
                return SkillResult(text=reply)
        return None


class TimeDateSkill(Skill):
    name = "time"
    title = "Время и дата"
    description = "Скажет время, день недели, число и год."
    examples = ["который час", "сколько времени", "какое сегодня число", "какой день недели"]
    priority = 88

    def handle(self, text: str, ctx: SkillContext) -> SkillResult | None:
        now = ctx.now()
        asks_time = re.search(
            r"(котор\w* час|сколько (сейчас )?времени|какое (сейчас )?время|"
            r"скажи время|время сейчас)", text)
        asks_date = re.search(
            r"(какое (сегодня )?число|какая (сегодня )?дата|какой (сегодня )?день|"
            r"какое сегодня|какой год|день недели|что за день)", text)

        if asks_time and asks_date:
            return SkillResult(
                text=f"Сейчас {now.strftime('%H:%M')}, {date_to_speech(now)}.",
                speak=f"Сейчас {time_to_speech(now)}. Сегодня {date_to_speech(now)}.",
            )
        if asks_time:
            return SkillResult(
                text=f"Сейчас {now.strftime('%H:%M')}.",
                speak=f"Сейчас {time_to_speech(now)}.",
            )
        if asks_date:
            return SkillResult(
                text=f"Сегодня {date_to_speech(now)}.",
                speak=f"Сегодня {date_to_speech(now)}.",
            )
        if re.search(r"^(сколько|который)\b", text) and "час" in text:
            return SkillResult(text=f"Сейчас {now.strftime('%H:%M')}.",
                               speak=f"Сейчас {time_to_speech(now)}.")
        return None


class HelpSkill(Skill):
    name = "help"
    title = "Что я умею"
    description = "Показывает список возможностей и примеры команд."
    examples = ["что ты умеешь", "помощь", "список команд"]
    priority = 90

    _SPEECH = (
        "Я умею почти всё, что Алиса, только без подписки и рекламы: "
        "скажу время и дату, поставлю таймер, будильник или напоминание, "
        "расскажу погоду и новости, курс валют, посчитаю, переведу, найду в Википедии, "
        "включу музыку или радио, запишу в заметки и запомню важное о вас. "
        "А на любые другие вопросы отвечу с помощью нейросети."
    )

    _TEXT = (
        "Быстрые команды (работают без интернета и нейросети):\n"
        "• «Который час», «какое сегодня число»\n"
        "• «Поставь таймер на 10 минут», «будильник на 7:30»\n"
        "• «Напомни через час позвонить маме»\n"
        "• «Погода», «погода на завтра», «погода в Питере»\n"
        "• «Новости», «курс доллара»\n"
        "• «Сколько будет 15 процентов от 2000»\n"
        "• «Добавь в список покупок молоко», «что в списке покупок»\n"
        "• «Запомни, что я люблю кофе без сахара»\n"
        "• «Включи радио Европа Плюс», «сделай громче», «пауза»\n"
        "• «Переведи на английский: доброе утро»\n"
        "• «Кто такой Юрий Гагарин»\n"
        "• «Стоп» — остановить ответ или музыку\n\n"
        "Всё остальное — свободный разговор с нейросетью: погода, наука, советы, "
        "идеи, тексты. Просто спросите."
    )

    def handle(self, text: str, ctx: SkillContext) -> SkillResult | None:
        if re.search(r"(что (ты )?умеешь|твои (возможности|умения)|список команд|"
                     r"помощь|как (тобой|тебя) пользоваться|команды)", text):
            return SkillResult(text=self._TEXT, speak=self._SPEECH)
        return None
