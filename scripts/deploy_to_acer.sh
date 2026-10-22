#!/usr/bin/env bash
# Перенос отлаженной сборки с тестового ПК на Acer.
#
#   bash scripts/deploy_to_acer.sh --host gul@192.168.1.50
#   bash scripts/deploy_to_acer.sh --host gul@acer.local --config --env --data
#   bash scripts/deploy_to_acer.sh --host gul@acer.local --dry-run
#
# Что делает:
#   1. rsync кода (без .venv, models, data, config.yaml, .env);
#   2. по желанию копирует проверенные config.yaml, .env и данные;
#   3. на Acer запускает setup_debian.sh (пакеты, модель, служба);
#   4. перезапускает службу и показывает --doctor.
#
# Acer должен быть доступен по SSH: ssh-copy-id gul@acer.local
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

HOST="${GUL_HOST:-}"
REMOTE_DIR="GulAssistant"
WITH_CONFIG=0
WITH_ENV=0
WITH_DATA=0
DRY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --dir) REMOTE_DIR="$2"; shift 2 ;;
    --config) WITH_CONFIG=1; shift ;;
    --env) WITH_ENV=1; shift ;;
    --data) WITH_DATA=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Неизвестный флаг: $1"; exit 2 ;;
  esac
done

if [[ -z "$HOST" ]]; then
  echo "Укажите адрес неттопа: bash scripts/deploy_to_acer.sh --host gul@acer.local"
  exit 2
fi

RSYNC=(rsync -az --human-readable --info=progress2
  --exclude '.venv/' --exclude 'models/' --exclude 'data/' --exclude '.git/'
  --exclude '__pycache__/' --exclude '.pytest_cache/' --exclude '*.pyc'
  --exclude 'config.yaml' --exclude '.env' --exclude '*.log')
[[ "$DRY" -eq 1 ]] && RSYNC+=(--dry-run)

echo "== 1/4. Код → ${HOST}:~/${REMOTE_DIR} =="
"${RSYNC[@]}" ./ "${HOST}:~/${REMOTE_DIR}/"

if [[ "$DRY" -eq 1 ]]; then
  echo "Сухой прогон: дальше ничего не делаю."
  exit 0
fi

if [[ "$WITH_CONFIG" -eq 1 && -f config.yaml ]]; then
  echo "== 2/4. Проверенный config.yaml =="
  rsync -az config.yaml "${HOST}:~/${REMOTE_DIR}/config.yaml"
else
  echo "== 2/4. config.yaml: на Acer останется свой (или создастся из примера) =="
fi
if [[ "$WITH_ENV" -eq 1 && -f .env ]]; then
  echo "   + .env с ключами"
  rsync -az .env "${HOST}:~/${REMOTE_DIR}/.env"
fi
if [[ "$WITH_DATA" -eq 1 && -d data ]]; then
  echo "   + данные (заметки, память, таймеры, кэши)"
  rsync -az --exclude '*.log' --exclude 'mic-test.wav' data/ "${HOST}:~/${REMOTE_DIR}/data/"
fi

echo "== 3/4. Установка на Acer =="
ssh "$HOST" "cd ~/${REMOTE_DIR} && bash scripts/setup_debian.sh"

echo "== 4/4. Перезапуск и проверка =="
ssh "$HOST" "sudo systemctl restart gul && sleep 3 && sudo systemctl --no-pager --lines=5 status gul || true"
ssh "$HOST" "cd ~/${REMOTE_DIR} && .venv/bin/python run.py --doctor || true"

cat <<MSG

Готово. Проверьте с этой машины:
  curl -s http://${HOST#*@}:8787/api/health

И голосом на самом неттопе:
  «Привет, Гуль, какая погода»

Если что-то не так:
  ssh ${HOST} 'journalctl -u gul -n 100 --no-pager'
MSG
