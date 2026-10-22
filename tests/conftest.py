import copy

import pytest

from gul.config import load_config


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Тесты не должны ходить в интернет (и не должны ждать погоду)."""
    monkeypatch.setattr(
        "gul.skills.weather.WeatherService.prewarm", lambda self: None, raising=False
    )


@pytest.fixture()
def cfg(tmp_path):
    config = load_config()
    config.data = copy.deepcopy(config.data)
    config.data["skills"]["notes_file"] = str(tmp_path / "notes.json")
    config.data["skills"]["memory_file"] = str(tmp_path / "memory.json")
    config.data["skills"]["timers_file"] = str(tmp_path / "timers.json")
    config.data["logging"]["file"] = str(tmp_path / "gul.log")
    config.data["web"]["port"] = 0
    config.data["web"]["token"] = ""
    config.data["assistant"]["max_history_turns"] = 4
    config.data["assistant"]["wake_words"] = ["привет гуль"]
    config.data["brain"]["order"] = ["mock"]
    config.data["brain"]["mock"] = {"enabled": True, "delay": 0}
    for provider in ("gigachat", "groq", "cloudflare", "generic"):
        config.data["brain"].setdefault(provider, {})["enabled"] = False
    return config


@pytest.fixture()
def timers(cfg):
    from gul.skills.timers import TimerManager

    manager = TimerManager(cfg)
    yield manager
    manager.stop()


@pytest.fixture()
def ctx(cfg, timers):
    from gul.skills import SkillContext

    return SkillContext(cfg=cfg, timers=timers)


@pytest.fixture()
def assistant(cfg):
    from gul.assistant import Assistant

    instance = Assistant(cfg, enable_audio=False)
    yield instance
    instance.close()
