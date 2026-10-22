"""Распознавание речи: Vosk (офлайн) + wake-word + облачный резерв Whisper."""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from .audio import AudioError
from .config import Config

log = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    return re.sub(r"[^а-яa-z0-9 ]+", " ", (text or "").lower().replace("ё", "е")).strip()


class VoskModel:
    """Одна модель Vosk на весь процесс: экономит ~200 МБ RAM."""

    _cache: dict[str, Any] = {}

    @classmethod
    def get(cls, path: Path):
        key = str(path)
        if key in cls._cache:
            return cls._cache[key]
        try:
            import vosk
        except ImportError as exc:
            raise AudioError("Модуль vosk не установлен: pip install vosk") from exc
        if not path.exists():
            raise AudioError(
                f"Модель Vosk не найдена: {path}. Запустите scripts/download_models.sh "
                "или скачайте vosk-model-small-ru-0.22 с alphacephei.com/vosk/models"
            )
        vosk.SetLogLevel(-1)
        started = time.perf_counter()
        model = vosk.Model(str(path))
        log.info("Модель Vosk загружена за %.2f с", time.perf_counter() - started)
        cls._cache[key] = model
        return model


class WakeWordDetector:
    """Слушает микрофон и ждёт «Привет, Гуль».

    Использует грамматику Vosk — распознаются только слова активации,
    поэтому CPU почти не тратится и ложных срабатываний мало.
    """

    def __init__(self, model, wake_words: list[str], sample_rate: int = 16000,
                 mode: str = "auto"):
        self.wake_words = [w.lower().replace("ё", "е") for w in wake_words if w.strip()]
        self._grammar_mode = mode != "full"
        if self._grammar_mode and mode == "auto":
            # В словаре маленькой модели может не быть имени «Гуль».
            # Тогда грамматика бесполезна (она пропускает OOV-слова), и мы
            # переключаемся на полное распознавание — чуть больше CPU, зато надёжно.
            try:
                finder = getattr(model, "vosk_model_find_word", None)
                if finder:
                    missing = []
                    for phrase in self.wake_words:
                        for word in phrase.split():
                            if finder(word) < 0:
                                missing.append(word)
                    if missing:
                        self._grammar_mode = False
                        log.warning(
                            "Слов нет в словаре Vosk: %s — включён полный режим wake-word. "
                            "Можно сменить слово активации в config.yaml (например, «привет гуля»).",
                            ", ".join(sorted(set(missing))),
                        )
            except Exception as exc:
                log.debug("Не удалось проверить словарь Vosk: %s", exc)

        grammar_words: list[str] = list(self.wake_words)
        for word in ("гуль", "гуля", "гулю", "привет", "слушай", "эй", "окей", "стоп"):
            if word not in grammar_words:
                grammar_words.append(word)
        grammar = json.dumps(grammar_words + ["[unk]"], ensure_ascii=False)
        try:
            import vosk

            if self._grammar_mode:
                self._rec = vosk.KaldiRecognizer(model, sample_rate, grammar)
            else:
                self._rec = vosk.KaldiRecognizer(model, sample_rate)
        except Exception:
            import vosk

            self._grammar_mode = False
            self._rec = vosk.KaldiRecognizer(model, sample_rate)
        self._rec.SetWords(False)

    def _matches(self, text: str) -> bool:
        normalized = _normalize(text)
        if not normalized:
            return False
        if any(phrase in normalized for phrase in self.wake_words):
            return True
        # Мягкий вариант: «привет гуль» могло распознаться как «привет куль».
        words = normalized.split()
        has_name = any(w in ("гуль", "гуля", "гулю", "гуле", "куль", "гул") for w in words)
        has_call = any(w in ("привет", "слушай", "эй", "окей", "алло") for w in words)
        return has_name and has_call

    def feed(self, chunk: bytes) -> bool:
        if not chunk:
            return False
        if self._rec.AcceptWaveform(chunk):
            try:
                text = json.loads(self._rec.Result()).get("text", "")
            except Exception:
                text = ""
            self._rec.Reset()
            return self._matches(text)
        try:
            partial = json.loads(self._rec.PartialResult()).get("partial", "")
        except Exception:
            partial = ""
        if self._matches(partial):
            self._rec.Reset()
            return True
        return False

    def reset(self) -> None:
        self._rec.Reset()


class CommandSTT:
    """Распознавание самой команды после слова активации."""

    def __init__(self, model, sample_rate: int = 16000):
        import vosk

        self._rec = vosk.KaldiRecognizer(model, sample_rate)
        self._rec.SetWords(True)
        self._last_partial = ""
        self._finalized = False

    def feed(self, chunk: bytes) -> str:
        if not chunk:
            return self._last_partial
        if self._rec.AcceptWaveform(chunk):
            try:
                self._last_partial = json.loads(self._rec.Result()).get("text", "")
            except Exception:
                self._last_partial = ""
        else:
            try:
                self._last_partial = json.loads(self._rec.PartialResult()).get("partial", "")
            except Exception:
                pass
        return self._last_partial

    @property
    def partial(self) -> str:
        return self._last_partial

    def final(self) -> str:
        if self._finalized:
            return self._last_partial
        self._finalized = True
        try:
            text = json.loads(self._rec.FinalResult()).get("text", "")
        except Exception:
            text = ""
        self._last_partial = text or self._last_partial
        return self._last_partial

    def reset(self) -> None:
        self._rec.Reset()
        self._last_partial = ""
        self._finalized = False


class CloudSTT:
    """Резервное распознавание через Groq Whisper (нужен ключ и VPN)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.api_key = str(cfg.get("brain.groq.api_key", "") or "")
        self.model = cfg.get("stt.cloud_model", "whisper-large-v3-turbo")
        self.base_url = str(cfg.get("brain.groq.base_url", "https://api.groq.com/openai/v1")).rstrip("/")
        self.proxy = cfg.get("network.proxy", "") or ""

    def available(self) -> bool:
        return bool(self.api_key)

    def transcribe(self, wav_path: Path, language: str = "ru") -> str:
        if not self.available():
            return ""
        import requests

        try:
            with open(wav_path, "rb") as fh:
                response = requests.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    files={"file": (wav_path.name, fh, "audio/wav")},
                    data={"model": self.model, "language": language, "response_format": "json"},
                    timeout=25,
                    proxies={"http": self.proxy, "https": self.proxy} if self.proxy else None,
                )
            response.raise_for_status()
            return (response.json().get("text") or "").strip()
        except Exception as exc:
            log.warning("Облачное распознавание не сработало: %s", exc)
            return ""


class SpeechEngine:
    """Ленивая загрузка Vosk и фабрика распознавателей."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sample_rate = int(cfg.get("audio.sample_rate", 16000))
        self.model_path = cfg.path(cfg.get("stt.model_path", "models/vosk-model-small-ru-0.22"))
        self.wake_words = cfg.get("assistant.wake_words") or ["привет гуль"]
        self.cloud = CloudSTT(cfg)
        self._model = None

    def load(self):
        if self._model is None:
            self._model = VoskModel.get(self.model_path)
        return self._model

    def wake_detector(self) -> WakeWordDetector:
        mode = str(self.cfg.get("stt.wake_mode", "auto") or "auto")
        return WakeWordDetector(self.load(), self.wake_words, self.sample_rate, mode=mode)

    def command_recognizer(self) -> CommandSTT:
        return CommandSTT(self.load(), self.sample_rate)

    def stop_detector(self) -> "StopWordDetector":
        return StopWordDetector(self.load(), self.sample_rate)


class StopWordDetector:
    """Слушает только слова остановки во время ответа (barge-in).

    Отдельная грамматика вместо полного распознавания: дешевле для CPU
    и меньше ложных срабатываний от собственной колонки.
    """

    _WORDS = ["стоп", "тише", "хватит", "замолчи", "отмена", "гуль стоп"]

    def __init__(self, model, sample_rate: int = 16000):
        import vosk
        import json as _json

        grammar = _json.dumps(self._WORDS + ["[unk]"], ensure_ascii=False)
        try:
            self._rec = vosk.KaldiRecognizer(model, sample_rate, grammar)
        except Exception:
            self._rec = vosk.KaldiRecognizer(model, sample_rate)
        self._rec.SetWords(False)

    def feed(self, chunk: bytes) -> bool:
        if not chunk:
            return False
        if self._rec.AcceptWaveform(chunk):
            try:
                text = json.loads(self._rec.Result()).get("text", "")
            except Exception:
                text = ""
            self._rec.Reset()
            return self._matches(text)
        try:
            partial = json.loads(self._rec.PartialResult()).get("partial", "")
        except Exception:
            partial = ""
        if self._matches(partial):
            self._rec.Reset()
            return True
        return False

    @staticmethod
    def _matches(text: str) -> bool:
        words = _normalize(text).split()
        return any(w in ("стоп", "тише", "хватит", "замолчи", "отмена") for w in words)
