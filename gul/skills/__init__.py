"""Скиллы Гуля — быстрые локальные команды, не требующие нейросети."""

from .base import Skill, SkillContext, SkillResult
from .registry import SkillRegistry, build_registry

__all__ = ["Skill", "SkillContext", "SkillResult", "SkillRegistry", "build_registry"]
