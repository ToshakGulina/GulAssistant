#!/usr/bin/env bash
# Быстрые тесты без интернета и без микрофона.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ ! -x .venv/bin/python ]]; then
  echo "Сначала установите зависимости: bash scripts/setup_debian.sh --no-systemd"
  exit 1
fi
.venv/bin/python -m pytest -q
