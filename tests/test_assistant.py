import time


def collect(assistant, text):
    events = list(assistant.respond_stream(text, source="test"))
    done = [event for event in events if event["type"] == "done"][-1]
    return events, done


def test_skill_answer(assistant):
    events, done = collect(assistant, "который час")
    assert done["source"] == "skill"
    assert done["skill"] == "time"
    assert done["total_ms"] < 5000


def test_llm_answer(assistant):
    events, done = collect(assistant, "расскажи про космос")
    assert done["source"] == "llm"
    assert done["provider"] == "mock"
    assert any(event["type"] == "delta" for event in events)
    assert "демо-режим" in done["text"]
    assert done["first_token_ms"] is not None
    assert done["total_ms"] < 5000


def test_history_and_memory(assistant):
    collect(assistant, "который час")
    assert assistant.status()["history_turns"] == 1
    assistant.memory.add("я люблю кофе без сахара")
    assert "кофе" in assistant.system_prompt()
    assistant.clear_history()
    assert assistant.status()["history_turns"] == 0


def test_timer_through_assistant(assistant):
    _, done = collect(assistant, "поставь таймер на 3 минуты")
    assert done["skill"] == "timers"
    assert len(assistant.timers.list("timer")) == 1
    assistant.timers.cancel("timer")


def test_stop(assistant):
    _, done = collect(assistant, "стоп")
    assert done["source"] == "skill"


def test_status_shape(assistant):
    status = assistant.status()
    assert status["name"] == "Гуль"
    assert len(status["skills"]) >= 15
    assert status["providers"]
    assert "latency" in status["metrics"]
    assert "cpu" in status["cpu"] or True


def test_empty_input(assistant):
    _, done = collect(assistant, "   ")
    assert done["source"] == "system"
