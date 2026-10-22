#!/usr/bin/env bash
# Тестовый стенд: тот же Гуль, тот же Debian, но на мощном ПК с рабочим столом.
#
#   bash scripts/install_testbox.sh
#   bash scripts/install_testbox.sh --with-piper --service
#
# Отличие от Acer только одно: здесь есть браузер и GUI, чтобы удобно было
# открыть веб-интерфейс и заодно проверить интернет. Код, звук, Vosk и TTS —
# ровно те же. Служба автозапуска по умолчанию НЕ ставится, чтобы ручной
# запуск не конфликтовал за порт 8787.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SERVICE=0
EXTRA=()
for arg in "$@"; do
  case "$arg" in
    --service) SERVICE=1 ;;
    --with-piper) EXTRA+=("--with-piper") ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Неизвестный флаг: $arg"; exit 2 ;;
  esac
done

if ! command -v apt-get >/dev/null; then
  echo "Нужен Debian/Ubuntu с apt. На Windows поставьте Debian на отдельный диск —"
  echo "это и есть «максимально приближенные условия». См. docs/DEVBOX.md."
  exit 1
fi

if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
  echo "Внимание: графическая сессия не обнаружена."
  echo "Если рабочий стол ещё не установлен, скрипт его поставит, но перезайти"
  echo "в графику нужно будет после перезагрузки."
  echo
fi

if [[ "$SERVICE" -eq 1 ]]; then
  echo "Устанавливаю со службой автозапуска."
  bash scripts/setup_debian.sh --desktop "${EXTRA[@]}"
else
  echo "Устанавливаю без службы: запускать вручную, чтобы не занимать порт."
  bash scripts/setup_debian.sh --desktop --no-systemd "${EXTRA[@]}"
fi

cat <<'MSG'

Стенд готов. Порядок отладки:

  1. Перезайдите в систему (нужны группы audio/pipewire) — или перезагрузитесь.
  2. .venv/bin/python run.py --doctor          # сравнить с Acer потом
  3. .venv/bin/python run.py --mic-test 4      # сказать фразу в микрофон
  4. .venv/bin/python run.py --speaker-test    # услышать ответ
  5. .venv/bin/python run.py --bench           # цифры до первого токена
  6. .venv/bin/python run.py                   # голос + веб
     Откройте http://127.0.0.1:8787 в Firefox.

Приблизить условия Acer (2 ядра, ~20% CPU):
     bash scripts/emulate_acer.sh .venv/bin/python run.py --web

Когда всё работает — перенос на неттоп:
     bash scripts/deploy_to_acer.sh

MSG
