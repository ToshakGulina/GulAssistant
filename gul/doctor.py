"""Диагностика стенда: микрофон, колонка, «доктор» и сравнение машин.

Всё, что здесь есть, запускается руками и не участвует в обычном ответе,
поэтому не влияет на задержку. Задача модуля — чтобы переход «мой ПК → Acer»
не превращался в угадайку: одинаковые команды, одинаковый вывод, понятные
подсказки.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import sys
import time
from pathlib import Path

from gul.config import Config
from gul.utils import cpu_info, mem_info, truncate

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Микрофон
# --------------------------------------------------------------------------

def mic_test(cfg: Config, seconds: float = 4.0, keep: bool = True) -> int:
    """Записать N секунд и честно сказать, слышно ли речь."""
    from gul.audio import AudioError, analyze_pcm, pcm_to_wav, record_pcm, which

    if not which("arecord"):
        print("arecord не найден.")
        print("  Debian/Ubuntu: sudo apt install alsa-utils")
        print("  На Windows Гуль не работает: нужен Debian (или WSL — но звук там ненадёжен).")
        return 2

    device = str(cfg.get("audio.input_device", "default") or "default")
    print(f"Записываю {seconds:.0f} с с устройства «{device}»…")
    print("Скажите что-нибудь обычным голосом, как будете говорить с ассистентом.")
    try:
        pcm = record_pcm(seconds, device=device, sample_rate=int(cfg.get("audio.sample_rate", 16000)))
    except AudioError as exc:
        print(f"\nНе удалось записать: {exc}")
        print("\nЧто проверить:")
        print("  1. python run.py --list-devices — какие карты вообще есть")
        print("  2. попробуйте plughw:1,0 вместо default в audio.input_device")
        print("  3. alsamixer → F4 (Capture) → снять mute, поднять Gain")
        print("  4. группа audio: sudo usermod -aG audio $USER (и перезайти)")
        return 2

    info = analyze_pcm(pcm, sample_rate=int(cfg.get("audio.sample_rate", 16000)))
    if keep:
        path = cfg.path("data/mic-test.wav")
        try:
            pcm_to_wav(pcm, path, sample_rate=int(cfg.get("audio.sample_rate", 16000)))
        except OSError as exc:
            log.debug("Не удалось сохранить запись: %s", exc)
            path = None
    else:
        path = None

    print(f"\nУровень: RMS {info['rms']:.0f}, пик {info['peak']}, "
          f"шум {info['noise']:.0f}, порог речи {info['threshold']:.0f}")
    print(f"Речь в {info['speech_ratio'] * 100:.0f}% кадров, "
          f"клиппинг {info['clipped_ratio'] * 100:.1f}%")

    problems: list[str] = []
    if info["peak"] < 50:
        problems.append(
            "Микрофон молчит. Проверьте, что выбран верный вход (--list-devices), "
            "снят mute и поднят Capture в alsamixer."
        )
    elif info["peak"] < 500:
        problems.append(
            "Сигнал очень тихий. Поднимите Capture/Gain в alsamixer или придвиньте микрофон."
        )
    if info["clipped_ratio"] > 0.02:
        problems.append(
            "Перегрузка (клиппинг). Убавьте Capture/Boost — иначе Vosk плохо распознаёт."
        )
    if info["peak"] >= 500 and info["speech_ratio"] < 0.05:
        problems.append(
            "Пики есть, но речи не видно. Возможно, шумит вентилятор или вы молчали. "
            "Повторите тест и говорите громче."
        )

    if problems:
        print("\nПроблемы:")
        for item in problems:
            print(f"  • {item}")
    else:
        print("\n✔ Микрофон работает, речь уверенно выше шума.")

    if info["speech_ratio"] > 0.05:
        suggested = int(max(150, min(2000, info["noise"] * 2.2)))
        current = int(cfg.get("audio.vad_threshold", 0) or 0)
        if not current:
            print(f"  Подсказка: если ассистент ловит шум, поставьте "
                  f"audio.vad_threshold: {suggested} (сейчас авто).")
        else:
            print(f"  Сейчас в конфиге порог {current}, для этого микрофона подходит ≈{suggested}.")
    if path:
        print(f"\nЗапись сохранена: {path}")
        print(f"  Послушать: aplay {path}   (или откройте файл в браузере/плеере)")
    print("\nЭтот же тест запускайте на Acer — сравните цифры.")
    return 0 if not problems else 1


# --------------------------------------------------------------------------
# Колонка
# --------------------------------------------------------------------------

def speaker_test(cfg: Config) -> int:
    """Проверить звук ровно тем же путём, что и настоящий ответ."""
    from gul.assistant import Assistant

    if not shutil.which("aplay") and not shutil.which("ffplay") and not shutil.which("mpv"):
        print("Нет ни aplay, ни ffplay, ни mpv — воспроизводить нечем.")
        print("  Debian: sudo apt install alsa-utils ffmpeg mpv")
        return 2

    assistant = Assistant(cfg, enable_audio=True)
    try:
        player = assistant.player
        print(f"Плеер: {'да' if player.available() else 'нет'} "
              f"({', '.join(x for x in ('aplay', 'ffplay', 'mpv') if shutil.which(x)) or '—'})")
        engines = assistant.speaker.available_engines()
        print("TTS-движки: " + ", ".join(
            f"{e['name']}{'' if e['available'] else ' (нет)'}" for e in engines) or "нет")

        print("\n1. Сигнал «я вас услышала» (два тона)…")
        assistant.player.beep()
        time.sleep(0.3)

        phrase = "Проверка звука. Раз, два, три. Если вы это слышите, колонка работает."
        print(f"2. Фраза через TTS: «{phrase}»")
        started = time.perf_counter()
        ok = assistant.speaker.say(phrase)
        elapsed = (time.perf_counter() - started) * 1000
        if not ok:
            print("\nОзвучить не удалось: ни один TTS-движок не сработал.")
            print("  Быстрая проверка: sudo apt install espeak-ng && python run.py --say привет")
            return 2
        print(f"\n✔ Озвучено движком «{assistant.speaker.last_engine}» за {elapsed:.0f} мс.")
        print(f"  Первый звук обычно раньше: синтез идёт по предложениям.")
        return 0
    finally:
        assistant.close()


# --------------------------------------------------------------------------
# Доктор
# --------------------------------------------------------------------------

def _line(name: str, ok: bool | None, value: str = "") -> None:
    mark = "OK " if ok else ("!! " if ok is False else "—  ")
    print(f"  [{mark}] {name}" + (f": {value}" if value else ""))


def doctor(cfg: Config, network: bool = True) -> int:
    """Полная проверка стенда. Показывает то же, что увидит Acer после установки."""
    problems = 0
    print(f"Гуль: диагностика стенда")
    print(f"Машина: {platform.node()} · {platform.system()} {platform.release()} · "
          f"Python {platform.python_version()}")
    print(f"Конфиг: {cfg.source}")
    print()

    cpu = cpu_info()
    mem = mem_info()
    print("Система:")
    _line("CPU", True, f"{cpu['model']} · AVX {'да' if cpu['avx'] else 'нет'} · "
                       f"SSE4.1 {'да' if cpu['sse4'] else 'нет'}")
    _line("Память", True, f"{mem['available']} МБ свободно из {mem['total']} МБ")
    if not cpu["avx"] and not cpu["sse4"]:
        print("       Atom без AVX/SSE4 — локальные нейросети не пойдут, это ожидаемо.")
    init = os.path.exists("/run/systemd/system")
    _line("systemd", init, "есть" if init else "нет (не Debian? служба не установится)")
    if init:
        try:
            unit = Path("/etc/systemd/system/gul.service")
            _line("Служба gul.service", True if unit.exists() else None,
                  "установлена" if unit.exists() else
                  "не установлена (для стенда нормально, для Acer — нужна)")
        except OSError:
            pass
    print()

    print("Звук:")
    tools = {name: bool(shutil.which(name)) for name in ("arecord", "aplay", "ffplay", "mpv", "espeak-ng")}
    _line("arecord (микрофон)", tools["arecord"], "есть" if tools["arecord"] else "apt install alsa-utils")
    _line("aplay (колонка)", tools["aplay"] or bool(shutil.which("ffplay")) or bool(shutil.which("mpv")),
          ", ".join(n for n, ok in tools.items() if ok and n in ("aplay", "ffplay", "mpv")) or "нет плеера")
    # PipeWire/PulseAudio: на десктопе звук идёт через них, на Acer — напрямую ALSA.
    try:
        import subprocess

        server = subprocess.run(["bash", "-lc", "command -v pactl >/dev/null && pactl info 2>/dev/null | head -1"],
                                capture_output=True, text=True, timeout=4).stdout.strip()
    except Exception:
        server = ""
    _line("Звуковой сервер", None, server or "нет PulseAudio/PipeWire (чистый ALSA — как на Acer)")
    devices = None
    if tools["arecord"] or tools["aplay"]:
        from gul.audio import list_alsa_devices

        devices = list_alsa_devices()
        _line("Микрофоны", bool(devices["capture"]),
              f"{len(devices['capture'])} шт." if devices["capture"] else "не найдено")
        _line("Колонки", bool(devices["playback"]),
              f"{len(devices['playback'])} шт." if devices["playback"] else "не найдено")
        for line in devices["capture"][:4]:
            print(f"        {line[:110]}")
        if devices["capture"] and not tools["arecord"]:
            print("        (arecord отсутствует — список неполный)")
    if not tools["arecord"]:
        problems += 1
    print()

    print("Распознавание речи:")
    model_path = cfg.path(cfg.get("stt.model_path", ""))
    _line("Модель Vosk", model_path.exists(), str(model_path))
    if not model_path.exists():
        print("        bash scripts/download_models.sh")
        problems += 1
    try:
        import vosk  # noqa: F401

        _line("Пакет vosk", True, "импортируется")
    except Exception as exc:
        _line("Пакет vosk", False, truncate(str(exc), 80))
        problems += 1
    wake = cfg.get("assistant.wake_words") or ["привет гуль"]
    _line("Слова активации", True, ", ".join(f"«{w}»" for w in wake))
    print()

    print("Синтез речи:")
    from gul.tts import ENGINES

    order = cfg.get("tts.engines") or ["edge", "piper", "rhvoice", "espeak"]
    ready = []
    for name in order:
        cls = ENGINES.get(name)
        if not cls:
            continue
        engine = cls(cfg)
        ok = engine.available()
        if ok:
            ready.append(name)
        _line(name, ok, "готов" if ok else "нет (см. docs/HARDWARE.md)")
    if not ready:
        problems += 1
    print(f"  Порядок: {' → '.join(order)}")
    if "edge" in order and "edge" not in ready:
        print("        Edge TTS требует интернет и edge-tts: pip install edge-tts")
    if not any(name in ready for name in ("rhvoice", "piper", "espeak")):
        print("        Локального TTS нет: без интернета ассистент будет молчать.")
    print()

    print("Мозги:")
    from gul.llm import ChatMessage, build_router

    groq_cfg = cfg.section("brain").get("groq", {}) or {}
    if groq_cfg.get("relay_url"):
        print(f"  Groq через релей: {groq_cfg.get('relay_url')}")
    elif groq_cfg.get("api_key"):
        print(f"  Groq напрямую (прокси: {cfg.get('network.proxy') or 'нет'})")
    router = build_router(cfg)
    real: list = []
    for provider in router.providers:
        available = provider.available()
        if available and provider.name != "mock":
            real.append(provider)
        info = {}
        getter = getattr(provider, "info", None)
        if callable(getter):
            try:
                info = getter() or {}
            except Exception:
                info = {}
        details = provider.title
        if info.get("mode"):
            details += f" · {info['mode']}"
        if info.get("model"):
            details += f" · {info['model']}"
        _line(provider.name, available, details)
    if real and network:
        provider = real[0]
        print(f"\n  Проверяю первый доступный мозг ({provider.name}) реальным запросом…")
        started = time.perf_counter()
        answer = ""
        try:
            for token in provider.stream(
                "Отвечай одним словом.",
                [ChatMessage("user", "Скажи ровно одно слово: привет")],
                max_tokens=8, temperature=0.1,
            ):
                answer += token
                if len(answer) > 40:
                    break
            elapsed = (time.perf_counter() - started) * 1000
            print(f"  ✔ Ответ за {elapsed:.0f} мс до первого токена: «{truncate(answer.strip(), 60)}»")
        except Exception as exc:
            print(f"  ✖ Не отвечает: {truncate(str(exc), 160)}")
            print("    Проверьте ключ, интернет и VPN/прокси (см. docs/HARDWARE.md).")
            problems += 1
    elif not real:
        print("\n  Ключей нет: работает демо-мозг. Быстрые команды — работают полностью.")
        print("  GigaChat Freemium: https://developers.sber.ru/portal/products/gigachat-api")
        print("  Впишите GIGACHAT_CLIENT_ID и GIGACHAT_CLIENT_SECRET в .env")
    print()

    print("Веб:")
    host = cfg.get("web.host", "0.0.0.0")
    port = int(cfg.get("web.port", 8787))
    _line("Адрес", True, f"http://{host}:{port}")
    if cfg.get("web.token"):
        _line("Токен", True, "задан")
    else:
        _line("Токен", None, "не задан (нормально для домашней сети, плохо для интернета)")
    print()

    warnings = cfg.warnings()
    if warnings:
        print("Предупреждения конфига:")
        for warning in warnings:
            print(f"  • {warning}")
        print()

    if problems:
        print(f"Итог: {problems} проблем(ы). Подсказки — выше, подробности — docs/HARDWARE.md.")
        return 1
    print("Итог: критических проблем нет. Можно говорить.")
    return 0


def explain_error(name: str, exc: Exception) -> list[str]:
    """Человеческие подсказки к типовым ошибкам провайдеров."""
    status = getattr(exc, "status", None)
    text = str(exc).lower()
    hints: list[str] = []

    if status == 401:
        hints.append("Ключ неверный или отозван — перевыпустите его в кабинете провайдера.")
    elif status == 403:
        if name == "groq":
            hints.append(
                "403 у Groq почти всегда означает гео-блок РФ. Варианты: "
                "свой релей на Cloudflare (docs/GROQ.md), VPN/прокси в network.proxy, "
                "или оставить основным GigaChat."
            )
        else:
            hints.append("Доступ запрещён: проверьте ключ, тариф и регион.")
    elif status == 429:
        hints.append(
            "Лимит бесплатного тарифа. Groq free: 30 запросов/мин, 1000/день, "
            "8000 токенов/мин. Подождите или уменьшите brain.max_tokens и max_history_turns."
        )
    elif status == 404:
        hints.append("Модель или путь не найдены — проверьте brain.<провайдер>.model и base_url.")
    elif "сеть недоступна" in text or "timeout" in text or "timed out" in text:
        hints.append(
            "Сеть: проверьте интернет, прокси в network.proxy и доступность домена "
            "(с телефона по мобильному интернету тоже стоит проверить)."
        )
    if name == "groq" and not hints:
        hints.append("Проверьте ключ и доступность api.groq.com; для РФ нужен релей или прокси.")
    return hints


def _check_relay(relay_url: str) -> None:
    """Проверить /health релея, не раскрывая токен."""
    import requests

    root = relay_url.split("/openai/v1")[0].rstrip("/")
    try:
        response = requests.get(f"{root}/health", timeout=6)
        data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    except Exception as exc:
        print(f"Релей: недоступен ({exc.__class__.__name__}). Проверьте URL и доступность "
              f"Cloudflare; запасной путь — VPN + прямой Groq.")
        return
    if response.status_code != 200:
        print(f"Релей: HTTP {response.status_code} на /health — проверьте, что Worker задеплоен.")
        return
    if not data.get("key_configured"):
        print("Релей: отвечает, но GROQ_API_KEY не задан в секретах Worker.")
        return
    protected = "токен требуется" if data.get("token_required") else "БЕЗ токена — задайте RELAY_TOKEN!"
    print(f"Релей: OK ({protected})")


def brain_test(cfg: Config, only: str | None = None, prompt: str = "Скажи ровно одно слово: привет") -> int:
    """Проверить мозги реальным запросом: время до первого токена и диагноз."""
    from gul.llm import ChatMessage, build_router

    router = build_router(cfg)
    order = cfg.get("brain.order") or []
    print("Мозги Гуля")
    print(f"Порядок: {' → '.join(order) or 'не задан'}")
    groq = cfg.section("brain").get("groq", {}) or {}
    if groq.get("relay_url"):
        print(f"Groq: через релей {groq.get('relay_url')} (ключ Groq на этой машине не нужен)")
        if not only or only == "groq":
            _check_relay(str(groq.get("relay_url")))
    elif cfg.get("network.proxy"):
        print(f"Groq: напрямую через прокси {cfg.get('network.proxy')}")
    else:
        print("Groq: напрямую (из РФ вернёт 403 — нужен релей или прокси, см. docs/GROQ.md)")
    print(f"Модель Groq: {groq.get('model', 'openai/gpt-oss-20b')}")
    print()

    providers = [p for p in router.providers if not only or p.name == only]
    if only and not providers:
        print(f"Провайдер «{only}» не включён. Доступные: "
              + ", ".join(p.name for p in router.providers))
        return 2

    failures = 0
    for provider in providers:
        info = {}
        getter = getattr(provider, "info", None)
        if callable(getter):
            try:
                info = getter() or {}
            except Exception:
                info = {}
        suffix = ""
        if info.get("mode"):
            suffix += f" · режим: {info['mode']}"
        if info.get("model"):
            suffix += f" · {info['model']}"

        if not provider.available():
            print(f"[—] {provider.title}{suffix}: не настроен (нет ключа/URL)")
            continue

        started = time.perf_counter()
        answer = ""
        first_ms = None
        try:
            for token in provider.stream(
                "Отвечай максимально кратко.",
                [ChatMessage("user", prompt)],
                max_tokens=40, temperature=0.1,
            ):
                if first_ms is None:
                    first_ms = (time.perf_counter() - started) * 1000
                answer += token
                if len(answer) > 80:
                    break
        except Exception as exc:
            failures += 1
            print(f"[!!] {provider.title}{suffix}: ошибка — {truncate(str(exc), 160)}")
            for hint in explain_error(provider.name, exc):
                print(f"     → {hint}")
            continue

        total_ms = (time.perf_counter() - started) * 1000
        if not answer.strip():
            failures += 1
            print(f"[!!] {provider.title}{suffix}: пустой ответ (модель израсходовала "
                  f"лимит на рассуждения — reasoning_effort уже low, попробуйте max_tokens больше)")
            continue
        first_text = f"{first_ms:.0f} мс" if first_ms is not None else "—"
        print(f"[OK] {provider.title}{suffix}: первый токен {first_text}, всего {total_ms:.0f} мс")
        print(f"     «{truncate(' '.join(answer.split()), 70)}»")

    print()
    if failures:
        print(f"Итог: {failures} провайдер(ов) с ошибками. Гуль продолжит работать "
              f"на следующем доступном мозге.")
        return 1
    print("Итог: все настроенные мозги ответили.")
    return 0


def compare_hint() -> None:
    """Короткая памятка, зачем этот модуль нужен на двух машинах."""
    print(
        "Стенд и Acer должны вести себя одинаково.\n"
        "Запускайте на обеих машинах:\n"
        "  python run.py --doctor\n"
        "  python run.py --mic-test 4\n"
        "  python run.py --speaker-test\n"
        "  python run.py --bench\n"
        "и сравнивайте вывод. Разница в задержках ожидаема, разница в "
        "«работает / не работает» — это баг, который надо чинить до переезда.\n"
        f"Python: {sys.version.split()[0]}"
    )
