#!/usr/bin/env python3
"""Гуль — точка входа.

Примеры:
  python run.py                 # голос + веб-интерфейс
  python run.py --web           # только веб (без микрофона)
  python run.py --text          # текстовый чат в терминале
  python run.py --ask "погода"  # один вопрос
  python run.py --check         # проверить настройки и железо
  python run.py --bench         # измерить задержки
  python run.py --list-devices  # список микрофонов/колонок
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import socket
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from gul import __version__
from gul.assistant import Assistant
from gul.audio import AudioPlayer, list_alsa_devices
from gul.config import PROJECT_ROOT, Config, load_config
from gul.metrics import metrics
from gul.server import EventBus, WebServer
from gul.skills.timers import TimerManager
from gul.utils import cpu_info, mem_info, truncate

log = logging.getLogger("gul")


# --------------------------------------------------------------------------
# Служебное
# --------------------------------------------------------------------------

def setup_logging(cfg: Config) -> None:
    level = getattr(logging, str(cfg.get("logging.level", "INFO")).upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
               for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))
        root.addHandler(console)
    log_file = cfg.path(cfg.get("logging.file", "data/gul.log"))
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
            handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            root.addHandler(handler)
    except OSError:
        log.warning("Не удалось открыть лог-файл %s", log_file)


def init_project() -> int:
    example = PROJECT_ROOT / "config.example.yaml"
    target = PROJECT_ROOT / "config.yaml"
    if target.exists():
        print(f"Конфиг уже есть: {target}")
    else:
        shutil.copyfile(example, target)
        print(f"Создан конфиг: {target}")
    env_example = PROJECT_ROOT / ".env.example"
    env_target = PROJECT_ROOT / ".env"
    if not env_target.exists() and env_example.exists():
        shutil.copyfile(env_example, env_target)
        print(f"Создан файл ключей: {env_target}")
    print("\nДальше:")
    print("  1. Получите бесплатные ключи GigaChat: https://developers.sber.ru/portal/products/gigachat-api")
    print("  2. Впишите GIGACHAT_CLIENT_ID и GIGACHAT_CLIENT_SECRET в .env")
    print("  3. Запустите: python run.py --check")
    print("  4. Запустите: python run.py")
    return 0


def print_devices() -> int:
    devices = list_alsa_devices()
    if not devices["capture"] and not devices["playback"]:
        print("ALSA-утилиты не найдены. Установите: sudo apt install alsa-utils")
        return 1
    print("\nМикрофоны (arecord -l):")
    print("\n".join(devices["capture"]) or "  — не найдено")
    print("\nКолонки (aplay -l):")
    print("\n".join(devices["playback"]) or "  — не найдено")
    print("\nСовет: если микрофон не открывается, попробуйте device 'plughw:1,0'")
    print("(номер карты и устройства — из списка выше) в config.yaml → audio.input_device.")
    return 0


def check_config(cfg: Config) -> int:
    cpu = cpu_info()
    mem = mem_info()
    print(f"Гуль v{__version__}")
    print(f"Конфиг: {cfg.source}")
    print(f"CPU: {cpu['model']}")
    flags = []
    flags.append("AVX" if cpu["avx"] else "БЕЗ AVX")
    flags.append("SSE4.1" if cpu["sse4"] else "БЕЗ SSE4.1")
    print(f"Инструкции: {', '.join(flags)}")
    print(f"Память: {mem['total']} МБ всего, {mem['available']} МБ свободно")
    if not cpu["avx"] and not cpu["sse4"]:
        print("  ⚠ Atom поколения D525: локальные нейросети (torch/ctranslate2) могут не работать.")
        print("    Это нормально — все умные ответы идут в бесплатное облако.")
    print("\nКомпоненты:")
    print(f"  Микрофон arecord: {'да' if shutil.which('arecord') else 'НЕТ (apt install alsa-utils)'}")
    print(f"  Плеер aplay/ffplay/mpv: "
          f"{', '.join(x for x in ('aplay', 'ffplay', 'mpv') if shutil.which(x)) or 'НЕТ'}")
    model_path = cfg.path(cfg.get("stt.model_path", ""))
    print(f"  Модель Vosk: {'да' if model_path.exists() else 'НЕТ'} ({model_path})")
    from gul.tts import ENGINES

    for name, cls in ENGINES.items():
        engine = cls(cfg)
        mark = "да" if engine.available() else "нет"
        print(f"  TTS {name}: {mark}")
    print(f"  edge-tts: {'да' if _module_ok('edge_tts') else 'нет (pip install edge-tts)'}")
    print(f"  paho-mqtt: {'да' if _module_ok('paho') else 'нет (необязательно)'}")

    print("\nКлючи и мозги:")
    from gul.llm import build_router

    router = build_router(cfg)
    for provider in router.status():
        state = "готов" if provider["available"] else "нет ключа"
        print(f"  {provider['title']}: {state}")
    gc = cfg.section("brain").get("gigachat", {}) or {}
    print(f"  GigaChat client_id: {'задан' if gc.get('client_id') else 'НЕ ЗАДАН'}")
    groq = cfg.section("brain").get("groq", {}) or {}
    print(f"  Groq API key: {'задан' if groq.get('api_key') else 'не задан'}")

    warnings = cfg.warnings()
    if warnings:
        print("\nПредупреждения:")
        for warning in warnings:
            print(f"  • {warning}")
    print("\nПроверка завершена.")
    return 0


def _module_ok(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def run_bench(cfg: Config) -> int:
    cpu = cpu_info()
    print(f"Бенчмарк Гуля на {cpu['model']}")
    print(f"AVX: {'да' if cpu['avx'] else 'нет'} | SSE4.1: {'да' if cpu['sse4'] else 'нет'}")
    print("-" * 60)

    print("\n1. Синтез речи (фраза «Проверка синтеза речи», первый запуск — с прогревом):")
    from gul.tts import ENGINES

    for name, cls in ENGINES.items():
        engine = cls(cfg)
        if not engine.available():
            print(f"   {name:8} — недоступен")
            continue
        for attempt in (1, 2):
            started = time.perf_counter()
            try:
                path = engine.synthesize("Проверка синтеза речи")
                elapsed = (time.perf_counter() - started) * 1000
                print(f"   {name:8} — {elapsed:6.0f} мс" + (" (прогрев)" if attempt == 1 else ""))
                path.unlink(missing_ok=True)
            except Exception as exc:
                print(f"   {name:8} — ошибка: {exc}")
                break

    print("\n2. Нейросети (время до первого токена):")
    from gul.llm import ChatMessage, build_router

    router = build_router(cfg)
    for provider in router.providers:
        if not provider.available():
            print(f"   {provider.name:10} — нет ключа")
            continue
        started = time.perf_counter()
        chunks: list[str] = []
        try:
            for token in provider.stream(
                "Отвечай одним словом.",
                [ChatMessage("user", "Скажи ровно одно слово: привет")],
                max_tokens=20, temperature=0.1,
            ):
                if not chunks:
                    first = (time.perf_counter() - started) * 1000
                chunks.append(token)
                if len("".join(chunks)) > 30:
                    break
            total = (time.perf_counter() - started) * 1000
            print(f"   {provider.name:10} — первый токен {first:5.0f} мс, всего {total:5.0f} мс")
        except Exception as exc:
            print(f"   {provider.name:10} — ошибка: {truncate(str(exc), 120)}")

    print("\n3. Распознавание речи:")
    model_path = cfg.path(cfg.get("stt.model_path", ""))
    if not model_path.exists():
        print(f"   Модель не найдена: {model_path}")
    else:
        try:
            import json

            from gul.stt import VoskModel

            started = time.perf_counter()
            model = VoskModel.get(model_path)
            load_ms = (time.perf_counter() - started) * 1000
            import vosk

            rec = vosk.KaldiRecognizer(model, 16000)
            started = time.perf_counter()
            silence = b"\x00\x00" * 16000 * 2  # 2 секунды тишины
            rec.AcceptWaveform(silence)
            decode_ms = (time.perf_counter() - started) * 1000
            print(f"   Загрузка модели: {load_ms:.0f} мс")
            print(f"   Разбор 2 с тишины: {decode_ms:.0f} мс (запас ~{2000 / max(1, decode_ms):.0f}x)")
        except Exception as exc:
            print(f"   Ошибка: {exc}")

    print("\n4. Итоговая оценка задержки голосового ответа:")
    print("   распознавание идёт во время речи → 0 мс после паузы;")
    print("   + первый токен LLM (см. выше) + синтез первого предложения (см. выше).")
    print("   Бытовые команды (время, таймеры, заметки) — десятки миллисекунд.")
    print("\nГотово.")
    return 0


# --------------------------------------------------------------------------
# Режимы
# --------------------------------------------------------------------------

def ask_once(cfg: Config, text: str, speak: bool = False) -> int:
    assistant = Assistant(cfg, metrics=metrics, enable_audio=speak)
    try:
        started = time.perf_counter()
        final = None
        for event in assistant.respond_stream(text, source="cli", speak=False):
            if event["type"] == "delta":
                sys.stdout.write(event["text"])
                sys.stdout.flush()
            elif event["type"] == "skill":
                sys.stdout.write(event["text"])
                sys.stdout.flush()
            elif event["type"] == "done":
                final = event
        print()
        if final:
            elapsed = (time.perf_counter() - started) * 1000
            source = final.get("provider") or final.get("skill") or final.get("source")
            print(f"\n[{source} · {elapsed:.0f} мс]", file=sys.stderr)
        if speak and final:
            assistant.speaker.say(final.get("text", ""))
        return 0
    finally:
        assistant.close()


def text_chat(cfg: Config, speak: bool = False) -> int:
    assistant = Assistant(cfg, metrics=metrics, enable_audio=speak)
    print("Гуль на связи. Пустая строка — выход.")
    try:
        while True:
            try:
                line = input("Вы: ").strip()
            except EOFError:
                break
            if not line:
                break
            started = time.perf_counter()
            final = None
            for event in assistant.respond_stream(line, source="cli", speak=False):
                if event["type"] in ("delta", "skill"):
                    sys.stdout.write(event["text"])
                    sys.stdout.flush()
                elif event["type"] == "done":
                    final = event
            print()
            if final:
                elapsed = (time.perf_counter() - started) * 1000
                source = final.get("provider") or final.get("skill") or final.get("source")
                print(f"   [{source} · {elapsed:.0f} мс]")
            if speak and final:
                assistant.speaker.say(final.get("text", ""))
    except KeyboardInterrupt:
        print()
    finally:
        assistant.close()
    return 0


def serve(cfg: Config, voice: bool, web: bool) -> int:
    bus = EventBus()
    assistant = Assistant(cfg, metrics=metrics, on_event=bus.publish, enable_audio=voice)
    stop_event = threading.Event()
    server: WebServer | None = None
    voice_loop = None

    if web and cfg.get("web.enabled", True):
        server = WebServer(assistant, cfg, bus)
        try:
            server.start()
        except OSError as exc:
            log.error("Не удалось запустить веб-интерфейс на %s:%s — %s",
                      cfg.get("web.host"), cfg.get("web.port"), exc)
            server = None
        else:
            print(f"\nВеб-интерфейс: {server.url}")
            print(f"В локальной сети: http://{_lan_ip()}:{server.port}\n")

    if voice:
        from gul.voice import VoiceLoop

        voice_loop = VoiceLoop(assistant, cfg)

        def run_voice() -> None:
            try:
                voice_loop.run()
            except Exception as exc:
                log.error("Голосовой режим недоступен: %s", exc)
                log.error("Веб-интерфейс продолжает работать. Проверьте: python run.py --check")
                if not server:
                    stop_event.set()

        thread = threading.Thread(target=run_voice, name="voice", daemon=True)
        thread.start()
    elif not server:
        log.error("Нечего запускать: и голос, и веб отключены.")
        return 1

    def shutdown(signum=None, frame=None) -> None:
        log.info("Останавливаюсь…")
        stop_event.set()
        if voice_loop:
            voice_loop.stop()
        if server:
            server.stop()
        assistant.close()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        while not stop_event.wait(1.0):
            pass
    finally:
        shutdown()
    return 0


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except Exception:
        return "IP-адрес неттопа"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Гуль — бесплатный голосовой ассистент для слабого железа.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", help="путь к config.yaml")
    parser.add_argument("--init", action="store_true", help="создать config.yaml и .env")
    parser.add_argument("--check", action="store_true", help="проверить настройки и железо")
    parser.add_argument("--bench", action="store_true", help="измерить задержки")
    parser.add_argument("--list-devices", action="store_true", help="список микрофонов и колонок")
    parser.add_argument("--ask", metavar="ТЕКСТ", help="задать один вопрос")
    parser.add_argument("--text", action="store_true", help="текстовый чат в терминале")
    parser.add_argument("--web", action="store_true", help="только веб-интерфейс")
    parser.add_argument("--no-web", action="store_true", help="без веб-интерфейса")
    parser.add_argument("--speak", action="store_true", help="озвучивать в режимах --ask/--text")
    parser.add_argument("--say", metavar="ТЕКСТ", help="проверить синтез речи")
    parser.add_argument("--port", type=int, help="порт веб-интерфейса")
    parser.add_argument("--version", action="version", version=f"Гуль {__version__}")
    args = parser.parse_args(argv)

    if args.init:
        return init_project()

    cfg = load_config(args.config)
    if args.port:
        cfg.data.setdefault("web", {})["port"] = args.port
    setup_logging(cfg)

    if args.list_devices:
        return print_devices()
    if args.check:
        return check_config(cfg)
    if args.bench:
        return run_bench(cfg)
    if args.say:
        assistant = Assistant(cfg, metrics=metrics)
        try:
            ok = assistant.speaker.say(args.say)
            print("Озвучено." if ok else "Не удалось озвучить: нет доступного TTS-движка.")
            return 0 if ok else 2
        finally:
            assistant.close()
    if args.ask:
        return ask_once(cfg, args.ask, speak=args.speak)
    if args.text:
        return text_chat(cfg, speak=args.speak)

    warn = cfg.warnings()
    for warning in warn:
        log.warning(warning)

    voice = not args.web
    web = not args.no_web
    return serve(cfg, voice=voice, web=web)


if __name__ == "__main__":
    sys.exit(main())
