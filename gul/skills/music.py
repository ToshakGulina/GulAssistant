"""Музыка и радио через mpv (управление по IPC, без перезапуска плеера).

mpv лёгкий (~40 МБ) и умеет всё: интернет-радио, mp3/flac/ogg, плейлисты.
На 4 ГБ RAM это важнее «красоты» — поэтому не тащим в проект браузер и GUI.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .base import Skill, SkillContext, SkillResult

log = logging.getLogger(__name__)

_AUDIO_EXT = {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".aac", ".opus", ".wma"}


def _norm(text: str) -> str:
    return re.sub(r"[^a-zа-я0-9]+", " ", text.lower().replace("ё", "е")).strip()


class MusicPlayer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.binary = cfg.get("skills.music.player", "mpv") or "mpv"
        self.socket_path = os.path.join(tempfile.gettempdir(), f"gul-mpv-{os.getpid()}.sock")
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._title = ""
        self._volume = 70

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _ensure(self) -> bool:
        if self._alive():
            return True
        if not self.available():
            return False
        try:
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
        except OSError:
            pass
        try:
            self._proc = subprocess.Popen(
                [self.binary, "--idle=yes", "--no-video", "--no-terminal",
                 f"--input-ipc-server={self.socket_path}", "--really-quiet",
                 f"--volume={self._volume}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            log.warning("mpv не запустился: %s", exc)
            return False
        for _ in range(30):
            if os.path.exists(self.socket_path):
                return True
            time.sleep(0.05)
        log.warning("mpv не создал IPC-сокет")
        return False

    def _command(self, *args) -> dict | None:
        if not self._ensure():
            return None
        with self._lock:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.settimeout(2)
                    sock.connect(self.socket_path)
                    sock.sendall((json.dumps({"command": list(args)}) + "\n").encode())
                    data = b""
                    while b"\n" not in data:
                        chunk = sock.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                    return json.loads(data.decode(errors="ignore").splitlines()[0])
            except Exception as exc:
                log.debug("mpv IPC: %s", exc)
                return None

    def play(self, url_or_path: str) -> bool:
        response = self._command("loadfile", url_or_path)
        if response is None:
            return False
        self._title = Path(url_or_path).name if not url_or_path.startswith("http") else ""
        self._command("set_property", "pause", False)
        return True

    def play_playlist(self, paths: list[str]) -> bool:
        if not paths:
            return False
        # mpv читает плейлист построчно; пути с переводами строк игнорируем.
        fd, list_path = tempfile.mkstemp(suffix=".m3u", text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("\n".join(p for p in paths if "\n" not in p) + "\n")
            if not self._ensure():
                return False
            response = self._command("loadlist", list_path, "replace")
            self._command("set_property", "pause", False)
            self._title = Path(paths[0]).name
            return response is not None
        finally:
            try:
                os.unlink(list_path)
            except OSError:
                pass

    def toggle_pause(self) -> bool | None:
        response = self._command("get_property", "pause")
        if response is None:
            return None
        paused = bool(response.get("data"))
        self._command("set_property", "pause", not paused)
        return not paused  # True = теперь играет

    def stop(self) -> bool:
        if not self._alive():
            return False
        self._command("stop")
        try:
            self._proc.terminate()
            self._proc.wait(timeout=2)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None
        self._title = ""
        return True

    def next(self) -> bool:
        return self._command("playlist-next") is not None

    def previous(self) -> bool:
        return self._command("playlist-prev") is not None

    def change_volume(self, delta: int) -> int | None:
        response = self._command("get_property", "volume")
        current = response.get("data") if response else None
        if current is None:
            current = self._volume
        new = max(0, min(100, int(current) + delta))
        self._volume = new
        if response is not None:
            self._command("set_property", "volume", new)
        return new

    def set_volume(self, value: int) -> int:
        value = max(0, min(100, value))
        self._volume = value
        if self._alive():
            self._command("set_property", "volume", value)
        return value

    def current_title(self) -> str:
        response = self._command("get_property", "media-title")
        if response and isinstance(response.get("data"), str):
            return response["data"]
        return self._title

    def playing(self) -> bool:
        if not self._alive():
            return False
        pause = self._command("get_property", "pause")
        return bool(pause is not None and not pause.get("data"))


class MusicSkill(Skill):
    name = "music"
    title = "Музыка и радио"
    description = "Интернет-радио и локальная фонотека, управление голосом."
    examples = ["включи радио Европа Плюс", "включи музыку", "сделай громче",
                "пауза", "следующий трек", "что играет"]
    priority = 73

    def __init__(self):
        self.player: MusicPlayer | None = None
        self._library: list[Path] = []
        self._library_at = 0.0
        self._lock = threading.Lock()

    def get_player(self, ctx: SkillContext) -> MusicPlayer:
        if self.player is None:
            self.player = MusicPlayer(ctx.cfg)
        return self.player

    def _scan_library(self, ctx: SkillContext) -> list[Path]:
        with self._lock:
            if self._library and time.time() - self._library_at < 300:
                return self._library
        files: list[Path] = []
        for folder in ctx.get("skills.music.library_dirs", []) or []:
            root = Path(os.path.expanduser(folder))
            if not root.exists():
                continue
            try:
                for path in root.rglob("*"):
                    if path.suffix.lower() in _AUDIO_EXT and path.is_file():
                        files.append(path)
            except OSError:
                continue
        files.sort()
        with self._lock:
            self._library = files
            self._library_at = time.time()
        return files

    def _find_station(self, query: str, ctx: SkillContext) -> dict | None:
        stations = ctx.get("skills.music.radio", []) or []
        q = _norm(query)
        if not q:
            return None
        for station in stations:
            if _norm(station.get("name", "")) in q or q in _norm(station.get("name", "")):
                return station
        return None

    def _find_tracks(self, query: str, ctx: SkillContext) -> list[Path]:
        files = self._scan_library(ctx)
        q = _norm(query)
        if not q:
            return []
        words = [w for w in q.split() if len(w) > 1]
        scored: list[tuple[int, Path]] = []
        for path in files:
            name = _norm(path.stem)
            score = sum(1 for w in words if w in name)
            if score:
                scored.append((score, path))
        scored.sort(key=lambda item: (-item[0], str(item[1])))
        return [p for _, p in scored[:20]]

    def handle(self, text: str, ctx: SkillContext) -> SkillResult | None:
        if not re.search(r"(музык|радио|песн|трек|играет|плейлист|громч|тише|громкост|"
                         r"пауза|продолжи|включи|выключи|играй|стоп)", text):
            return None
        # Не перехватываем «включи свет» — этим занимается умный дом.
        if re.search(r"(свет|розетк|ламп|кондиционер|чайник|телевизор|тв)\b", text) and "музык" not in text:
            return None
        if re.search(r"(таймер|будильник|напомни|погод|новост)", text):
            return None

        player = self.get_player(ctx)

        if re.search(r"(что (сейчас )?играет|какая (сейчас )?(песня|музыка|станция))", text):
            title = player.current_title() if player.available() else ""
            if title:
                return SkillResult(text=f"Сейчас играет: {title}.")
            return SkillResult(text="Сейчас ничего не играет.")

        if re.search(r"(стоп|выключи|останови)", text) and re.search(r"(музык|радио|песн|трек)", text):
            stopped = player.stop()
            return SkillResult(text="Выключила музыку." if stopped else "Музыка и так не играет.")

        if re.search(r"(пауза|приостанови)", text):
            state = player.toggle_pause()
            if state is None:
                return SkillResult(text="Сейчас нечего ставить на паузу.")
            return SkillResult(text="Продолжаю." if state else "Пауза.")

        if re.search(r"(продолжи|играй|возобнови)", text) and not re.search(r"включи", text):
            state = player.toggle_pause()
            if state is None:
                return SkillResult(text="Сейчас ничего не играет.")
            return SkillResult(text="Продолжаю." if state else "Пауза.")

        m = re.search(r"(громче|тише|громкость|убавь|прибавь)", text)
        if m:
            if "громкость" in text:
                num = re.search(r"(\d{1,3})", text)
                if num:
                    value = player.set_volume(int(num.group(1)))
                    return SkillResult(text=f"Громкость: {value} процентов.")
            delta = int(ctx.get("skills.music.volume_step", 10))
            if re.search(r"(тише|убавь|меньше)", text):
                delta = -delta
            if not player.available():
                return SkillResult(text="Не могу изменить громкость: установите mpv "
                                         "(sudo apt install mpv) и включите музыку.")
            value = player.change_volume(delta)
            if value is None:
                return SkillResult(text="Не могу изменить громкость: плеер не запущен.")
            return SkillResult(text=f"Сделала {'тише' if delta < 0 else 'громче'}, громкость {value} процентов.")

        if re.search(r"(следующ|дальше|переключи)", text):
            if player.next():
                time.sleep(0.3)
                return SkillResult(text="Переключаю.")
            return SkillResult(text="Плейлист закончился или не запущен.")
        if re.search(r"(предыдущ|назад)", text):
            if player.previous():
                return SkillResult(text="Возвращаюсь к предыдущему треку.")
            return SkillResult(text="Не могу переключить.")

        if not re.search(r"(включи|поставь|запусти|играй)", text):
            return None
        if not player.available():
            return SkillResult(text="Для музыки установите mpv: sudo apt install mpv. "
                                     "Без него я не смогу воспроизводить звук.")

        query = text
        query = re.sub(r"^(включи|поставь|запусти|играй|мне|пожалуйста)\s+", "", query)
        query = re.sub(r"\b(музыку|музыка|радио|песню|трек|пожалуйста)\b", " ", query)
        query = re.sub(r"\s+", " ", query).strip(" .,!?—-")

        station = self._find_station(query, ctx)
        if station is None and not query:
            stations = ctx.get("skills.music.radio", []) or []
            if stations:
                station = stations[0]
                query = station.get("name", "")
        if station:
            if player.play(station["url"]):
                time.sleep(0.6)
                title = player.current_title() or station["name"]
                return SkillResult(text=f"Включаю «{station['name']}».", data={"title": title})
            return SkillResult(text=f"Не удалось включить «{station['name']}». Проверьте интернет.")

        if query:
            tracks = self._find_tracks(query, ctx)
            if tracks:
                if player.play_playlist([str(p) for p in tracks[:20]]):
                    return SkillResult(
                        text=f"Включаю «{tracks[0].stem}»." if len(tracks) == 1
                        else f"Включаю подборку из {len(tracks)} треков, начиная с «{tracks[0].stem}»."
                    )
                return SkillResult(text="Не удалось запустить воспроизведение.")

        files = self._scan_library(ctx)
        if files:
            random.shuffle(files)
            if player.play_playlist([str(p) for p in files[:30]]):
                return SkillResult(text="Включаю музыку из вашей фонотеки.")
        return SkillResult(
            text="Не нашла такую музыку. Добавьте файлы в папку ~/Music или настройте "
                 "радиостанции в config.yaml. Скажите, например: «Включи радио Европа Плюс»."
        )
