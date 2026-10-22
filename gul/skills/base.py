"""База для скиллов.

Скилл — это быстрый локальный обработчик голосовой команды. Если скилл
узнал команду, он отвечает за миллисекунды, без похода в нейросеть.
Если нет — вопрос уходит в LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from ..config import Config
from ..utils import clean_for_speech


@dataclass
class SkillResult:
    text: str
    speak: str | None = None
    source: str = "skill"
    # Если заполнено — вместо готового ответа отправляем этот промпт в LLM
    # (например, перевод). Так скилл может пользоваться «мозгом».
    needs_llm: str | None = None
    stop_audio: bool = False
    data: dict = field(default_factory=dict)

    def spoken(self, max_chars: int = 900) -> str:
        return clean_for_speech(self.speak or self.text, max_chars=max_chars)


@dataclass
class SkillContext:
    cfg: Config
    llm: Any = None
    timers: Any = None
    notes: Any = None
    memory: Any = None
    music: Any = None
    speaker: Any = None
    now: Callable[[], datetime] = datetime.now

    def get(self, path: str, default: Any = None) -> Any:
        return self.cfg.get(path, default)


class Skill:
    name = "base"
    title = "Скилл"
    description = ""
    examples: list[str] = []
    priority = 0
    # Показывать ли в списке умений веб-интерфейса.
    visible = True

    def handle(self, text: str, ctx: SkillContext) -> SkillResult | None:
        raise NotImplementedError

    # -- удобные помощники ------------------------------------------------
    @staticmethod
    def match(pattern: str, text: str, flags: int = re.I) -> re.Match | None:
        return re.search(pattern, text, flags)

    @staticmethod
    def pick(match: re.Match | None, group: int = 1, default: str = "") -> str:
        if not match:
            return default
        try:
            return (match.group(group) or default).strip()
        except (IndexError, AttributeError):
            return default

    def public(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "examples": self.examples,
        }
