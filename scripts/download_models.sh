#!/usr/bin/env bash
# Скачивание моделей. Vosk обязателен, Piper — по флагу --piper.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p models

MODEL="vosk-model-small-ru-0.22"
if [[ -d "models/${MODEL}" ]]; then
  echo "Модель Vosk уже на месте: models/${MODEL}"
else
  echo "Скачиваю ${MODEL} (~45 МБ)…"
  tmp="$(mktemp /tmp/${MODEL}.XXXXXX.zip)"
  curl -fL --retry 3 --connect-timeout 20 -o "${tmp}" \
    "https://alphacephei.com/vosk/models/${MODEL}.zip"
  unzip -q "${tmp}" -d models
  rm -f "${tmp}"
  echo "Готово: models/${MODEL}"
fi

if [[ "${1:-}" == "--piper" ]]; then
  mkdir -p models/piper
  base="https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitry/medium/ru_RU-dmitry-medium"
  if [[ ! -f models/piper/ru_RU-dmitry-medium.onnx ]]; then
    echo "Скачиваю голос Piper (dmitry, medium)…"
    curl -fL --retry 3 -o models/piper/ru_RU-dmitry-medium.onnx "${base}.onnx"
    curl -fL --retry 3 -o models/piper/ru_RU-dmitry-medium.onnx.json "${base}.onnx.json"
  fi
  echo "Piper-голос готов. Сам бинарник: pip install piper-tts (проверьте на своём CPU)."
fi

echo
echo "Если Vosk не скачался, возьмите модель вручную:"
echo "  https://alphacephei.com/vosk/models → vosk-model-small-ru-0.22"
echo "  и распакуйте в папку models/."
