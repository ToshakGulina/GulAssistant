"""Таймеры, будильники и напоминания.

Работают локально, без интернета, и переживают перезапуск: всё хранится
в data/timers.json. Проверка — каждые полсекунды, точность ±0.5 с.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from ..config import Config
from ..utils import atomic_write_json, read_json

log = logging.getLogger(__name__)


@dataclass
class TimerEvent:
    id: str
    kind: str  # timer | alarm | reminder
    fire_at: float
    text: str = ""
    daily: bool = False
    created: float = field(default_factory=time.time)

    @property
    def fire_datetime(self) -> datetime:
        return datetime.fromtimestamp(self.fire_at)


class TimerManager:
    def __init__(self, cfg: Config, on_fire: Callable[[TimerEvent], None] | None = None):
        self.cfg = cfg
        self.path: Path = cfg.path(cfg.get("skills.timers_file", "data/timers.json"))
        self.on_fire = on_fire
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._events: list[TimerEvent] = []
        self._load()
        self._thread = threading.Thread(target=self._run, name="timers", daemon=True)
        self._thread.start()

    # -- хранение --------------------------------------------------------
    def _load(self) -> None:
        data = read_json(self.path, [])
        events = []
        for item in data if isinstance(data, list) else []:
            try:
                events.append(TimerEvent(**item))
            except TypeError:
                continue
        self._events = events
        # Пропущенные, пока ассистент был выключен: срабатываем, если недавно.
        now = time.time()
        for event in list(events):
            if event.fire_at < now - 600 and not event.daily:
                self._events.remove(event)
        self._save()

    def _save(self) -> None:
        atomic_write_json(self.path, [asdict(e) for e in self._events])

    # -- API -------------------------------------------------------------
    def add(self, kind: str, fire_at: datetime, text: str = "",
            daily: bool = False) -> TimerEvent:
        event = TimerEvent(
            id=uuid.uuid4().hex[:8],
            kind=kind,
            fire_at=fire_at.timestamp(),
            text=text,
            daily=daily,
        )
        with self._lock:
            self._events.append(event)
            self._save()
        log.info("Добавлено: %s %s на %s", kind, text, fire_at)
        return event

    def add_timer(self, seconds: int, label: str = "") -> TimerEvent:
        return self.add("timer", datetime.now() + timedelta(seconds=max(1, seconds)), label)

    def add_alarm(self, when: datetime, daily: bool = False, label: str = "") -> TimerEvent:
        return self.add("alarm", when, label, daily=daily)

    def add_reminder(self, when: datetime, text: str) -> TimerEvent:
        return self.add("reminder", when, text)

    def list(self, kind: str | None = None) -> list[TimerEvent]:
        with self._lock:
            events = [e for e in self._events if kind is None or e.kind == kind]
        return sorted(events, key=lambda e: e.fire_at)

    def cancel(self, kind: str | None = None, event_id: str | None = None) -> int:
        with self._lock:
            before = len(self._events)
            self._events = [
                e for e in self._events
                if not ((event_id and e.id == event_id) or (not event_id and (kind is None or e.kind == kind)))
            ]
            removed = before - len(self._events)
            if removed:
                self._save()
        return removed

    def stop(self) -> None:
        self._stop.set()

    # -- планировщик -----------------------------------------------------
    def _run(self) -> None:
        while not self._stop.wait(0.5):
            now = time.time()
            due: list[TimerEvent] = []
            with self._lock:
                for event in list(self._events):
                    if event.fire_at <= now:
                        due.append(event)
                        if event.daily:
                            event.fire_at = (event.fire_datetime + timedelta(days=1)).timestamp()
                        else:
                            self._events.remove(event)
                if due:
                    self._save()
            for event in due:
                try:
                    if self.on_fire:
                        self.on_fire(event)
                except Exception:
                    log.exception("Ошибка обработки события %s", event.id)
