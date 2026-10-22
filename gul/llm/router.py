"""Роутер мозгов: пробуем бесплатные провайдеры по порядку.

Главная идея: если GigaChat лёг, кончились токены или пропал интернет —
ассистент не «умирает», а молча переключается на следующий бесплатный мозг.
Пользователь слышит ответ, а не извинения.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Iterator

from ..config import Config
from ..metrics import Metrics, metrics as global_metrics
from .base import ChatMessage, LLMError, LLMProvider
from .providers import GigaChatProvider, GroqProvider, MockProvider, OpenAICompatProvider

log = logging.getLogger(__name__)

COOLDOWN_SECONDS = 90


def build_router(cfg: Config, metrics: Metrics | None = None,
                 on_event: Callable[[str, dict], None] | None = None) -> "LLMRouter":
    return LLMRouter(cfg, metrics=metrics, on_event=on_event)


class LLMRouter:
    def __init__(self, cfg: Config, metrics: Metrics | None = None,
                 on_event: Callable[[str, dict], None] | None = None):
        self.cfg = cfg
        self.metrics = metrics or global_metrics
        self.on_event = on_event
        self._lock = threading.Lock()
        self._health: dict[str, dict] = {}
        self.providers: list[LLMProvider] = self._build_providers()
        self.last_provider: str | None = None

    # -- сборка ----------------------------------------------------------
    def _build_providers(self) -> list[LLMProvider]:
        brain = self.cfg.section("brain")
        factories: dict[str, Callable[[], LLMProvider]] = {}

        gc = brain.get("gigachat", {}) or {}
        if gc.get("enabled", True):
            factories["gigachat"] = lambda: GigaChatProvider(self.cfg)

        groq = brain.get("groq", {}) or {}
        if groq.get("enabled", True):
            # Режим (прямой / прокси / релей) выбирает сам провайдер.
            factories["groq"] = lambda: GroqProvider(self.cfg)

        cf = brain.get("cloudflare", {}) or {}
        if cf.get("enabled", True):
            account = str(cf.get("account_id") or "").strip()
            factories["cloudflare"] = lambda: OpenAICompatProvider(
                self.cfg, "cloudflare", "Cloudflare Workers AI",
                base_url=f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1",
                api_key=cf.get("api_key", ""),
                model=cf.get("model", "@cf/openai/gpt-oss-120b"),
            )

        generic = brain.get("generic", {}) or {}
        if generic.get("enabled", False):
            factories["generic"] = lambda: OpenAICompatProvider(
                self.cfg, "generic", "Свой OpenAI-совместимый сервер",
                base_url=generic.get("base_url", ""),
                api_key=generic.get("api_key", "none"),
                model=generic.get("model", "local-model"),
            )

        mock = brain.get("mock", {}) or {}
        if mock.get("enabled", True):
            factories["mock"] = lambda: MockProvider(self.cfg)

        order = brain.get("order") or ["gigachat", "groq", "cloudflare", "generic", "mock"]
        providers: list[LLMProvider] = []
        for name in order:
            factory = factories.get(name)
            if not factory:
                continue
            try:
                providers.append(factory())
            except Exception as exc:  # конфиг битый — не роняем ассистента
                log.warning("Провайдер %s не создан: %s", name, exc)
        return providers

    # -- здоровье --------------------------------------------------------
    def _state(self, provider: LLMProvider) -> dict:
        return self._health.setdefault(provider.name, {
            "state": "ok" if provider.available() else "no_key",
            "failures": 0,
            "calls": 0,
            "last_error": "",
            "last_latency_ms": None,
            "cooldown_until": 0.0,
        })

    def _mark_ok(self, provider: LLMProvider, latency_ms: int) -> None:
        with self._lock:
            st = self._state(provider)
            st.update(state="ok", failures=0, last_error="", last_latency_ms=latency_ms,
                      cooldown_until=0.0)
            st["calls"] += 1
        self.last_provider = provider.name
        self._emit("provider_ok", {"provider": provider.name, "latency_ms": latency_ms})

    @staticmethod
    def _cooldown_for(exc: Exception) -> float:
        """Сколько секунд не трогать провайдера после ошибки.

        Геоблок и неверный ключ сами не пройдут — нет смысла дёргать их часто.
        Лимит 429 отпускает за секунды-минуты, сеть — тоже.
        """
        status = getattr(exc, "status", None)
        text = str(exc).lower()
        if status in (401, 403):
            return max(COOLDOWN_SECONDS, 300.0)
        if status == 429:
            return 45.0
        if status == 404:
            return 300.0
        if "сеть недоступна" in text or "timeout" in text or "timed out" in text:
            return 30.0
        return float(COOLDOWN_SECONDS)

    def _mark_fail(self, provider: LLMProvider, exc: Exception) -> None:
        cooldown = self._cooldown_for(exc)
        with self._lock:
            st = self._state(provider)
            st["failures"] += 1
            st["last_error"] = str(exc)[:300]
            st["state"] = "error"
            st["cooldown_until"] = time.time() + cooldown
        self.metrics.bump("fallbacks")
        log.warning("Мозг %s недоступен: %s (пауза %.0f с)", provider.name, exc, cooldown)
        self._emit("provider_error", {"provider": provider.name, "error": str(exc)[:300]})

    def _emit(self, event: str, data: dict) -> None:
        if self.on_event:
            try:
                self.on_event(event, data)
            except Exception:
                pass

    def _usable(self, provider: LLMProvider, now: float, ignore_cooldown: bool) -> bool:
        if not provider.available():
            return False
        st = self._health.get(provider.name) or {}
        if not ignore_cooldown and st.get("cooldown_until", 0) > now:
            return False
        return True

    # -- генерация -------------------------------------------------------
    def stream(self, system: str, messages: list[ChatMessage],
               **options: Any) -> Iterator[str]:
        now = time.time()
        ordered = list(self.providers)
        usable = [p for p in ordered if self._usable(p, now, ignore_cooldown=False)]
        if not usable:
            # Все в кулдауне — пробуем лучший доступный, вдруг уже ожил.
            usable = [p for p in ordered if p.available()]
        if not usable:
            raise LLMError(
                "Нет доступных нейросетей. Добавьте ключ GigaChat в .env или включите демо-режим."
            )

        errors: list[str] = []
        for index, provider in enumerate(usable):
            started = time.perf_counter()
            iterator = provider.stream(system, messages, **options)
            try:
                first = next(iterator)
            except StopIteration:
                self._mark_fail(provider, LLMError("пустой ответ"))
                errors.append(f"{provider.title}: пустой ответ")
                continue
            except Exception as exc:
                self._mark_fail(provider, exc)
                errors.append(f"{provider.title}: {exc}")
                continue

            first_latency = int((time.perf_counter() - started) * 1000)
            self._mark_ok(provider, first_latency)
            self.metrics.bump("llm")
            self.metrics.record("llm_first_token", provider=provider.name,
                                latency_ms=first_latency)
            try:
                yield first
                for chunk in iterator:
                    yield chunk
            except Exception as exc:
                # Ошибка посреди ответа: не начинаем заново, просто завершаем фразу.
                log.warning("Поток %s оборвался: %s", provider.name, exc)
                self._emit("stream_broken", {"provider": provider.name, "error": str(exc)})
            return

        raise LLMError("; ".join(errors) or "Все нейросети недоступны")

    def complete(self, system: str, messages: list[ChatMessage], **options: Any) -> str:
        return "".join(self.stream(system, messages, **options))

    # -- статус для UI ---------------------------------------------------
    def status(self) -> list[dict]:
        now = time.time()
        result = []
        with self._lock:
            for provider in self.providers:
                st = dict(self._state(provider))
                cooldown = max(0, int(st.get("cooldown_until", 0) - now))
                extra = {}
                info = getattr(provider, "info", None)
                if callable(info):
                    try:
                        extra = info() or {}
                    except Exception:
                        extra = {}
                result.append({
                    "name": provider.name,
                    "title": provider.title,
                    "available": provider.available(),
                    **extra,
                    "state": st.get("state"),
                    "calls": st.get("calls", 0),
                    "last_error": st.get("last_error", ""),
                    "last_latency_ms": st.get("last_latency_ms"),
                    "cooldown_seconds": cooldown,
                    "active": provider.name == self.last_provider,
                })
        return result
