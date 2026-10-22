import json
import urllib.error
import urllib.request

import pytest

from gul.assistant import Assistant
from gul.server import WebServer


def http(method, url, payload=None, timeout=15):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


@pytest.fixture()
def web(cfg):
    assistant = Assistant(cfg, enable_audio=False)
    server = WebServer(assistant, cfg)
    server.start()
    yield server, assistant
    server.stop()
    assistant.close()


def test_index(web):
    server, _ = web
    with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=10) as response:
        body = response.read().decode()
    assert response.status == 200
    assert "Гуль" in body
    assert "/app.js" in body


def test_status(web):
    server, _ = web
    status, body = http("GET", f"http://127.0.0.1:{server.port}/api/status")
    data = json.loads(body)
    assert status == 200
    assert data["name"] == "Гуль"
    assert data["providers"]
    assert data["skills"]
    assert data["machine"]["host"]


def test_chat_skill(web):
    server, _ = web
    status, body = http(
        "POST", f"http://127.0.0.1:{server.port}/api/chat", {"text": "который час"}
    )
    assert status == 200
    lines = [json.loads(line) for line in body.decode().splitlines() if line.strip()]
    kinds = [line["type"] for line in lines]
    assert "skill" in kinds
    assert "done" in kinds
    done = [line for line in lines if line["type"] == "done"][-1]
    assert done["skill"] == "time"


def test_chat_mock_llm(web):
    server, _ = web
    _, body = http(
        "POST", f"http://127.0.0.1:{server.port}/api/chat", {"text": "расскажи про космос"}
    )
    lines = [json.loads(line) for line in body.decode().splitlines() if line.strip()]
    done = [line for line in lines if line["type"] == "done"][-1]
    assert done["provider"] == "mock"
    assert done["text"]


def test_stop_and_clear(web):
    server, assistant = web
    status, _ = http("POST", f"http://127.0.0.1:{server.port}/api/stop", {})
    assert status == 200
    status, _ = http("POST", f"http://127.0.0.1:{server.port}/api/clear", {})
    assert status == 200


def test_speak_without_player(web):
    server, _ = web
    status, body = http(
        "POST", f"http://127.0.0.1:{server.port}/api/speak", {"text": "проверка"}
    )
    assert status in (200, 503)
