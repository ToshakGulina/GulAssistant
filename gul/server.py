"""Веб-интерфейс и API Гуля. Только стандартная библиотека + assistant.

GET  /                — интерфейс
GET  /api/status      — состояние (CPU, RAM, мозги, TTS, метрики)
GET  /api/skills      — список умений
GET  /api/events      — SSE-события (таймеры, ошибки, ответы)
POST /api/chat        — NDJSON-поток ответа
POST /api/speak       — озвучить текст (проверка голоса)
POST /api/stop        — остановить речь/музыку
POST /api/clear       — очистить историю диалога
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .assistant import Assistant
from .config import Config
from .utils import truncate

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
}


class EventBus:
    """Раздаёт события всем открытым SSE-клиентам."""

    def __init__(self):
        self._lock = threading.Lock()
        self._clients: set[queue.Queue] = set()
        self._closed = False

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self._clients.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._clients.discard(q)

    def publish(self, event: str, data: dict) -> None:
        if self._closed:
            return
        payload = {"event": event, "data": data, "ts": time.time()}
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.put_nowait(payload)
            except queue.Full:
                pass

    def close(self) -> None:
        self._closed = True
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.put_nowait(None)
            except queue.Full:
                pass


class WebServer:
    def __init__(self, assistant: Assistant, cfg: Config, bus: EventBus | None = None):
        self.assistant = assistant
        self.cfg = cfg
        self.bus = bus or EventBus()
        self.host = cfg.get("web.host", "0.0.0.0")
        self.port = int(cfg.get("web.port", 8787))
        self.token = str(cfg.get("web.token", "") or "")
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> "WebServer":
        handler = _make_handler(self)
        self.httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="web", daemon=True)
        self.thread.start()
        log.info("Веб-интерфейс: http://%s:%s", self.host, self.port)
        return self

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self.bus.close()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread:
            self.thread.join(timeout=3)

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "::") else self.host
        return f"http://{host}:{self.port}"

    # -- авторизация ------------------------------------------------------
    def authorized(self, headers, query: dict) -> bool:
        if not self.token:
            return True
        provided = headers.get("X-Gul-Token") or (query.get("token", [""])[0] if query else "")
        return provided == self.token


def _make_handler(app: WebServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"GulAssistant/{__version__}"
        protocol_version = "HTTP/1.1"

        # -- утилиты -----------------------------------------------------
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _query(self) -> dict:
            return parse_qs(urlparse(self.path).query)

        def _authorized(self) -> bool:
            return app.authorized(self.headers, self._query())

        def _json(self, status: int, data: dict) -> None:
            body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _unauthorized(self) -> None:
            self._json(401, {"error": "unauthorized", "hint": "Укажите токен из config.yaml"})

        def _read_json(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return {}
            if length <= 0 or length > 131072:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return {}

        def _headers_no_cache(self, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.close_connection = True

        # -- GET ---------------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in _STATIC:
                self._static(*_STATIC[path])
                return
            if not self._authorized():
                self._unauthorized()
                return
            if path == "/api/status":
                self._json(200, app.assistant.status())
                return
            if path == "/api/skills":
                self._json(200, {"skills": app.assistant.registry.public()})
                return
            if path == "/api/health":
                self._json(200, {"ok": True, "name": app.assistant.name,
                                 "version": app.assistant.status().get("version")})
                return
            if path == "/api/events":
                self._sse()
                return
            self._json(404, {"error": "not_found"})

        def _static(self, filename: str, content_type: str) -> None:
            file_path = (WEB_DIR / filename).resolve()
            if WEB_DIR.resolve() not in file_path.parents and file_path.parent != WEB_DIR.resolve():
                self._json(404, {"error": "not_found"})
                return
            try:
                body = file_path.read_bytes()
            except OSError:
                self._json(404, {"error": "not_found"})
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _sse(self) -> None:
            self._headers_no_cache("text/event-stream; charset=utf-8")
            client = app.bus.subscribe()
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while not app._stop.is_set():
                    try:
                        event = client.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    if event is None:
                        break
                    payload = json.dumps(event, ensure_ascii=False, default=str)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                app.bus.unsubscribe(client)

        # -- POST --------------------------------------------------------
        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if not self._authorized():
                self._unauthorized()
                return
            if path == "/api/chat":
                self._chat()
                return
            if path == "/api/speak":
                self._speak()
                return
            if path == "/api/stop":
                app.assistant.stop_speaking()
                self._json(200, {"ok": True})
                return
            if path == "/api/clear":
                app.assistant.clear_history()
                self._json(200, {"ok": True})
                return
            self._json(404, {"error": "not_found"})

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Gul-Token")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _chat(self) -> None:
            data = self._read_json()
            text = str(data.get("text", "")).strip()
            if not text:
                self._json(400, {"error": "empty_text"})
                return
            if len(text) > 2000:
                text = text[:2000]
            speak = bool(app.cfg.get("web.speak", False))
            self._headers_no_cache("application/x-ndjson; charset=utf-8")
            try:
                for event in app.assistant.respond_stream(text, source="web", speak=speak):
                    line = json.dumps(event, ensure_ascii=False, default=str)
                    self.wfile.write((line + "\n").encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                log.debug("Клиент отключился во время ответа")
            except Exception as exc:
                log.exception("Ошибка обработки запроса")
                try:
                    err = json.dumps({"type": "error", "text": str(exc)[:300]}, ensure_ascii=False)
                    self.wfile.write((err + "\n").encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    pass

        def _speak(self) -> None:
            data = self._read_json()
            text = str(data.get("text", "")).strip()
            if not text:
                self._json(400, {"error": "empty_text"})
                return
            if not app.assistant.speaker.any_available():
                self._json(503, {"error": "tts_unavailable",
                                 "hint": "Установите edge-tts, Piper, RHVoice или espeak-ng"})
                return
            if not app.assistant.player or not app.assistant.player.available():
                self._json(503, {"error": "player_unavailable",
                                 "hint": "Установите плеер: sudo apt install alsa-utils ffmpeg mpv"})
                return
            threading.Thread(target=app.assistant.speaker.say, args=(text[:500],),
                             daemon=True).start()
            self._json(200, {"ok": True, "queued": True})

    return Handler
