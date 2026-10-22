"""Заметки, список покупок и долговременная память об пользователе."""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

from ..utils import atomic_write_json, plural_ru, read_json
from .base import Skill, SkillContext, SkillResult


class NotesStore:
    """Две простые коллекции: список покупок и заметки."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.data = read_json(path, {"shopping": [], "notes": []})
        self.data.setdefault("shopping", [])
        self.data.setdefault("notes", [])

    def _save(self) -> None:
        atomic_write_json(self.path, self.data)

    def add(self, bucket: str, text: str) -> dict:
        item = {"text": text.strip(), "ts": time.time()}
        with self._lock:
            self.data.setdefault(bucket, []).append(item)
            self._save()
        return item

    def remove(self, bucket: str, text: str) -> int:
        with self._lock:
            before = len(self.data.get(bucket, []))
            text_low = text.lower().strip()
            self.data[bucket] = [
                item for item in self.data.get(bucket, [])
                if text_low not in item["text"].lower()
            ]
            removed = before - len(self.data[bucket])
            if removed:
                self._save()
        return removed

    def clear(self, bucket: str) -> int:
        with self._lock:
            count = len(self.data.get(bucket, []))
            self.data[bucket] = []
            self._save()
        return count

    def items(self, bucket: str) -> list[dict]:
        with self._lock:
            return list(self.data.get(bucket, []))


class NotesSkill(Skill):
    name = "notes"
    title = "Заметки и список покупок"
    description = "Добавляет, читает и очищает список покупок и заметки."
    examples = ["добавь в список покупок молоко", "что в списке покупок",
                "запиши заметку позвонить в сервис", "очисти список покупок"]
    priority = 76

    _stores: dict[str, NotesStore] = {}
    _lock = threading.Lock()

    def store(self, ctx: SkillContext) -> NotesStore:
        key = str(ctx.cfg.path(ctx.get("skills.notes_file", "data/notes.json")))
        with self._lock:
            if key not in self._stores:
                self._stores[key] = NotesStore(Path(key))
            return self._stores[key]

    def handle(self, text: str, ctx: SkillContext) -> SkillResult | None:
        store = self.store(ctx)
        shopping = re.search(r"(список покуп|покуп|купить|куплю|магазин)", text)
        notes = re.search(r"(заметк|запиши|записать|блокнот)", text)

        # Добавление в покупки
        if shopping:
            if re.search(r"(добавь|внеси|запиши|купи|положи|нужно|надо)", text):
                item = self._extract_item(text, [
                    "добавь", "внеси", "запиши", "в", "список", "покупок", "покупки",
                    "купи", "купить", "положи", "нужно", "надо", "мне", "пожалуйста",
                ])
                if item:
                    store.add("shopping", item)
                    return SkillResult(text=f"Добавила в список покупок: {item}.")
            if re.search(r"(что|покажи|прочитай|скажи|какой).{0,15}(списк|покупк)|"
                         r"(список покупок).{0,10}(что|покажи)", text) or text in ("список покупок", "покупки"):
                items = store.items("shopping")
                if not items:
                    return SkillResult(text="Список покупок пуст.")
                names = [i["text"] for i in items]
                return SkillResult(
                    text="В списке покупок: " + ", ".join(names) + ".",
                    speak=f"В списке {len(names)} {plural_ru(len(names), 'пункт', 'пункта', 'пунктов')}: "
                          + ", ".join(names[:8]) + ".",
                )
            if re.search(r"(очисти|удали все|сотри)", text):
                count = store.clear("shopping")
                return SkillResult(text=f"Очистила список покупок ({count}).")
            m = re.search(r"(удали|убери|вычеркни)\s+(?:из списка\s+)?(.+)", text)
            if m:
                item = self._extract_item(m.group(2), ["из", "списка", "покупок", "покупки"])
                removed = store.remove("shopping", item) if item else 0
                return SkillResult(
                    text=f"Удалила «{item}» из списка." if removed else f"Не нашла «{item}» в списке."
                )

        # Заметки
        if notes:
            if re.search(r"(прочитай|покажи|что в заметках|какие заметки|список заметок)", text):
                items = store.items("notes")
                if not items:
                    return SkillResult(text="Заметок пока нет.")
                names = [i["text"] for i in items]
                return SkillResult(
                    text="Заметки: " + "; ".join(names) + ".",
                    speak=f"У вас {len(names)} {plural_ru(len(names), 'заметка', 'заметки', 'заметок')}: "
                          + "; ".join(names[:6]) + ".",
                )
            if re.search(r"(очисти|удали все|сотри)", text):
                count = store.clear("notes")
                return SkillResult(text=f"Удалила все заметки ({count}).")
            if re.search(r"(запиши|записать|добавь|создай|сохрани)", text):
                item = self._extract_item(text, [
                    "запиши", "записать", "заметку", "заметка", "добавь", "создай",
                    "сохрани", "мне", "пожалуйста", "что",
                ])
                if item:
                    store.add("notes", item)
                    return SkillResult(text=f"Записала заметку: {item}.")
        return None

    @staticmethod
    def _extract_item(text: str, stop_words: list[str]) -> str:
        result = text
        for word in sorted(stop_words, key=len, reverse=True):
            result = re.sub(rf"\b{re.escape(word)}\b", " ", result, flags=re.I)
        result = re.sub(r"\s+", " ", result).strip(" .,!?;:—-")
        return result


class MemoryStore:
    """Факты о пользователе, которые подмешиваются в системный промпт."""

    def __init__(self, path: Path, limit: int = 40):
        self.path = path
        self.limit = limit
        self._lock = threading.Lock()
        self.facts = read_json(path, [])
        if not isinstance(self.facts, list):
            self.facts = []

    def add(self, text: str) -> bool:
        text = re.sub(r"\s+", " ", text).strip(" .,!?;:—-")
        if not text or len(text) < 3:
            return False
        with self._lock:
            for fact in self.facts:
                if fact.get("text", "").lower() == text.lower():
                    return False
            self.facts.append({"text": text, "ts": time.time()})
            self.facts = self.facts[-self.limit:]
            atomic_write_json(self.path, self.facts)
        return True

    def forget(self, query: str | None = None) -> int:
        with self._lock:
            if query is None:
                removed = len(self.facts)
                self.facts = []
            else:
                before = len(self.facts)
                q = query.lower()
                self.facts = [f for f in self.facts if q not in f.get("text", "").lower()]
                removed = before - len(self.facts)
            atomic_write_json(self.path, self.facts)
        return removed

    def all(self) -> list[str]:
        with self._lock:
            return [f["text"] for f in self.facts]


class MemorySkill(Skill):
    name = "memory"
    title = "Память обо мне"
    description = "Запоминает факты о вас и использует их в разговоре."
    examples = ["запомни, что я люблю кофе без сахара", "что ты обо мне помнишь",
                "забудь про кофе"]
    priority = 75

    _stores: dict[str, MemoryStore] = {}
    _lock = threading.Lock()

    def store(self, ctx: SkillContext) -> MemoryStore:
        key = str(ctx.cfg.path(ctx.get("skills.memory_file", "data/memory.json")))
        with self._lock:
            if key not in self._stores:
                self._stores[key] = MemoryStore(Path(key))
            return self._stores[key]

    def handle(self, text: str, ctx: SkillContext) -> SkillResult | None:
        store = self.store(ctx)
        if re.search(r"(что (ты )?(обо мне )?помнишь|что ты знаешь обо мне|мои факты|"
                     r"что запомнила|покажи память)", text):
            facts = store.all()
            if not facts:
                return SkillResult(text="Я пока ничего о вас не запоминала. "
                                         "Скажите: «Запомни, что...» — и я буду учитывать это в разговоре.")
            return SkillResult(
                text="Я помню: " + "; ".join(facts) + ".",
                speak="Я помню следующее: " + "; ".join(facts[:6]) + ".",
            )
        m = re.search(r"(запомни|запомнить)[,\s]+(?:(?:что|это|пожалуйста|себе)[,\s]+)*(.+)", text)
        if m:
            fact = m.group(2).strip(" .,!?;:—-")
            if store.add(fact):
                return SkillResult(text=f"Запомнила: {fact}. Теперь буду это учитывать.")
            return SkillResult(text="Это я уже помню.")
        m = re.search(r"(забудь|удали из памяти|вычеркни из памяти)[,\s]+(?:(?:все|всё|про|о)[,\s]+)*(.*)", text)
        if m:
            query = m.group(2).strip(" .,!?;:—-") or None
            removed = store.forget(query)
            if not removed:
                return SkillResult(text="Не нашла, что забыть.")
            return SkillResult(text=f"Забыла ({removed})." if query is None
                               else f"Забыла всё про «{query}».")
        return None
