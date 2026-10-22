#!/usr/bin/env bash
# Установка Гуля на Debian 12/13 — одинаково на тестовом ПК и на Acer.
#
#   bash scripts/setup_debian.sh                  # серверная установка (как на Acer)
#   bash scripts/setup_debian.sh --desktop        # + рабочий стол, браузер, PipeWire
#   bash scripts/setup_debian.sh --no-systemd     # не ставить службу автозапуска
#   bash scripts/setup_debian.sh --with-piper     # + локальный нейроголос Piper
#   bash scripts/setup_debian.sh --desktop --with-piper
#
# Скрипт идемпотентен: повторный запуск ничего не ломает.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DESKTOP=0
SYSTEMD=1
PIPER=0
for arg in "$@"; do
  case "$arg" in
    --desktop) DESKTOP=1 ;;
    --no-systemd) SYSTEMD=0 ;;
    --with-piper) PIPER=1 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Неизвестный флаг: $arg (см. --help)"; exit 2 ;;
  esac
done

if [[ "${EUID}" -eq 0 ]]; then SUDO=""; else SUDO="sudo"; fi
if ! command -v apt-get >/dev/null; then
  echo "Этот скрипт для Debian/Ubuntu с apt. На другой системе ставьте пакеты вручную:"
  echo "  python3-venv alsa-utils ffmpeg mpv espeak-ng"
  exit 1
fi

echo "== 1/7. Системные пакеты =="
$SUDO apt-get update
BASE_PKGS=(
  python3 python3-venv python3-pip
  alsa-utils libasound2-plugins ffmpeg mpv espeak-ng
  unzip curl ca-certificates git
)
$SUDO apt-get install -y "${BASE_PKGS[@]}"

if [[ "$DESKTOP" -eq 1 ]]; then
  echo "   + рабочий стол XFCE, браузер и звуковой сервер PipeWire"
  # task-xfce-desktop есть в Debian; если нет (Ubuntu/производные) — ставим xfce4.
  $SUDO apt-get install -y task-xfce-desktop firefox-esr \
    pipewire-audio pipewire-alsa wireplumber pavucontrol dbus-x11 \
    || $SUDO apt-get install -y xfce4 xfce4-terminal firefox-esr \
         pipewire-audio pipewire-alsa wireplumber pavucontrol dbus-x11
fi

# RHVoice в Debian бывает не во всех репозиториях — не считаем это ошибкой.
echo "   + RHVoice (локальный быстрый голос), если доступен"
$SUDO apt-get install -y rhvoice rhvoice-russian \
  || echo "   RHVoice недоступен в репозитории — останутся Edge и espeak-ng."

echo "== 2/7. Виртуальное окружение Python =="
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip wheel
pip install --quiet -r requirements.txt
if [[ "$PIPER" -eq 1 ]]; then
  echo "   + Piper (может не собраться на старом Atom — это нормально)"
  pip install --quiet piper-tts || echo "   Piper не установился, продолжаем без него."
fi

echo "== 3/7. Модель распознавания речи =="
if [[ "$PIPER" -eq 1 ]]; then
  bash scripts/download_models.sh --piper
else
  bash scripts/download_models.sh
fi

echo "== 4/7. Конфигурация =="
[[ -f config.yaml ]] || cp config.example.yaml config.yaml
[[ -f .env ]] || cp .env.example .env
mkdir -p data

echo "== 5/7. Доступ к звуковым устройствам =="
$SUDO usermod -aG audio "${USER}" 2>/dev/null || true
if [[ "$DESKTOP" -eq 1 ]] && getent group pipewire >/dev/null 2>&1; then
  $SUDO usermod -aG pipewire "${USER}" 2>/dev/null || true
fi

echo "== 6/7. Служба автозапуска =="
if [[ "$SYSTEMD" -eq 1 ]] && [[ -d /run/systemd/system ]]; then
  sed "s|__PROJECT__|${ROOT}|g; s|__USER__|${USER}|g" systemd/gul.service \
    | $SUDO tee /etc/systemd/system/gul.service >/dev/null
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable gul.service
  echo "Служба установлена и включена в автозапуск."
else
  echo "Пропущено (--no-systemd). Запуск вручную: .venv/bin/python run.py"
fi

echo "== 7/7. Проверка =="
.venv/bin/python run.py --check || true

cat <<'MSG'

Готово. Дальше:

  1. Бесплатный ключ GigaChat (физлицо, Freemium):
     https://developers.sber.ru/portal/products/gigachat-api
     nano .env   →  GIGACHAT_CLIENT_ID и GIGACHAT_CLIENT_SECRET

  2. Проверка стенда (запускайте на обеих машинах):
     .venv/bin/python run.py --doctor
     .venv/bin/python run.py --mic-test 4
     .venv/bin/python run.py --speaker-test
     .venv/bin/python run.py --bench

  3. Запуск:
     .venv/bin/python run.py          # голос + веб
     sudo systemctl start gul         # если устанавливали службу

  Веб-интерфейс: http://IP-МАШИНЫ:8787

MSG
