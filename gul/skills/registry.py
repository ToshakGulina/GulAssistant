"""Реестр скиллов и диспетчер команд."""

from __future__ import annotations

import logging

from ..config import Config
from ..utils import normalize_text
from .base import Skill, SkillContext, SkillResult
from .basic import GreetingSkill, HelpSkill, StopSkill, TimeDateSkill
from .calc import CalcSkill
from .info import CurrencySkill, NewsSkill, WikiSkill
from .music import MusicSkill
from .notes_memory import MemorySkill, NotesSkill
from .smarthome import SmartHomeSkill, SystemSkill
from .timers_skill import TimerSkill
from .translate import TranslateSkill
from .weather import WeatherSkill

log = logging.getLogger(__name__)


def build_registry(cfg: Config) -> "SkillRegistry":
    skills: list[Skill] = [
        StopSkill(),
        GreetingSkill(),
        HelpSkill(),
        TimeDateSkill(),
        TimerSkill(),
        WeatherSkill(),
        NewsSkill(),
        CurrencySkill(),
        CalcSkill(),
        NotesSkill(),
        MemorySkill(),
        WikiSkill(),
        MusicSkill(),
        SmartHomeSkill(),
        TranslateSkill(),
        SystemSkill(),
    ]
    return SkillRegistry(skills)


class SkillRegistry:
    def __init__(self, skills: list[Skill]):
        self.skills = sorted(skills, key=lambda s: s.priority, reverse=True)

    def by_name(self, name: str) -> Skill | None:
        for skill in self.skills:
            if skill.name == name:
                return skill
        return None

    def dispatch(self, text: str, ctx: SkillContext) -> SkillResult | None:
        normalized = normalize_text(text)
        if not normalized:
            return None
        for skill in self.skills:
            try:
                result = skill.handle(normalized, ctx)
            except Exception:
                log.exception("Скилл %s упал на запросе %r", skill.name, normalized)
                continue
            if result is not None:
                result.data.setdefault("skill", skill.name)
                return result
        return None

    def public(self) -> list[dict]:
        return [s.public() for s in self.skills if s.visible]
