"""Голосовой цикл: слушаем wake-word, записываем команду, отвечаем голосом."""

from __future__ import annotations

import logging
import threading
import time

from .assistant import Assistant
from .audio import AudioError, Microphone, VAD, pcm_to_wav
from .config import Config
from .stt import SpeechEngine
from .utils import strip_wake_words

log = logging.getLogger(__name__)


class VoiceLoop:
    def __init__(self, assistant: Assistant, cfg: Config):
        self.assistant = assistant
        self.cfg = cfg
        self.sample_rate = int(cfg.get("audio.sample_rate", 16000))
        self.silence = float(cfg.get("audio.silence_seconds", 0.8))
        self.max_seconds = float(cfg.get("audio.max_record_seconds", 12))
        self.barge_in = bool(cfg.get("audio.barge_in", True))
        self.beep = bool(cfg.get("audio.beep", True))
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        mic = Microphone(self.cfg.get("audio.input_device", "default"), self.sample_rate)
        if not mic.available():
            raise AudioError("arecord не установлен. sudo apt install alsa-utils")
        engine = SpeechEngine(self.cfg)
        log.info("Загружаю модель распознавания…")
        engine.load()
        wake = engine.wake_detector()
        mic.start()
        log.info("Гуль слушает. Скажите: «%s»", self.cfg.get("assistant.wake_words", ["привет гуль"])[0])
        self.assistant._emit("state", {"state": "idle"})
        try:
            while not self._stop.is_set():
                if self.assistant.speaker.is_speaking():
                    time.sleep(0.05)
                    continue
                chunk = mic.read(timeout=0.5)
                if not chunk:
                    continue
                if not wake.feed(chunk):
                    continue
                if self.beep:
                    try:
                        self.assistant.speaker.player.beep()
                    except Exception:
                        pass
                self.assistant._emit("state", {"state": "listening"})
                text = self._listen_command(mic, engine)
                wake.reset()
                if not text:
                    self.assistant._emit("state", {"state": "idle"})
                    continue
                text = strip_wake_words(text, self.assistant.wake_words)
                if not text:
                    self.assistant._speak("Я здесь.")
                    continue
                log.info("Команда: %s", text)
                self._answer_with_barge_in(text, mic, engine)
                self.assistant._emit("state", {"state": "idle"})
        finally:
            mic.stop()

    def _listen_command(self, mic: Microphone, engine: SpeechEngine) -> str:
        recognizer = engine.command_recognizer()
        vad = VAD(int(self.cfg.get("audio.vad_threshold", 0)))
        started = time.monotonic()
        last_voice = started
        speech_started = False
        collected = bytearray()
        last_partial = ""
        while not self._stop.is_set():
            elapsed = time.monotonic() - started
            if elapsed > self.max_seconds:
                break
            chunk = mic.read(timeout=0.25)
            if not chunk:
                if elapsed > 3 and not speech_started:
                    break
                continue
            collected += chunk
            partial = recognizer.feed(chunk)
            if partial and partial != last_partial:
                speech_started = True
                last_voice = time.monotonic()
                last_partial = partial
            if vad.is_speech(chunk):
                speech_started = True
                last_voice = time.monotonic()
            if speech_started and time.monotonic() - last_voice >= self.silence:
                break
            if not speech_started and elapsed > 5:
                break
        if not collected:
            return ""
        text = recognizer.final().strip()
        if not text and engine.cloud.available() and len(collected) > self.sample_rate // 2:
            wav = pcm_to_wav(bytes(collected), "/tmp/gul-last-command.wav", self.sample_rate)
            text = engine.cloud.transcribe(wav)
        if not text:
            self.assistant._speak("Не расслышала. Повторите, пожалуйста.")
        return text

    def _answer_with_barge_in(self, text: str, mic: Microphone, engine: SpeechEngine) -> None:
        worker = threading.Thread(target=self.assistant.answer_voice, args=(text,), daemon=True)
        worker.start()
        if not self.barge_in:
            worker.join()
            return
        stop_detector = engine.stop_detector()
        while worker.is_alive():
            if self._stop.is_set():
                self.assistant.stop_speaking()
                break
            chunk = mic.read(timeout=0.25)
            if chunk and stop_detector.feed(chunk):
                log.info("Barge-in: пользователь сказал «стоп»")
                self.assistant.stop_speaking()
                break
        worker.join(timeout=3)
