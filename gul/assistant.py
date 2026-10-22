"""Оркестратор Гуля: скиллы → мозг → голос, с историей и метриками."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from . import __version__
from .audio import AudioPlayer
from .config import Config
from .llm import ChatMessage, LLMError, build_router
from .metrics import Metrics, metrics as global_metrics
from .skills import SkillContext, build_registry
from .skills.notes_memory import MemoryStore, NotesStore
from .skills.timers import TimerManager
from .skills.timers_skill import format_timer_fired
from .tts import Speaker
from .utils import (
    cpu_info, date_to_speech, duration_to_speech, human_uptime, mem_info,
    normalize_text, strip_wake_words, time_to_speech, truncate,
)

log = logging.getLogger(__name__)


@dataclass
class Answer:
    text: str
    source: str = "llm"
    provider: str | None = None
    skill: str | None = None
    total_ms: int = 0
    first_token_ms: int | None = None
    first_audio_ms: int | None = None
    error: str | None = None
    data: dict = field(default_factory=dict)


class Assistant:
    def __init__(self, cfg: Config, metrics: Metrics | None = None,
                 on_event: Callable[[str, dict], None] | None = None,
                 enable_audio: bool = True):
        self.cfg = cfg
        self.metrics = metrics or global_metrics
        self.on_event = on_event
        self.started_at = time.time()
        self.name = cfg.get("assistant.name", "Гуль")
        self.city = cfg.get("skills.weather.city", "Москва")
        self.wake_words = cfg.get("assistant.wake_words") or ["привет гуль"]
        self.max_chars = int(cfg.get("assistant.tts_max_chars", 900))

        self.registry = build_registry(cfg)
        self.router = build_router(cfg, self.metrics, on_event=self._on_router_event)
        self.notes = NotesStore(cfg.path(cfg.get("skills.notes_file", "data/notes.json")))
        self.memory = MemoryStore(cfg.path(cfg.get("skills.memory_file", "data/memory.json")))
        self.timers = TimerManager(cfg, on_fire=self._on_timer)
        self.player = AudioPlayer(cfg.get("audio.output_device", "default")) if enable_audio else None
        self.speaker = Speaker(cfg, self.player, self.metrics, on_event=on_event)

        music_skill = self.registry.by_name("music")
        self.ctx = SkillContext(
            cfg=cfg, llm=self.router, timers=self.timers, notes=self.notes,
            memory=self.memory, music=music_skill, speaker=self.speaker,
        )
        self.history: list[ChatMessage] = []
        self._history_lock = threading.Lock()
        self._speak_lock = threading.Lock()
        self._warming = threading.Thread(target=self._prewarm, name="prewarm", daemon=True)
        self._warming.start()

    # -- события ---------------------------------------------------------
    def _emit(self, event: str, data: dict) -> None:
        if self.on_event:
            try:
                self.on_event(event, data)
            except Exception:
                log.debug("Ошибка обработчика событий", exc_info=True)

    def _on_router_event(self, event: str, data: dict) -> None:
        self._emit(event, data)

    def _prewarm(self) -> None:
        """Готовим погоду заранее: к первому вопросу ответ будет мгновенным."""
        skill = self.registry.by_name("weather")
        try:
            skill.service(self.ctx).prewarm()
        except Exception:
            pass

    # -- системный промпт -------------------------------------------------
    def system_prompt(self) -> str:
        now = datetime.now()
        facts = self.memory.all()
        memory_block = ""
        if facts:
            memory_block = ("\nЧто ты знаешь о пользователе (используй естественно, "
                            "без перечисления): " + "; ".join(facts[-15:]) + ".")
        return (
            f"{self.cfg.get('assistant.persona', '')}\n"
            f"Тебя зовут {self.name}. Сейчас {date_to_speech(now)}, "
            f"{time_to_speech(now)}. Город пользователя: {self.city}.\n"
            f"Ты работаешь голосом: отвечай без markdown, списков и таблиц, "
            f"обычно не длиннее трёх предложений. Если просят подробнее — можно больше. "
            f"Не говори, что ты языковая модель; ты — голосовой ассистент Гуль."
            f"{memory_block}"
        )

    # -- работа со скиллами ----------------------------------------------
    def _dispatch(self, normalized: str):
        if not normalized:
            return None
        return self.registry.dispatch(normalized, self.ctx)

    def _remember(self, user_text: str, answer_text: str) -> None:
        with self._history_lock:
            self.history.append(ChatMessage("user", user_text))
            self.history.append(ChatMessage("assistant", answer_text))
            limit = int(self.cfg.get("assistant.max_history_turns", 8)) * 2
            if len(self.history) > limit:
                self.history = self.history[-limit:]

    def clear_history(self) -> None:
        with self._history_lock:
            self.history = []

    # -- основной текстовый поток ----------------------------------------
    def respond_stream(self, text: str, source: str = "web",
                       speak: bool | None = None) -> Iterator[dict]:
        """Поток событий для веб-интерфейса: skill/delta/done/error."""
        started = time.perf_counter()
        raw = (text or "").strip()
        normalized = normalize_text(strip_wake_words(raw, self.wake_words))
        if not normalized:
            yield {"type": "done", "text": "Я вас слушаю.", "source": "system", "total_ms": 0}
            return

        self.metrics.bump("questions")
        result = self._dispatch(normalized)
        if result is not None:
            self.metrics.bump("skills")
            if result.stop_audio:
                self.speaker.stop()
            if result.needs_llm:
                yield from self._stream_llm(result.needs_llm, raw, started, source,
                                            speak=speak, skill=result.data.get("skill"))
                return
            reply = result.text
            self._remember(raw, reply)
            elapsed = int((time.perf_counter() - started) * 1000)
            self.metrics.record("answer", source="skill", skill=result.data.get("skill"),
                                total_ms=elapsed, chars=len(reply))
            self._emit("answer", {"source": "skill", "skill": result.data.get("skill"),
                                   "characters": len(reply), "total_ms": elapsed})
            yield {
                "type": "skill", "skill": result.data.get("skill"),
                "text": reply, "speak": result.spoken(self.max_chars),
                "total_ms": elapsed,
            }
            if speak and self.speaker.any_available():
                self.speaker.say(result.spoken(self.max_chars))
            yield {"type": "done", "text": reply, "source": "skill",
                   "skill": result.data.get("skill"), "total_ms": elapsed}
            return

        yield from self._stream_llm(raw, raw, started, source, speak=speak, skill=None)

    def _stream_llm(self, prompt: str, raw_user: str, started: float, source: str,
                    speak: bool | None, skill: str | None) -> Iterator[dict]:
        system = self.system_prompt()
        with self._history_lock:
            messages = list(self.history)
        messages.append(ChatMessage("user", prompt))
        yield {"type": "state", "state": "thinking"}
        first_token_ms: int | None = None
        chunks: list[str] = []
        error: str | None = None
        try:
            for token in self.router.stream(system, messages):
                if first_token_ms is None:
                    first_token_ms = int((time.perf_counter() - started) * 1000)
                chunks.append(token)
                yield {"type": "delta", "text": token}
        except LLMError as exc:
            error = str(exc)
            log.warning("LLM недоступен: %s", exc)
        except Exception as exc:
            error = f"{exc.__class__.__name__}: {exc}"
            log.exception("Неожиданная ошибка LLM")

        reply = "".join(chunks).strip()
        if not reply:
            reply = self._fallback_reply(error)
            yield {"type": "delta", "text": reply}
        elapsed = int((time.perf_counter() - started) * 1000)
        provider = self.router.last_provider
        self._remember(raw_user, reply)
        self.metrics.counters["errors"] += 1 if error else 0
        self.metrics.record("answer", source="llm", provider=provider,
                            total_ms=elapsed, chars=len(reply),
                            first_token_ms=first_token_ms, error=error)
        self._emit("answer", {"source": "llm", "provider": provider,
                               "characters": len(reply), "total_ms": elapsed,
                               "first_token_ms": first_token_ms})
        if speak and self.speaker.any_available():
            self.speaker.say(reply)
        yield {
            "type": "done", "text": reply, "source": "llm", "provider": provider,
            "skill": skill, "total_ms": elapsed, "first_token_ms": first_token_ms,
            "error": error,
        }

    def _fallback_reply(self, error: str | None) -> str:
        hint = ""
        if error:
            if "403" in error or "Forbidden" in error:
                hint = (" Похоже, сервис недоступен из вашего региона. "
                        "Проверьте VPN в настройках прокси.")
            elif "авторизац" in error or "401" in error:
                hint = " Проверьте ключи GigaChat в файле .env."
            elif "сеть" in error.lower() or "Network" in error:
                hint = " Проверьте интернет."
        return ("Извините, не могу получить ответ от нейросети." + hint +
                " Бытовые команды — время, таймеры, погода, заметки — работают и без неё.")

    # -- голосовой ответ --------------------------------------------------
    def answer_voice(self, text: str) -> Answer:
        started = time.perf_counter()
        raw = (text or "").strip()
        normalized = normalize_text(strip_wake_words(raw, self.wake_words))
        if not normalized:
            reply = "Я вас слушаю."
            self._speak(reply)
            return Answer(text=reply, source="system", total_ms=int((time.perf_counter() - started) * 1000))

        self.metrics.bump("questions")
        result = self._dispatch(normalized)
        if result is not None and not result.needs_llm:
            self.metrics.bump("skills")
            reply = result.text
            spoken = result.spoken(self.max_chars)
            if result.stop_audio:
                self.speaker.stop()
            first_audio = self._speak(spoken)
            elapsed = int((time.perf_counter() - started) * 1000)
            self._remember(raw, reply)
            self.metrics.record("answer", source="skill", skill=result.data.get("skill"),
                                total_ms=elapsed if first_audio is None else first_audio,
                                chars=len(reply))
            self._emit("answer", {"source": "skill", "skill": result.data.get("skill"),
                                   "characters": len(reply), "total_ms": elapsed})
            return Answer(text=reply, source="skill", skill=result.data.get("skill"),
                          total_ms=elapsed, first_audio_ms=first_audio)

        prompt = result.needs_llm if result is not None else raw
        system = self.system_prompt()
        with self._history_lock:
            messages = list(self.history)
        messages.append(ChatMessage("user", prompt))
        first_audio: int | None = None
        first_token: int | None = None
        error: str | None = None

        def on_first_audio() -> None:
            nonlocal first_audio
            if first_audio is None:
                first_audio = int((time.perf_counter() - started) * 1000)
                self._emit("state", {"state": "speaking"})

        try:
            tokens = self.router.stream(system, messages)
            # Считаем первый токен, не съедая поток: оборачиваем генератор.
            def monitored() -> Iterator[str]:
                nonlocal first_token
                for token in tokens:
                    if first_token is None:
                        first_token = int((time.perf_counter() - started) * 1000)
                    yield token

            reply = self.speaker.say_stream(monitored(), on_first_audio=on_first_audio)
        except LLMError as exc:
            error = str(exc)
            reply = self._fallback_reply(error)
            self._speak(reply)
        except Exception as exc:
            error = f"{exc.__class__.__name__}: {exc}"
            log.exception("Ошибка голосового ответа")
            reply = self._fallback_reply(error)
            self._speak(reply)

        reply = (reply or "").strip()
        if not reply:
            reply = self._fallback_reply(error)
        elapsed = int((time.perf_counter() - started) * 1000)
        provider = self.router.last_provider
        self._remember(raw, reply)
        if error:
            self.metrics.counters["errors"] += 1
        self.metrics.record("answer", source="llm", provider=provider, total_ms=elapsed,
                            first_token_ms=first_token, first_audio_ms=first_audio,
                            chars=len(reply), error=error)
        self._emit("answer", {"source": "llm", "provider": provider,
                               "characters": len(reply), "total_ms": elapsed,
                               "first_audio_ms": first_audio})
        return Answer(text=reply, source="llm", provider=provider, total_ms=elapsed,
                      first_token_ms=first_token, first_audio_ms=first_audio, error=error)

    def _speak(self, text: str) -> int | None:
        if not text:
            return None
        started = time.perf_counter()
        self._emit("state", {"state": "speaking"})
        ok = False
        with self._speak_lock:
            try:
                ok = self.speaker.say(text)
            except Exception:
                log.exception("Озвучка не удалась")
        self._emit("state", {"state": "idle"})
        return int((time.perf_counter() - started) * 1000) if ok else None

    def stop_speaking(self) -> None:
        self.speaker.stop()
        music = self.registry.by_name("music")
        if music is not None and getattr(music, "player", None) is not None:
            try:
                music.player.stop()
            except Exception:
                pass

    # -- таймеры ----------------------------------------------------------
    def _on_timer(self, event) -> None:
        message = format_timer_fired(event)
        log.info("Сработало событие: %s", message)
        self._emit("timer_fired", {"kind": event.kind, "text": event.text,
                                    "message": message})
        try:
            if self.cfg.get("audio.beep", True) and self.speaker is not None:
                self.speaker.player.beep()
            self._speak(message)
        except Exception:
            log.exception("Не удалось озвучить таймер")
        self.metrics.record("timer_fired", kind=event.kind)

    # -- статус -----------------------------------------------------------
    def status(self) -> dict:
        cpu = cpu_info()
        mem = mem_info()
        timers = self.timers.list()
        return {
            "name": self.name,
            "version": __version__,
            "uptime": human_uptime(time.time() - self.started_at),
            "uptime_seconds": round(time.time() - self.started_at),
            "city": self.city,
            "wake_words": self.wake_words,
            "cpu": {"model": cpu["model"], "avx": cpu["avx"], "sse4": cpu["sse4"]},
            "memory": mem,
            "providers": self.router.status(),
            "tts": self.speaker.available_engines(),
            "audio": {
                "player": self.player.available() if self.player else False,
                "speaking": self.speaker.is_speaking(),
            },
            "skills": self.registry.public(),
            "timers": {
                "total": len(timers),
                "next": (min(timers, key=lambda e: e.fire_at).fire_datetime.isoformat()
                         if timers else None),
            },
            "metrics": self.metrics.snapshot(),
            "history_turns": len(self.history) // 2,
            "warnings": self.cfg.warnings(),
        }

    def close(self) -> None:
        try:
            self.timers.stop()
        except Exception:
            pass
        try:
            self.speaker.stop()
        except Exception:
            pass
