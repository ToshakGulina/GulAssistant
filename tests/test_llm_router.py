import pytest

from gul.llm.base import ChatMessage, LLMError, LLMProvider
from gul.llm.providers import MockProvider
from gul.llm.router import LLMRouter, build_router


class OkProvider(LLMProvider):
    name = "ok"
    title = "Ok provider"

    def available(self):
        return True

    def stream(self, system, messages, **options):
        yield "При"
        yield "вет"


class FailProvider(LLMProvider):
    name = "fail"
    title = "Fail provider"

    def available(self):
        return True

    def stream(self, system, messages, **options):
        raise LLMError("сервис недоступен")
        yield  # pragma: no cover


def test_mock_provider_streams(cfg):
    provider = MockProvider(cfg)
    text = provider.complete("", [ChatMessage("user", "привет")])
    assert "Гуль" in text


def test_router_falls_back(cfg):
    router = LLMRouter(cfg)
    router.providers = [FailProvider(), OkProvider()]
    text = "".join(router.stream("system", [ChatMessage("user", "привет")]))
    assert text == "Привет"
    assert router.last_provider == "ok"
    status = {item["name"]: item for item in router.status()}
    assert status["fail"]["state"] == "error"
    assert status["fail"]["cooldown_seconds"] > 0


def test_router_raises_when_all_fail(cfg):
    router = LLMRouter(cfg)
    router.providers = [FailProvider()]
    with pytest.raises(LLMError):
        list(router.stream("system", [ChatMessage("user", "привет")]))


def test_build_router_with_mock_only(cfg):
    router = build_router(cfg)
    assert [provider.name for provider in router.providers] == ["mock"]
    text = "".join(router.stream("system", [ChatMessage("user", "кто ты")]))
    assert "Гуль" in text


def test_router_records_metrics(cfg):
    router = build_router(cfg)
    list(router.stream("system", [ChatMessage("user", "спасибо")]))
    events = [event for event in cfg and router.metrics.last(20) if event["event"] == "llm_first_token"]
    assert events
