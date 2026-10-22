"""Метрики задержки: главный KPI проекта — ответ не дольше 5 секунд."""

from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Any


class Metrics:
    def __init__(self, max_events: int = 200):
        self._lock = threading.Lock()
        self._events: deque[dict] = deque(maxlen=max_events)
        self._started = time.time()
        self.counters: dict[str, int] = {
            "questions": 0,
            "skills": 0,
            "llm": 0,
            "errors": 0,
            "fallbacks": 0,
        }

    @contextmanager
    def timer(self, **fields: Any):
        """with metrics.timer(stage='stt'): ... → добавляет latency_ms."""
        started = time.perf_counter()
        box: dict[str, Any] = {}
        try:
            yield box
        finally:
            box["latency_ms"] = round((time.perf_counter() - started) * 1000)
            self.record(**fields, **box)

    def record(self, event: str = "event", **fields: Any) -> dict:
        entry = {"event": event, "ts": time.time(), **fields}
        with self._lock:
            self._events.append(entry)
        return entry

    def bump(self, name: str, delta: int = 1) -> None:
        with self._lock:
            self.counters[name] = self.counters.get(name, 0) + delta

    def last(self, n: int = 20) -> list[dict]:
        with self._lock:
            return list(self._events)[-n:]

    def latency_summary(self) -> dict:
        with self._lock:
            answers = [
                e for e in self._events
                if e.get("event") == "answer" and isinstance(e.get("total_ms"), (int, float))
            ]
        if not answers:
            return {"count": 0, "avg_ms": None, "p50_ms": None, "p95_ms": None, "max_ms": None}
        values = sorted(a["total_ms"] for a in answers)
        n = len(values)

        def pct(p: float) -> int:
            return int(values[min(n - 1, max(0, int(round(p * (n - 1))))) ])

        return {
            "count": n,
            "avg_ms": int(sum(values) / n),
            "p50_ms": pct(0.5),
            "p95_ms": pct(0.95),
            "max_ms": int(values[-1]),
            "under_5s_percent": round(100 * sum(1 for v in values if v <= 5000) / n),
        }

    def snapshot(self) -> dict:
        with self._lock:
            counters = dict(self.counters)
        return {
            "uptime_seconds": round(time.time() - self._started),
            "counters": counters,
            "latency": self.latency_summary(),
        }


metrics = Metrics()
