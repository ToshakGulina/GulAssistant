"""Конкретные бесплатные провайдеры.

GigaChat  — Сбер, из РФ без VPN, 365 млн токенов/год физическим лицам.
Groq      — самый быстрый (LPU), но из РФ нужен VPN/прокси.
Cloudflare— 10 000 нейронов/день бесплатно, без карты.
OpenAICompat — любой свой сервер (llama.cpp, vLLM, DeepSeek, AIR и пр.).
Mock      — демо-режим без ключей, чтобы интерфейс и скиллы работали сразу.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

import requests

from .. import __version__
from ..config import Config
from ..utils import atomic_write_json, read_json
from .base import ChatMessage, LLMError, LLMProvider, extract_error, iter_sse_content

log = logging.getLogger(__name__)


_UNSET = object()


class _HttpProvider(LLMProvider):
    """Общая обвязка: сессия, прокси, таймауты, разбор ошибок."""

    def __init__(self, cfg: Config, proxy: Any = _UNSET):
        self.cfg = cfg
        # Прокси можно задать для конкретного провайдера; иначе — общий network.proxy.
        # Релею (Cloudflare) прокси не нужен и только добавит задержку.
        self.proxy = (cfg.get("network.proxy", "") or "") if proxy is _UNSET else str(proxy or "")
        self.timeout = float(cfg.get("network.timeout", 15))
        self.first_token_timeout = float(cfg.get("brain.first_token_timeout", 8))
        self.max_tokens = int(cfg.get("brain.max_tokens", 450))
        self.temperature = float(cfg.get("brain.temperature", 0.6))
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": f"GulAssistant/{__version__}"})
        if self.proxy:
            self.session.proxies.update({"http": self.proxy, "https": self.proxy})

    def _timeout(self) -> tuple[float, float]:
        return (5.0, self.first_token_timeout)

    def _options(self, options: dict) -> dict:
        return {
            "temperature": options.get("temperature", self.temperature),
            "max_tokens": options.get("max_tokens", self.max_tokens),
        }


class GigaChatProvider(_HttpProvider):
    name = "gigachat"
    title = "GigaChat (Сбер, РФ)"

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        section = cfg.section("brain").get("gigachat", {}) or {}
        self.client_id = str(section.get("client_id") or "").strip()
        self.client_secret = str(section.get("client_secret") or "").strip()
        self.model = section.get("model", "GigaChat-2")
        self.scope = section.get("scope", "GIGACHAT_API_PERS")
        self.base_url = str(section.get("base_url", "")).rstrip("/")
        self.auth_url = section.get("auth_url", "")
        self.verify = bool(section.get("verify_ssl", False))
        self._token: str | None = None
        self._expires_at: float = 0
        self._token_file: Path = cfg.path("data/.gigachat_token.json")
        if not self.verify:
            try:
                import urllib3

                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass

    def available(self) -> bool:
        return bool(self.client_id and self.client_secret and self.base_url)

    # -- авторизация ------------------------------------------------------
    def _load_cached_token(self) -> None:
        data = read_json(self._token_file, {})
        if (
            data.get("access_token")
            and data.get("client_id") == self.client_id
            and float(data.get("expires_at", 0)) / 1000 > time.time() + 90
        ):
            self._token = data["access_token"]
            self._expires_at = float(data["expires_at"]) / 1000

    def _fetch_token(self) -> None:
        self._load_cached_token()
        if self._token and self._expires_at > time.time() + 90:
            return
        rquid = str(uuid.uuid4())
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": rquid,
            "Authorization": f"Basic {basic}",
        }
        try:
            resp = self.session.post(
                self.auth_url,
                headers=headers,
                data={"scope": self.scope},
                timeout=(5, 15),
                verify=self.verify,
            )
        except requests.RequestException as exc:
            raise LLMError(f"GigaChat: нет связи с сервером авторизации ({exc.__class__.__name__})") from exc
        if resp.status_code >= 400:
            raise LLMError(f"GigaChat: авторизация не удалась (HTTP {resp.status_code}). "
                           "Проверьте client_id/secret и scope.", status=resp.status_code)
        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMError("GigaChat: сервер авторизации вернул не JSON") from exc
        token = data.get("access_token")
        if not token:
            raise LLMError("GigaChat: в ответе авторизации нет access_token")
        self._token = token
        # expires_at приходит в миллисекундах; подстрахуемся, если его нет.
        expires_ms = float(data.get("expires_at") or (time.time() + 25 * 60) * 1000)
        self._expires_at = expires_ms / 1000
        atomic_write_json(self._token_file, {
            "access_token": token,
            "expires_at": expires_ms,
            "client_id": self.client_id,
        })

    # -- генерация --------------------------------------------------------
    def stream(self, system: str, messages: list[ChatMessage], **options: Any) -> Iterator[str]:
        if not self.available():
            raise LLMError("GigaChat не настроен: нет ключей")
        self._fetch_token()
        payload = {
            "model": options.get("model", self.model),
            "messages": [{"role": "system", "content": system}] + [
                {"role": m.role, "content": m.content} for m in messages
            ],
            "stream": True,
            **self._options(options),
        }
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        try:
            resp = self.session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                stream=True,
                timeout=self._timeout(),
                verify=self.verify,
            )
        except requests.RequestException as exc:
            raise LLMError(f"GigaChat: сеть недоступна ({exc.__class__.__name__})") from exc
        if resp.status_code == 401:
            # Токен мог протухнуть раньше времени — обновляем один раз.
            self._token = None
            self._fetch_token()
            headers["Authorization"] = f"Bearer {self._token}"
            resp = self.session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                stream=True,
                timeout=self._timeout(),
                verify=self.verify,
            )
        if resp.status_code >= 400:
            raise LLMError(f"GigaChat: {extract_error(resp)}", status=resp.status_code)
        produced = False
        for chunk in iter_sse_content(resp.iter_lines()):
            produced = True
            yield chunk
        if not produced:
            # Иногда стрим не приходит — добираем обычным запросом.
            payload["stream"] = False
            resp2 = self.session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=(5, self.timeout),
                verify=self.verify,
            )
            if resp2.status_code >= 400:
                raise LLMError(f"GigaChat: {extract_error(resp2)}", status=resp2.status_code)
            data = resp2.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            if content:
                yield content


class OpenAICompatProvider(_HttpProvider):
    """Groq, Cloudflare, локальные серверы и любые OpenAI-совместимые API."""

    def __init__(self, cfg: Config, name: str, title: str, base_url: str,
                 api_key: str, model: str, extra_headers: dict | None = None,
                 extra_params: dict | None = None, token_param: str = "max_tokens",
                 proxy: Any = _UNSET):
        super().__init__(cfg, proxy=proxy)
        self.name = name
        self.title = title
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model
        self.extra_headers = extra_headers or {}
        self.extra_params = dict(extra_params or {})
        self.token_param = token_param or "max_tokens"
        self.mode = "direct"

    def available(self) -> bool:
        return bool(self.base_url and self.model and self.api_key)

    def _headers(self) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        headers.update(self.extra_headers)
        return headers

    def _post(self, payload: dict, stream: bool):
        try:
            return self.session.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
                stream=stream,
                timeout=self._timeout() if stream else (5, self.timeout),
            )
        except requests.RequestException as exc:
            raise LLMError(f"{self.title}: сеть недоступна ({exc.__class__.__name__})") from exc

    def _payload(self, system: str, messages: list[ChatMessage], stream: bool,
                 options: dict) -> dict:
        params = self._options(options)
        max_tokens = params.pop("max_tokens", self.max_tokens)
        payload = {
            "model": options.get("model", self.model),
            "messages": [{"role": "system", "content": system}] + [
                {"role": m.role, "content": m.content} for m in messages
            ],
            "stream": stream,
            # Groq считает reasoning-токены в этом же лимите, поэтому имя
            # параметра вынесено в конфиг (max_completion_tokens).
            self.token_param: max_tokens,
            **params,
            **self.extra_params,
        }
        return payload

    def stream(self, system: str, messages: list[ChatMessage], **options: Any) -> Iterator[str]:
        if not self.available():
            raise LLMError(f"{self.title}: не настроен")
        resp = self._post(self._payload(system, messages, True, options), stream=True)
        if resp.status_code == 400 and self.extra_params:
            # Провайдер не принял необязательные параметры (например, reasoning_effort
            # для модели, которая его не поддерживает). Убираем и пробуем снова.
            dropped = ", ".join(self.extra_params)
            log.warning("%s: параметры %s не приняты, повторяю без них", self.title, dropped)
            self.extra_params = {}
            resp = self._post(self._payload(system, messages, True, options), stream=True)
        if resp.status_code in (400, 404, 422):
            # Некоторые бесплатные эндпоинты не умеют stream — пробуем без него.
            resp = self._post(self._payload(system, messages, False, options), stream=False)
        if resp.status_code >= 400:
            raise LLMError(f"{self.title}: {extract_error(resp)}", status=resp.status_code)
        if resp.headers.get("content-type", "").startswith("application/json"):
            data = resp.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            if content:
                yield content
            return
        produced = False
        for chunk in iter_sse_content(resp.iter_lines()):
            produced = True
            yield chunk
        if not produced:
            raise LLMError(f"{self.title}: пустой ответ модели")


class GroqProvider(OpenAICompatProvider):
    """Groq (LPU) — самый быстрый бесплатный мозг.

    Три режима, выбираются конфигом:
      • прямой      — base_url api.groq.com + ключ (нужен VPN/прокси из РФ);
      • через прокси — то же, но brain.groq.proxy или network.proxy;
      • через релей — relay_url своего Cloudflare Worker, ключ Groq живёт
        только в секретах Worker и на неттоп не попадает.

    gpt-oss — reasoning-модель: thinking тратит и время, и лимит токенов.
    Для голоса ставим reasoning_effort=low и не просим показывать рассуждения.
    """

    name = "groq"
    title = "Groq (LPU)"

    def __init__(self, cfg: Config):
        section = dict((cfg.section("brain").get("groq", {}) or {}))
        relay_url = str(section.get("relay_url") or "").strip().rstrip("/")
        self.relay = bool(relay_url)
        self.relay_token = str(section.get("relay_token") or "").strip()

        model = str(section.get("model") or "openai/gpt-oss-20b")
        extra = dict(section.get("extra_params") or {})
        if model.startswith("openai/gpt-oss") or "gpt-oss" in model:
            # Значения вне low/medium/high Groq отклоняет с 400.
            extra.setdefault("reasoning_effort", "low")
            extra.setdefault("include_reasoning", False)

        if self.relay:
            base_url = relay_url
            # Клиент авторизуется в релее своим токеном, а не ключом Groq.
            api_key = self.relay_token or str(section.get("api_key") or "") or "relay"
            proxy: Any = section.get("proxy", "")
            title = "Groq (через свой релей)"
            mode = "relay"
        else:
            base_url = str(section.get("base_url") or "https://api.groq.com/openai/v1")
            api_key = str(section.get("api_key") or "")
            proxy = section.get("proxy", _UNSET)
            title = "Groq (LPU)"
            mode = "proxy" if (proxy not in (_UNSET, "", None) or cfg.get("network.proxy")) else "direct"

        super().__init__(
            cfg, "groq", title, base_url, api_key, model,
            extra_params=extra,
            token_param=str(section.get("token_param") or "max_completion_tokens"),
            proxy=proxy,
        )
        self.mode = mode
        self._relay_configured = self.relay

    def available(self) -> bool:
        if self.relay:
            # Без токена релей тоже может быть открыт — тогда хватит URL и модели.
            return bool(self.base_url and self.model)
        return bool(self.base_url and self.model and self.api_key)

    def info(self) -> dict:
        return {"mode": self.mode, "model": self.model}


class MockProvider(LLMProvider):
    """Демо-мозг: без ключей, без интернета.

    Нужен, чтобы можно было собрать и проверить ассистента до регистрации
    в GigaChat. Отвечает честно: «я в демо-режиме», плюс немного болтовни.
    """

    name = "mock"
    title = "Демо-мозг (без ключей)"

    _CANNED = [
        (r"\b(привет|здравствуй|здорово|добрый (день|вечер)|доброе утро)\b",
         "Привет! Я Гуль. Работаю в демо-режиме, но команды уже понимаю: время, таймеры, "
         "погоду, новости, курсы, заметки и музыку. Подключите GigaChat — и я стану по-настоящему умным."),
        (r"\b(как дела|как ты|как настроение)\b",
         "Отлично, работаю стабильно и почти не греюсь. А у вас как дела?"),
        (r"\b(кто ты|как тебя зовут|твое имя|что ты такое)\b",
         "Я Гуль — голосовой ассистент, которого вы собрали сами. Живу на маленьком неттопе, "
         "думаю в бесплатном облаке, а всё бытовое делаю локально и мгновенно."),
        (r"\b(что ты умеешь|твои возможности|помощь|команды)\b",
         "Многое: скажу время и дату, поставлю таймер, будильник и напоминание, расскажу погоду "
         "и новости, курс валют, посчитаю пример, переведу, найду в Википедии, включу музыку или радио, "
         "запишу в заметки и запомню факты о вас. А на остальные вопросы отвечу, когда подключите бесплатный GigaChat."),
        (r"\b(спасибо|благодарю)\b", "Всегда пожалуйста! Обращайтесь."),
        (r"\b(анекдот|шутк|пошути)\b",
         "Программист ставит на ночь два стакана: один с водой — если захочет пить, "
         "второй пустой — если не захочет."),
        (r"\b(любовь|смысл жизни)\b",
         "В демо-режиме я философствую осторожно: смысл — в людях, которым можно помочь. "
         "А вот настоящую беседу обо всём начнём, когда подключите нейросеть."),
    ]

    def __init__(self, cfg: Config):
        self.delay = float(cfg.get("brain.mock.delay", 0.02))
        self.enabled = bool(cfg.get("brain.mock.enabled", True))

    def available(self) -> bool:
        return self.enabled

    def stream(self, system: str, messages: list[ChatMessage], **options: Any) -> Iterator[str]:
        question = ""
        for m in reversed(messages):
            if m.role == "user":
                question = m.content
                break
        text = self._answer(question)
        for i, word in enumerate(text.split(" ")):
            if i:
                yield " "
            yield word
            if self.delay:
                time.sleep(self.delay)

    def _answer(self, question: str) -> str:
        q = (question or "").lower().replace("ё", "е")
        for pattern, answer in self._CANNED:
            if re.search(pattern, q):
                return answer
        if not q.strip():
            return "Я вас слушаю."
        return (
            "Сейчас я в демо-режиме и не могу рассуждать по-настоящему. "
            f"Вы спросили: «{question.strip()}». Подключите бесплатный Groq через свой "
            "релей (docs/GROQ.md) или GigaChat в .env — и я отвечу умно, коротко и по делу. "
            "Все бытовые команды при этом работают уже сейчас."
        )
