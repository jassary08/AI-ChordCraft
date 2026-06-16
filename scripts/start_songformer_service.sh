#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export CHORDCRAFT_SONGFORMER_ROOT="${CHORDCRAFT_SONGFORMER_ROOT:-./third_party/SongFormer}"
export SONGFORMER_MODEL_NAME="${SONGFORMER_MODEL_NAME:-SongFormer}"
export SONGFORMER_CHECKPOINT="${SONGFORMER_CHECKPOINT:-src/SongFormer/ckpts/SongFormer.safetensors}"
export SONGFORMER_CONFIG="${SONGFORMER_CONFIG:-src/SongFormer/configs/SongFormer.yaml}"

python scripts/songformer_service.py
