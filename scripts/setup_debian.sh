#!/usr/bin/env bash
# Установка Гуля на Debian 12/13 (без графики).
# Запуск: bash scripts/setup_debian.sh
# Флаг --no-systemd — не регистрировать службу автозапуска.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "${EUID}" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

echo "== 1/7. Системные пакеты =="
$SUDO apt-get update
$SUDO apt-get install -y \
  python3 python3-venv python3-pip \
  alsa-utils ffmpeg mpv espeak-ng \
  unzip curl ca-certificates git

echo "== 2/7. Виртуальное окружение Python =="
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip wheel
pip install -r requirements.txt

echo "== 3/7. Модель распознавания речи =="
bash scripts/download_models.sh

echo "== 4/7. Конфигурация =="
[[ -f config.yaml ]] || cp config.example.yaml config.yaml
[[ -f .env ]] || cp .env.example .env

echo "== 5/7. Доступ к звуковым устройствам =="
$SUDO usermod -aG audio "${USER}" 2>/dev/null || true

echo "== 6/7. Служба автозапуска =="
if [[ "${1:-}" != "--no-systemd" ]]; then
  sed "s|__PROJECT__|${ROOT}|g; s|__USER__|${USER}|g" systemd/gul.service \
    | $SUDO tee /etc/systemd/system/gul.service >/dev/null
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable gul.service
  echo "Служба установлена (автозапуск включён)."
else
  echo "Пропущено по флагу --no-systemd."
fi

echo "== 7/7. Проверка =="
.venv/bin/python run.py --check || true

cat <<'MSG'

Готово! Осталось три шага:

  1. Получите бесплатный ключ GigaChat (физлицо, Freemium):
     https://developers.sber.ru/portal/products/gigachat-api
     Впишите GIGACHAT_CLIENT_ID и GIGACHAT_CLIENT_SECRET в файл .env:
         nano .env

  2. Проверьте микрофон и колонку:
         python run.py --list-devices
         python run.py --bench

  3. Запустите:
         python run.py                # голос + веб
         sudo systemctl start gul     # если установили службу

Веб-интерфейс: http://IP-НЕТТОПА:8787
MSG
