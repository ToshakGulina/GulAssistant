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


# ---------------------------------------------------------------------------
# Groq: прямой доступ, прокси и свой релей
# ---------------------------------------------------------------------------

import json

from gul.llm.providers import GroqProvider


def _groq_cfg(cfg, **section):
    cfg.data["brain"]["groq"] = {
        "enabled": True,
        "api_key": "",
        "model": "openai/gpt-oss-20b",
        "base_url": "https://api.groq.com/openai/v1",
        **section,
    }
    return cfg


def test_groq_direct_uses_key(cfg):
    provider = GroqProvider(_groq_cfg(cfg, api_key="gsk_test"))
    assert provider.mode == "direct"
    assert provider.base_url == "https://api.groq.com/openai/v1"
    assert provider.api_key == "gsk_test"
    assert provider.available() is True


def test_groq_direct_without_key_is_unavailable(cfg):
    provider = GroqProvider(_groq_cfg(cfg))
    assert provider.available() is False


def test_groq_relay_wins_over_direct_key(cfg):
    cfg.data["network"]["proxy"] = "http://127.0.0.1:8118"
    provider = GroqProvider(_groq_cfg(
        cfg,
        api_key="gsk_test",
        relay_url="https://relay.example.workers.dev/openai/v1/",
        relay_token="relay_secret",
    ))
    assert provider.mode == "relay"
    assert provider.base_url == "https://relay.example.workers.dev/openai/v1"
    # В релей идём токеном релея, а не ключом Groq.
    assert provider.api_key == "relay_secret"
    # Общий прокси к релею не применяется — иначе теряем весь смысл.
    assert provider.proxy == ""
    assert provider.available() is True


def test_groq_relay_without_token_still_usable(cfg):
    provider = GroqProvider(_groq_cfg(cfg, relay_url="https://relay.example.workers.dev/openai/v1"))
    assert provider.mode == "relay"
    assert provider.available() is True


def test_groq_uses_per_provider_proxy(cfg):
    cfg.data["network"]["proxy"] = "http://common:8118"
    provider = GroqProvider(_groq_cfg(cfg, api_key="gsk", proxy="socks5h://127.0.0.1:1080"))
    assert provider.proxy == "socks5h://127.0.0.1:1080"
    assert provider.mode == "proxy"


def test_groq_falls_back_to_common_proxy(cfg):
    cfg.data["network"]["proxy"] = "http://common:8118"
    provider = GroqProvider(_groq_cfg(cfg, api_key="gsk"))
    assert provider.proxy == "http://common:8118"
    assert provider.mode == "proxy"


def test_groq_sets_voice_friendly_reasoning(cfg):
    provider = GroqProvider(_groq_cfg(cfg, api_key="gsk"))
    assert provider.extra_params["reasoning_effort"] == "low"
    assert provider.extra_params["include_reasoning"] is False
    assert provider.token_param == "max_completion_tokens"


def test_groq_payload_uses_completion_tokens(cfg):
    provider = GroqProvider(_groq_cfg(cfg, api_key="gsk"))
    payload = provider._payload("system", [ChatMessage("user", "привет")], True, {})
    assert payload["max_completion_tokens"] == provider.max_tokens
    assert "max_tokens" not in payload
    assert payload["reasoning_effort"] == "low"
    assert payload["stream"] is True


class _FakeResponse:
    def __init__(self, status_code=200, body=None, lines=None):
        self.status_code = status_code
        self._body = body or {}
        self._lines = lines or []
        self.headers = {"content-type": "text/event-stream"}

    def json(self):
        return self._body

    def iter_lines(self):
        return iter(self._lines)


def test_groq_drops_unsupported_params_on_400(cfg):
    provider = GroqProvider(_groq_cfg(cfg, api_key="gsk"))
    calls = []

    def fake_post(payload, stream):
        calls.append(dict(payload))
        if len(calls) == 1:
            return _FakeResponse(400, {"error": {"message": "unsupported parameter"}})
        return _FakeResponse(200, lines=[b'data: {"choices":[{"delta":{"content":"ok"}}]}'])

    provider._post = fake_post  # type: ignore[assignment]
    text = "".join(provider.stream("system", [ChatMessage("user", "привет")]))
    assert text == "ok"
    assert len(calls) == 2
    # первую попытку отправили с reasoning_effort, вторую — уже без него
    assert "reasoning_effort" in json.dumps(calls[0]["messages"]) or "reasoning_effort" in calls[0]
    assert "reasoning_effort" not in calls[1]


def test_router_cooldown_depends_on_error(cfg):
    from gul.llm.router import LLMRouter

    router = LLMRouter(cfg)
    assert router._cooldown_for(LLMError("403", status=403)) >= 300
    assert router._cooldown_for(LLMError("401", status=401)) >= 300
    assert router._cooldown_for(LLMError("429", status=429)) < 60
    assert router._cooldown_for(LLMError("сеть недоступна")) < 60


def test_router_skips_provider_in_cooldown(cfg):
    router = LLMRouter(cfg)
    failing = FailProvider()
    router.providers = [failing, OkProvider()]
    list(router.stream("system", [ChatMessage("user", "раз")]))
    # Второй вызов не должен снова бить упавший провайдер.
    calls = {"n": 0}

    class Counting(FailProvider):
        def stream(self, system, messages, **options):
            calls["n"] += 1
            raise LLMError("снова упал")
            yield  # pragma: no cover

    router.providers = [Counting(), OkProvider()]
    text = "".join(router.stream("system", [ChatMessage("user", "два")]))
    assert text == "Привет"
    # Counting — новый объект с тем же именем, но кулдаун хранится по имени.
    assert calls["n"] == 0


def test_brain_test_reports_missing_keys(cfg, capsys):
    from gul.doctor import brain_test

    cfg.data["brain"]["order"] = ["groq", "mock"]
    cfg.data["brain"]["groq"] = {"enabled": True, "api_key": "", "model": "openai/gpt-oss-20b"}
    code = brain_test(cfg)
    out = capsys.readouterr().out
    assert code == 0
    assert "Groq" in out
    assert "не настроен" in out
    assert "Демо-мозг" in out


def test_brain_test_fails_when_requested_provider_missing(cfg, capsys):
    from gul.doctor import brain_test

    cfg.data["brain"]["order"] = ["groq"]
    cfg.data["brain"]["groq"] = {"enabled": True, "api_key": "", "model": "openai/gpt-oss-20b"}
    code = brain_test(cfg, only="groq")
    out = capsys.readouterr().out
    assert code == 1
    assert "не готов" in out
    assert "GROQ_RELAY_URL" in out


def test_brain_test_reports_unknown_provider(cfg, capsys):
    from gul.doctor import brain_test

    cfg.data["brain"]["order"] = ["mock"]
    code = brain_test(cfg, only="groq")
    out = capsys.readouterr().out
    assert code == 2
    assert "не включён" in out


def test_brain_test_explains_403(cfg, capsys):
    from gul.doctor import brain_test

    cfg.data["brain"]["order"] = ["groq"]
    cfg.data["brain"]["groq"] = {"enabled": True, "api_key": "gsk_test",
                                 "model": "openai/gpt-oss-20b"}

    class GeoBlocked(GroqProvider):
        def stream(self, system, messages, **options):
            raise LLMError("Groq (LPU): 403 Forbidden", status=403)
            yield  # pragma: no cover

    import gul.llm.router as router_module

    original = router_module.GroqProvider
    router_module.GroqProvider = GeoBlocked
    try:
        code = brain_test(cfg)
    finally:
        router_module.GroqProvider = original
    out = capsys.readouterr().out
    assert code == 1
    assert "403" in out
    assert "релей" in out
