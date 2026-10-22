#!/usr/bin/env bash
# Первичная настройка Гуля. Спрашивает только то, что нужно, и сразу проверяет.
#
#   bash scripts/quickstart.sh
#
# Можно запускать без ключей — тогда останется демо-мозг, а быстрые команды
# (время, таймеры, погода, заметки) будут работать полностью.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY=".venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "Нет .venv. Сначала: bash scripts/setup_debian.sh --desktop --no-systemd"
  exit 1
fi
mkdir -p data

echo "=============================================="
echo "  Гуль — быстрая настройка"
echo "=============================================="
echo
echo "Ключи можно вписать сейчас или позже в .env."
echo "Ничего не вводите — Enter: останется демо-режим."
echo

# --- .env: создаём из примера, если нет ---
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "Создан .env (права 600)."
fi

# Значение по умолчанию из текущего .env, чтобы повторный запуск не стирал.
current() {
  local key="$1"
  grep -E "^${key}=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true
}

set_env() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" .env; then
    # awk безопаснее sed: значение может содержать / и &
    awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="}
      $1==k {print k "=" v; next} {print}' .env > .env.tmp
    mv .env.tmp .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
  chmod 600 .env
}

echo "--- Groq (самый быстрый бесплатный мозг) ---"
echo "Из РФ напрямую не работает: нужен релей Cloudflare (docs/GROQ.md)"
echo "или VPN/прокси. Можно указать и то, и другое — релей важнее."
echo

read -r -p "URL релея [${GROQ_RELAY_URL:-$(current GROQ_RELAY_URL)}]: " relay_url || true
relay_url="${relay_url:-$(current GROQ_RELAY_URL)}"
read -r -p "Токен релея [оставьте пустым, чтобы не менять]: " relay_token || true
relay_token="${relay_token:-$(current GROQ_RELAY_TOKEN)}"
read -r -p "Ключ Groq (если без релея, через VPN) [${GROQ_API_KEY:+задан}]: " groq_key || true
groq_key="${groq_key:-$(current GROQ_API_KEY)}"

[[ -n "$relay_url" ]] && set_env GROQ_RELAY_URL "$relay_url"
[[ -n "$relay_token" ]] && set_env GROQ_RELAY_TOKEN "$relay_token"
[[ -n "$groq_key" ]] && set_env GROQ_API_KEY "$groq_key"

echo
echo "--- GigaChat (работает из РФ без VPN, 365 млн токенов в год) ---"
echo "Ключи: https://developers.sber.ru/portal/products/gigachat-api"
read -r -p "GIGACHAT_CLIENT_ID [${GIGACHAT_CLIENT_ID:+задан}]: " gc_id || true
gc_id="${gc_id:-$(current GIGACHAT_CLIENT_ID)}"
read -r -p "GIGACHAT_CLIENT_SECRET [оставьте пустым, чтобы не менять]: " gc_secret || true
gc_secret="${gc_secret:-$(current GIGACHAT_CLIENT_SECRET)}"
[[ -n "$gc_id" ]] && set_env GIGACHAT_CLIENT_ID "$gc_id"
[[ -n "$gc_secret" ]] && set_env GIGACHAT_CLIENT_SECRET "$gc_secret"

echo
echo "--- Прокси (нужен только для прямого Groq и Edge TTS) ---"
read -r -p "Прокси, например socks5h://127.0.0.1:1080 [Enter — пропустить]: " proxy || true
if [[ -n "$proxy" ]]; then
  "$PY" - "$proxy" <<'PY'
import sys
from pathlib import Path
import yaml

path = Path("config.yaml")
if not path.exists():
    path.write_text(Path("config.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
data.setdefault("network", {})["proxy"] = sys.argv[1]
path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
print(f"Прокси записан в config.yaml: {sys.argv[1]}")
PY
fi

echo
echo "--- Модель распознавания речи ---"
if [[ -f models/vosk-model-small-ru-0.22/conf/model.conf ]]; then
  echo "Модель Vosk уже на месте."
else
  bash scripts/download_models.sh || echo "Модель не скачалась — повторите позже: bash scripts/download_models.sh"
fi

echo
echo "--- Проверка мозгов ---"
"$PY" run.py --brain || true

echo
echo "--- Диагностика стенда ---"
"$PY" run.py --doctor || true

cat <<'MSG'

==============================================
Дальше:

  1. Микрофон:  .venv/bin/python run.py --mic-test 4
  2. Колонка:   .venv/bin/python run.py --speaker-test
  3. Задержки:  .venv/bin/python run.py --bench
  4. Запуск:    .venv/bin/python run.py
     Веб:      http://127.0.0.1:8787

Если Groq отвечает 403 — это гео-блок. Поднимите релей:
docs/GROQ.md (5 минут, бесплатно, VPN не нужен).
==============================================
MSG
