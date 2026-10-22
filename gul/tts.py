"""Синтез речи: Edge TTS → Piper → RHVoice → espeak, с автоматическим фолбэком.

Почему так:
• Edge TTS — самый красивый русский голос, бесплатный, но нужен интернет.
• Piper — локальный нейроголос, быстрый даже на Atom, но модель надо скачать.
• RHVoice — локальный, очень быстрый и лёгкий, голос чуть роботизированный.
• espeak-ng — совсем простой резерв, зато есть всегда после apt install.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Iterable, Iterator

from .audio import AudioPlayer
from .config import Config
from .metrics import Metrics, metrics as global_metrics
from .utils import SentenceBuffer, clean_for_speech

log = logging.getLogger(__name__)


class TTSError(RuntimeError):
    pass


class TTSEngine:
    name = "base"
    title = "TTS"
    needs_network = False

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.speed = float(cfg.get("tts.speed", 1.0) or 1.0)
        self._tmpdir = Path(tempfile.gettempdir()) / "gul-tts"
        self._tmpdir.mkdir(parents=True, exist_ok=True)

    def available(self) -> bool:
        raise NotImplementedError

    def synthesize(self, text: str) -> Path:
        raise NotImplementedError

    def _temp_path(self, suffix: str) -> Path:
        fd, path = tempfile.mkstemp(dir=str(self._tmpdir), suffix=suffix)
        os.close(fd)
        return Path(path)


class EdgeTTSEngine(TTSEngine):
    name = "edge"
    title = "Edge TTS (нейроголос)"
    needs_network = True

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.voice = cfg.get("tts.edge_voice", "ru-RU-DmitryNeural")
        self.proxy = cfg.get("network.proxy", "") or ""

    def available(self) -> bool:
        try:
            import edge_tts  # noqa: F401
            return True
        except Exception:
            return False

    def synthesize(self, text: str) -> Path:
        import edge_tts

        path = self._temp_path(".mp3")
        rate = f"{int(round((self.speed - 1) * 100)):+d}%"
        proxy = self.proxy or None

        async def run() -> None:
            communicate = edge_tts.Communicate(text, self.voice, rate=rate, proxy=proxy)
            await asyncio.wait_for(communicate.save(str(path)), timeout=20)

        try:
            asyncio.run(run())
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise TTSError(f"Edge TTS: {exc.__class__.__name__}") from exc
        if not path.exists() or path.stat().st_size < 500:
            raise TTSError("Edge TTS: пустой аудиофайл")
        return path


class PiperTTSEngine(TTSEngine):
    name = "piper"
    title = "Piper (локальный нейроголос)"

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.binary = cfg.get("tts.piper_binary", "piper")
        self.model = cfg.path(cfg.get("tts.piper_model", "models/piper/ru_RU-dmitry-medium.onnx"))

    def available(self) -> bool:
        return shutil.which(self.binary) is not None and self.model.exists()

    def synthesize(self, text: str) -> Path:
        path = self._temp_path(".wav")
        length_scale = max(0.5, min(2.0, 1.0 / max(0.5, self.speed)))
        cmd = [shutil.which(self.binary) or self.binary, "--model", str(self.model),
               "--output_file", str(path), "--length_scale", f"{length_scale:.2f}"]
        try:
            subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, timeout=30, check=True)
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise TTSError(f"Piper: {exc}") from exc
        if not path.exists() or path.stat().st_size < 500:
            raise TTSError("Piper: пустой аудиофайл")
        return path


class RHVoiceEngine(TTSEngine):
    name = "rhvoice"
    title = "RHVoice (локальный, быстрый)"

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.binary = shutil.which("RHVoice-test") or shutil.which("rhvoice-test")
        self.voice = cfg.get("tts.rhvoice_voice", "Aleksandr")

    def available(self) -> bool:
        return bool(self.binary)

    def synthesize(self, text: str) -> Path:
        path = self._temp_path(".wav")
        cmd = [self.binary, "-p", self.voice, "-o", str(path)]
        try:
            subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, timeout=30, check=True)
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise TTSError(f"RHVoice: {exc}") from exc
        if not path.exists() or path.stat().st_size < 500:
            raise TTSError("RHVoice: пустой аудиофайл")
        return path


class EspeakEngine(TTSEngine):
    name = "espeak"
    title = "espeak-ng (резерв)"

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.binary = shutil.which("espeak-ng") or shutil.which("espeak")
        self.voice = cfg.get("tts.espeak_voice", "ru")

    def available(self) -> bool:
        return bool(self.binary)

    def synthesize(self, text: str) -> Path:
        path = self._temp_path(".wav")
        speed = int(160 * max(0.6, min(1.6, self.speed)))
        cmd = [self.binary, "-v", self.voice, "-s", str(speed), "-w", str(path)]
        try:
            subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, timeout=30, check=True)
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise TTSError(f"espeak: {exc}") from exc
        if not path.exists() or path.stat().st_size < 500:
            raise TTSError("espeak: пустой аудиофайл")
        return path


ENGINES = {
    "edge": EdgeTTSEngine,
    "piper": PiperTTSEngine,
    "rhvoice": RHVoiceEngine,
    "espeak": EspeakEngine,
}


class Speaker:
    """Озвучка с потоковой генерацией: начинаем говорить, пока LLM ещё думает."""

    def __init__(self, cfg: Config, player: AudioPlayer | None = None,
                 metrics: Metrics | None = None,
                 on_event: Callable[[str, dict], None] | None = None):
        self.cfg = cfg
        self.metrics = metrics or global_metrics
        self.on_event = on_event
        self.player = player or AudioPlayer(cfg.get("audio.output_device", "default"))
        self.max_chars = int(cfg.get("assistant.tts_max_chars", 900))
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._speaking = False
        order = cfg.get("tts.engines") or ["edge", "piper", "rhvoice", "espeak"]
        self.engines: list[TTSEngine] = []
        for name in order:
            cls = ENGINES.get(name)
            if cls:
                self.engines.append(cls(cfg))
        self._disabled: dict[str, float] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts")
        self._last_files: list[Path] = []
        # Какой движок реально озвучил последнюю фразу — для --speaker-test и логов.
        self.last_engine: str = ""

    # -- состояние -------------------------------------------------------
    def available_engines(self) -> list[dict]:
        result = []
        for engine in self.engines:
            result.append({
                "name": engine.name,
                "title": engine.title,
                "available": engine.available(),
                "network": engine.needs_network,
                "disabled_for": max(0, int(self._disabled.get(engine.name, 0) - time.time())),
            })
        return result

    def any_available(self) -> bool:
        return any(e.available() for e in self.engines)

    def is_speaking(self) -> bool:
        return self._speaking

    def stop(self) -> None:
        self._stop.set()
        self.player.stop()

    # -- синтез ----------------------------------------------------------
    def _pick_engine(self) -> TTSEngine | None:
        now = time.time()
        for engine in self.engines:
            if not engine.available():
                continue
            if self._disabled.get(engine.name, 0) > now:
                continue
            return engine
        # Все «приболели» — пробуем первый доступный, чтобы не молчать.
        return next((e for e in self.engines if e.available()), None)

    def _synthesize(self, text: str) -> tuple[Path | None, str]:
        text = clean_for_speech(text, self.max_chars)
        if not text:
            return None, ""
        engines_tried: list[str] = []
        for _ in range(len(self.engines)):
            engine = self._pick_engine()
            if engine is None or engine.name in engines_tried:
                break
            engines_tried.append(engine.name)
            started = time.perf_counter()
            try:
                path = engine.synthesize(text)
                latency = int((time.perf_counter() - started) * 1000)
                self.metrics.record("tts", engine=engine.name, latency_ms=latency,
                                    chars=len(text))
                self.last_engine = engine.name
                self._cleanup()
                return path, engine.name
            except Exception as exc:
                log.warning("TTS %s не сработал: %s", engine.name, exc)
                self._disabled[engine.name] = time.time() + 120
                self._emit("tts_error", {"engine": engine.name, "error": str(exc)[:200]})
        return None, ""

    def _cleanup(self) -> None:
        try:
            files = sorted(self._tmpdir().glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
            for path in files[40:]:
                path.unlink(missing_ok=True)
        except Exception:
            pass

    def _tmpdir(self) -> Path:
        if self.engines:
            return self.engines[0]._tmpdir
        return Path(tempfile.gettempdir())

    def _emit(self, event: str, data: dict) -> None:
        if self.on_event:
            try:
                self.on_event(event, data)
            except Exception:
                pass

    def _play(self, path: Path) -> None:
        if self._stop.is_set():
            return
        self.player.play_file(path, blocking=True)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    # -- публичное API ---------------------------------------------------
    def say(self, text: str, blocking: bool = True) -> bool:
        """Озвучить готовый текст. Возвращает False, если синтез не удался."""
        text = clean_for_speech(text, self.max_chars)
        if not text:
            return False
        self._stop.clear()
        with self._lock:
            self._speaking = True
            try:
                path, engine = self._synthesize(text)
                if not path:
                    self._emit("tts_unavailable", {"text": text[:100]})
                    return False
                self.metrics.record("tts_first_audio", engine=engine, chars=len(text))
                self._play(path)
                return True
            finally:
                self._speaking = False

    def say_stream(self, tokens: Iterable[str], on_first_audio: Callable[[], None] | None = None,
                   collect: bool = True) -> str:
        """Озвучивает поток токенов LLM по предложениям, не дожидаясь конца."""
        self._stop.clear()
        buffer = SentenceBuffer(max_chars=220)
        full: list[str] = []
        sentences: Iterator[str] = self._sentences(tokens, buffer, full)
        first_audio = False
        with self._lock:
            self._speaking = True
            try:
                try:
                    sentence = next(sentences)
                except StopIteration:
                    return "".join(full)
                future = self._executor.submit(self._synthesize, sentence)
                while future is not None:
                    path, engine = future.result()
                    try:
                        next_sentence = next(sentences)
                    except StopIteration:
                        next_sentence = None
                    next_future = self._executor.submit(self._synthesize, next_sentence) if next_sentence else None
                    if path:
                        if not first_audio:
                            first_audio = True
                            if on_first_audio:
                                on_first_audio()
                        self.metrics.record("tts_first_audio", engine=engine,
                                            chars=len(sentence))
                        self._play(path)
                    future = next_future
                return "".join(full) if collect else ""
            finally:
                self._speaking = False

    def _sentences(self, tokens: Iterable[str], buffer: SentenceBuffer,
                   full: list[str]) -> Iterator[str]:
        for token in tokens:
            if self._stop.is_set():
                break
            full.append(token)
            for sentence in buffer.add(token):
                yield sentence
        for sentence in buffer.flush():
            yield sentence
