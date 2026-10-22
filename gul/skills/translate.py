"""Перевод — скилл, который делегирует работу нейросети."""

from __future__ import annotations

import re

from .base import Skill, SkillContext, SkillResult

_LANGS = {
    "английский": "английский", "английски": "английский", "англ": "английский",
    "русский": "русский", "русски": "русский",
    "немецкий": "немецкий", "немецки": "немецкий",
    "французский": "французский", "французски": "французский",
    "испанский": "испанский", "испански": "испанский",
    "итальянский": "итальянский", "китайский": "китайский",
    "турецкий": "турецкий", "японский": "японский", "корейский": "корейский",
}


class TranslateSkill(Skill):
    name = "translate"
    title = "Перевод"
    description = "Переводит слова и фразы на десятки языков."
    examples = ["переведи на английский: доброе утро", "как по-немецки спасибо"]
    priority = 65

    def _language(self, text: str) -> str:
        for key, name in _LANGS.items():
            if re.search(rf"\b{key}", text):
                return name
        return "английский"

    def handle(self, text: str, ctx: SkillContext) -> SkillResult | None:
        m = re.search(r"переведи(?:\s+(?:мне|это|пожалуйста))*\s*(?:на\s+([а-я]+))?\s*[:\-—]?\s*(.+)", text)
        phrase = ""
        if m:
            phrase = m.group(2).strip(" .,!?;:—-")
        else:
            m = re.search(r"как (?:будет |сказать |по-?)([а-я]+)(?:ски|ский)?\s*[:\-—]?\s*(.+)", text)
            if m:
                phrase = m.group(2).strip(" .,!?;:—-")
        if not phrase or len(phrase) > 500:
            return None
        language = self._language(text)
        prompt = (
            f"Переведи на {language} язык следующий текст. Верни только перевод, "
            f"без пояснений и кавычек: «{phrase}»"
        )
        return SkillResult(
            text=f"Перевожу на {language}…",
            speak="",
            needs_llm=prompt,
        )
