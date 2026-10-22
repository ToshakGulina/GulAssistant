import time
from datetime import datetime, timedelta

from gul.skills.timers import TimerManager


def test_timer_fires(cfg):
    fired = []
    manager = TimerManager(cfg, on_fire=fired.append)
    try:
        manager.add("timer", datetime.now() + timedelta(seconds=0.2), "быстрый")
        deadline = time.time() + 3
        while not fired and time.time() < deadline:
            time.sleep(0.05)
        assert fired, "таймер не сработал"
        assert fired[0].kind == "timer"
        assert manager.list() == []
    finally:
        manager.stop()


def test_events_persist(cfg):
    when = datetime.now() + timedelta(hours=2)
    first = TimerManager(cfg)
    first.add("reminder", when, "позвонить")
    first.stop()

    second = TimerManager(cfg)
    try:
        events = second.list("reminder")
        assert events
        assert events[0].text == "позвонить"
    finally:
        second.stop()


def test_cancel(cfg):
    manager = TimerManager(cfg)
    try:
        manager.add("timer", datetime.now() + timedelta(minutes=5))
        manager.add("alarm", datetime.now() + timedelta(hours=3))
        assert manager.cancel("timer") == 1
        assert manager.list("timer") == []
        assert len(manager.list("alarm")) == 1
        assert manager.cancel() == 1
        assert manager.list() == []
    finally:
        manager.stop()
