"""Загрузка и проверка конфигурации.

Приоритет: config.yaml → config.example.yaml (как демо-шаблон) → встроенные значения.
Ключи вида ${ENV_VAR} берутся из окружения или из файла .env.
"""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config.example.yaml"
ENV_PATH = PROJECT_ROOT / ".env"

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

# Минимальные значения, чтобы ассистент запускался даже с пустым конфигом.
DEFAULTS: dict[str, Any] = {
    "assistant": {
        "name": "Гуль",
        "wake_words": ["привет гуль", "слушай гуль"],
        "language": "ru",
        "persona": "Ты — Гуль, дружелюбный голосовой ассистент. Отвечай кратко, по-русски, без markdown.",
        "max_history_turns": 8,
        "tts_max_chars": 900,
    },
    "audio": {
        "input_device": "default",
        "output_device": "default",
        "sample_rate": 16000,
        "silence_seconds": 0.8,
        "max_record_seconds": 12,
        "vad_threshold": 0,
        "beep": True,
        "barge_in": True,
    },
    "stt": {
        "engine": "vosk",
        "model_path": "models/vosk-model-small-ru-0.22",
        "cloud_fallback": "none",
        "cloud_model": "whisper-large-v3-turbo",
        "wake_mode": "auto",
    },
    "tts": {
        "engines": ["edge", "piper", "rhvoice", "espeak"],
        "edge_voice": "ru-RU-DmitryNeural",
        "piper_binary": "piper",
        "piper_model": "models/piper/ru_RU-dmitry-medium.onnx",
        "rhvoice_voice": "Aleksandr",
        "espeak_voice": "ru",
        "speed": 1.0,
        "volume": 1.0,
    },
    "brain": {
        "order": ["gigachat", "groq", "cloudflare", "generic", "mock"],
        "temperature": 0.6,
        "max_tokens": 450,
        "first_token_timeout": 8,
        "gigachat": {
            "enabled": True,
            "client_id": "",
            "client_secret": "",
            "model": "GigaChat-2",
            "scope": "GIGACHAT_API_PERS",
            "base_url": "https://gigachat.devices.sberbank.ru/api/v1",
            "auth_url": "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
            "verify_ssl": False,
        },
        "groq": {
            "enabled": True,
            "api_key": "",
            "model": "openai/gpt-oss-120b",
            "base_url": "https://api.groq.com/openai/v1",
        },
        "cloudflare": {
            "enabled": True,
            "account_id": "",
            "api_key": "",
            "model": "@cf/openai/gpt-oss-120b",
        },
        "generic": {
            "enabled": False,
            "base_url": "http://127.0.0.1:8080/v1",
            "api_key": "none",
            "model": "local-model",
        },
        "mock": {"enabled": True},
    },
    "network": {"proxy": "", "timeout": 15},
    "skills": {
        "weather": {
            "city": "Москва",
            "lat": 55.7558,
            "lon": 37.6173,
            "cache_minutes": 10,
        },
        "news": {"max_items": 4, "sources": ["https://lenta.ru/rss/news"]},
        "currency": {"cache_hours": 6},
        "music": {"library_dirs": ["~/Music"], "radio": [], "player": "mpv", "volume_step": 10},
        "mqtt": {"enabled": False, "host": "127.0.0.1", "port": 1883, "devices": []},
        "system": {"allow_shutdown": False},
        "wiki": {"enabled": True},
        "notes_file": "data/notes.json",
        "memory_file": "data/memory.json",
        "timers_file": "data/timers.json",
    },
    "web": {"enabled": True, "host": "0.0.0.0", "port": 8787, "token": "", "speak": False},
    "logging": {"level": "INFO", "file": "data/gul.log"},
}


def load_dotenv(path: Path = ENV_PATH) -> None:
    """Читает .env, не перетирая уже заданные переменные окружения."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def repl(match: re.Match) -> str:
            name, fallback = match.group(1), match.group(2)
            return os.environ.get(name, fallback or "")

        return _ENV_RE.sub(repl, value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


class Config:
    """Обёртка над словарём с доступом по пути: cfg.get("tts.engines")."""

    def __init__(self, data: dict, source: Path | None = None):
        self.data = data
        self.source = source

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, name: str) -> dict:
        value = self.get(name, {}) or {}
        return value if isinstance(value, dict) else {}

    def path(self, value: str | os.PathLike) -> Path:
        """Превращает относительный путь из конфига в абсолютный от корня проекта."""
        p = Path(os.path.expanduser(str(value)))
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT

    def warnings(self) -> list[str]:
        """Понятные человеку предупреждения о недонастроенном конфиге."""
        out: list[str] = []
        gc = self.section("brain").get("gigachat", {}) or {}
        if not (gc.get("client_id") and gc.get("client_secret")):
            out.append(
                "GigaChat не настроен (нет GIGACHAT_CLIENT_ID/SECRET) — "
                "основной бесплатный мозг не подключён."
            )
        if not self.path(self.get("stt.model_path", "")).exists():
            out.append(
                f"Модель Vosk не найдена: {self.get('stt.model_path')}. "
                "Запустите scripts/download_models.sh — без неё не будет распознавания речи."
            )
        piper_model = self.path(self.get("tts.piper_model", ""))
        if "piper" in (self.get("tts.engines") or []) and not piper_model.exists():
            out.append(
                "Модель Piper не найдена (необязательно) — локальный нейроголос будет недоступен."
            )
        return out


def load_config(path: str | os.PathLike | None = None) -> Config:
    load_dotenv()
    if path:
        config_path = Path(path)
    elif DEFAULT_CONFIG_PATH.exists():
        config_path = DEFAULT_CONFIG_PATH
    elif EXAMPLE_CONFIG_PATH.exists():
        config_path = EXAMPLE_CONFIG_PATH
    else:
        config_path = None

    file_data: dict = {}
    if config_path and config_path.exists():
        with open(config_path, encoding="utf-8") as fh:
            file_data = yaml.safe_load(fh) or {}
    data = _expand_env(_deep_merge(DEFAULTS, file_data))
    return Config(data, config_path)
