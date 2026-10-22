"""Базовые типы для LLM-провайдеров."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator


@dataclass
class ChatMessage:
    role: str  # system | user | assistant
    content: str


class LLMError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, retryable: bool = True):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class LLMProvider:
    name = "base"
    title = "LLM"

    def available(self) -> bool:
        raise NotImplementedError

    def stream(self, system: str, messages: list[ChatMessage], **options: Any) -> Iterator[str]:
        raise NotImplementedError

    def complete(self, system: str, messages: list[ChatMessage], **options: Any) -> str:
        return "".join(self.stream(system, messages, **options))


def iter_sse_content(lines: Iterable[bytes | str]) -> Iterator[str]:
    """Достаёт текст из SSE-потока OpenAI-совместимых API.

    Терпим к разным форматам: data: {...}, пустые строки, [DONE].
    """
    for raw in lines:
        if isinstance(raw, bytes):
            line = raw.decode("utf-8", errors="ignore")
        else:
            line = raw
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("event:"):
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line == "[DONE]":
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        choices = payload.get("choices") or []
        if not choices:
            continue
        choice = choices[0] or {}
        delta = choice.get("delta") or {}
        content = delta.get("content")
        if content:
            yield content
            continue
        # Некоторые провайдеры в стриме отдают message целиком.
        message = choice.get("message") or {}
        if isinstance(message.get("content"), str):
            yield message["content"]


def extract_error(resp: Any) -> str:
    try:
        data = resp.json()
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                return err.get("message") or json.dumps(err, ensure_ascii=False)[:300]
            if isinstance(err, str):
                return err
            if data.get("message"):
                return str(data["message"])
    except Exception:
        pass
    try:
        return (resp.text or "")[:300]
    except Exception:
        return f"HTTP {getattr(resp, 'status_code', '?')}"
