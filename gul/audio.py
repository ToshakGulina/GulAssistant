"""Звук: микрофон через arecord, воспроизведение через aplay/ffplay, VAD.

Никаких PortAudio/PyAudio: на слабом Atom лишние зависимости и сборка — боль.
ALSA-утилиты уже есть в Debian и потребляют минимум памяти.
"""

from __future__ import annotations

import array
import logging
import math
import os
import select
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

log = logging.getLogger(__name__)


class AudioError(RuntimeError):
    pass


def which(name: str) -> str | None:
    return shutil.which(name)


def list_alsa_devices() -> dict:
    out = {"capture": [], "playback": []}
    for kind, cmd in (("capture", ["arecord", "-l"]), ("playback", ["aplay", "-l"])):
        if not which(cmd[0]):
            continue
        try:
            text = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
        except Exception:
            continue
        for line in text.splitlines():
            if line.startswith("card "):
                out[kind].append(line.strip())
    return out


class Microphone:
    """Поток сырого PCM 16 кГц моно из arecord."""

    def __init__(self, device: str = "default", sample_rate: int = 16000, chunk_ms: int = 125):
        self.device = device or "default"
        self.sample_rate = sample_rate
        self.chunk_bytes = int(sample_rate * 2 * chunk_ms / 1000)
        self.chunk_ms = chunk_ms
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return which("arecord") is not None

    def start(self) -> None:
        if not self.available():
            raise AudioError("arecord не найден. Установите: sudo apt install alsa-utils")
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return
            cmd = ["arecord", "-q", "-D", self.device, "-f", "S16_LE",
                   "-r", str(self.sample_rate), "-c", "1", "-t", "raw"]
            try:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            except OSError as exc:
                raise AudioError(f"Не удалось запустить arecord: {exc}") from exc
            time.sleep(0.15)
            if self._proc.poll() is not None:
                err = (self._proc.stderr.read() or b"").decode(errors="ignore")[:400]
                raise AudioError(f"Микрофон не открылся ({self.device}): {err.strip()}. "
                                 "Проверьте устройства командой python run.py --list-devices")

    def read(self, timeout: float = 1.0) -> bytes:
        if not self._proc or self._proc.poll() is not None:
            return b""
        fd = self._proc.stdout.fileno()
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return b""
        data = b""
        while len(data) < self.chunk_bytes:
            piece = os.read(fd, self.chunk_bytes - len(data))
            if not piece:
                break
            data += piece
        return data

    def stop(self) -> None:
        with self._lock:
            if not self._proc:
                return
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()


class VAD:
    """Простой энергетический детектор речи с адаптацией к шуму.

    Для дешёвого микрофона этого достаточно: не тянем webrtcvad и не
    нагружаем CPU. Порог можно задать вручную в config.yaml.
    """

    def __init__(self, threshold: int = 0):
        self.fixed_threshold = int(threshold or 0)
        self.noise = None
        self._calibrated = 0

    def rms(self, chunk: bytes) -> float:
        if len(chunk) < 2:
            return 0.0
        samples = array.array("h")
        samples.frombytes(chunk[: len(chunk) // 2 * 2])
        if not samples:
            return 0.0
        # Быстро и без numpy: средний квадрат.
        total = sum(s * s for s in samples[::2])  # каждый второй сэмпл — хватает
        count = max(1, len(samples[::2]))
        return math.sqrt(total / count)

    def is_speech(self, chunk: bytes) -> bool:
        level = self.rms(chunk)
        if self.fixed_threshold:
            return level > self.fixed_threshold
        if self.noise is None:
            self.noise = level
            return False
        if self._calibrated < 8:
            self._calibrated += 1
            self.noise = min(self.noise, level) if self.noise else level
            return False
        if level < self.noise * 1.25:
            self.noise = 0.97 * self.noise + 0.03 * level
        return level > max(130.0, self.noise * 1.9)

    def reset(self) -> None:
        self._calibrated = 0


def silence_chunk(milliseconds: int = 100, sample_rate: int = 16000) -> bytes:
    return b"\x00\x00" * int(sample_rate * milliseconds / 1000)


def pcm_to_wav(pcm: bytes, path: str | os.PathLike, sample_rate: int = 16000) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sample_rate)
        fh.writeframes(pcm)
    return path


class AudioPlayer:
    """Воспроизведение WAV/MP3 через aplay/ffplay/mpv с возможностью стопа."""

    def __init__(self, device: str = "default"):
        self.device = device or "default"
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._playing = False

    def available(self) -> bool:
        return any(which(b) for b in ("aplay", "ffplay", "mpv"))

    def _command(self, path: Path) -> list[str] | None:
        suffix = path.suffix.lower()
        if suffix == ".wav" and which("aplay"):
            return ["aplay", "-q", "-D", self.device, str(path)]
        if which("ffplay"):
            return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]
        if which("mpv"):
            return ["mpv", "--no-video", "--really-quiet", str(path)]
        if suffix == ".wav" and which("aplay"):
            return ["aplay", "-q", str(path)]
        return None

    def play_file(self, path: str | os.PathLike, blocking: bool = True,
                  timeout: float | None = None) -> bool:
        path = Path(path)
        if not path.exists():
            return False
        cmd = self._command(path)
        if cmd is None:
            log.warning("Нет плеера (aplay/ffplay/mpv) — не могу воспроизвести %s", path)
            return False
        with self._lock:
            self.stop_locked()
            try:
                self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL)
            except OSError as exc:
                log.warning("Плеер не запустился: %s", exc)
                return False
            self._playing = True
        if blocking:
            try:
                self._proc.wait(timeout=timeout or 180)
            except subprocess.TimeoutExpired:
                self.stop()
            finally:
                with self._lock:
                    self._playing = False
                    self._proc = None
        return True

    def play_pcm(self, pcm: bytes, sample_rate: int = 16000) -> bool:
        tmp = Path(tempfile.mkstemp(suffix=".wav")[1])
        try:
            pcm_to_wav(pcm, tmp, sample_rate)
            return self.play_file(tmp, blocking=True)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    def beep(self, frequencies: tuple[int, ...] = (880, 1175), duration: float = 0.07) -> None:
        """Короткий двухтональный сигнал «я вас услышала»."""
        sample_rate = 22050
        frames = bytearray()
        for freq in frequencies:
            for i in range(int(sample_rate * duration)):
                value = int(12000 * math.sin(2 * math.pi * freq * i / sample_rate)
                            * min(1.0, min(i, sample_rate * duration - i) / (sample_rate * 0.01)))
                frames += struct.pack("<h", value)
        tmp = Path(tempfile.mkstemp(suffix=".wav")[1])
        try:
            with wave.open(str(tmp), "wb") as fh:
                fh.setnchannels(1)
                fh.setsampwidth(2)
                fh.setframerate(sample_rate)
                fh.writeframes(bytes(frames))
            self.play_file(tmp, blocking=True, timeout=3)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    def stop(self) -> None:
        with self._lock:
            self.stop_locked()

    def stop_locked(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=1)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None
        self._playing = False

    def is_playing(self) -> bool:
        return self._playing and self._proc is not None and self._proc.poll() is None
