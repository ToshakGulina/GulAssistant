#!/usr/bin/env bash
# Приблизить условия Acer R3700 на мощном ПК: 2 ядра и ограничение CPU.
#
#   bash scripts/emulate_acer.sh .venv/bin/python run.py --web
#   bash scripts/emulate_acer.sh --quota 15 .venv/bin/python run.py
#   bash scripts/emulate_acer.sh --net eth0 .venv/bin/python run.py
#
# Настройки по умолчанию:
#   --cpus 0-1     оставить два потока, как у Atom D525
#   --quota 20     ~20% CPU. D525 (1.8 ГГц, 2010) против 7800X3D (5 ГГц, 2022)
#                  слабее примерно в 5–15 раз по IPC и частоте.
#
# Это приближение, а не эмулятор: x86-инструкции те же, но AVX никуда не денется.
# Точные цифры всё равно даст только сам Acer. Зато «влезает ли в память»,
# «успевает ли Vosk» и «нет ли гонок» — видно уже здесь.
set -euo pipefail

CPUS="0-1"
QUOTA=20
NET_IFACE=""
ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cpus) CPUS="$2"; shift 2 ;;
    --quota) QUOTA="$2"; shift 2 ;;
    --net) NET_IFACE="$2"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --) shift; ARGS+=("$@"); break ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

if [[ ${#ARGS[@]} -eq 0 ]]; then
  echo "Укажите команду, например: bash scripts/emulate_acer.sh .venv/bin/python run.py --web"
  exit 2
fi

echo "Ограничиваю: CPU ${CPUS}, квота ${QUOTA}%."
if [[ -n "$NET_IFACE" ]]; then
  echo "Сетевая задержка (нужен root, действует на весь интерфейс):"
  echo "  sudo tc qdisc add dev ${NET_IFACE} root netem delay 60ms 20ms"
  echo "  снять: sudo tc qdisc del dev ${NET_IFACE} root"
fi
echo

USE_SYSTEMD_RUN=0
if command -v systemd-run >/dev/null && systemd-run --user --scope true >/dev/null 2>&1; then
  USE_SYSTEMD_RUN=1
fi

if [[ "$USE_SYSTEMD_RUN" -eq 1 ]]; then
  exec systemd-run --user --scope \
    -p "CPUQuota=${QUOTA}%" -p "AllowedCPUs=${CPUS}" \
    -- "${ARGS[@]}"
fi

TASKSET=""
if command -v taskset >/dev/null; then TASKSET="taskset -c ${CPUS}"; fi

if command -v cpulimit >/dev/null; then
  # shellcheck disable=SC2086
  exec cpulimit -l "${QUOTA}" -- ${TASKSET} "${ARGS[@]}"
fi

echo "Нет ни systemd-run, ни cpulimit. Ставлю только привязку к ядрам:"
echo "  sudo apt install cpulimit   # для ограничения по CPU"
if [[ -n "$TASKSET" ]]; then
  # shellcheck disable=SC2086
  exec ${TASKSET} "${ARGS[@]}"
fi

echo "taskset тоже не найден (apt install util-linux). Запускаю как есть."
exec "${ARGS[@]}"
