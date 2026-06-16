#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PROJECT_ROOT="$(cd .. && pwd)"
DEFAULT_MOSS_MUSIC_ROOT="${PROJECT_ROOT}/MOSS-Music"

MOSS_MUSIC_ROOT="${CHORDCRAFT_MOSS_MUSIC_ROOT:-${DEFAULT_MOSS_MUSIC_ROOT}}"
SGLANG_WORKDIR="${CHORDCRAFT_SGLANG_WORKDIR:-${MOSS_MUSIC_ROOT}/sglang}"

THINKING_MODEL_PATH="${CHORDCRAFT_MOSS_THINKING_MODEL_PATH:-${MOSS_MUSIC_ROOT}/model/MOSS-Music-8B-Thinking}"
INSTRUCT_MODEL_PATH="${CHORDCRAFT_MOSS_INSTRUCT_MODEL_PATH:-${MOSS_MUSIC_ROOT}/model/MOSS-Music-8B-Instruct}"

THINKING_PORT="${CHORDCRAFT_MOSS_THINKING_PORT:-30000}"
INSTRUCT_PORT="${CHORDCRAFT_MOSS_INSTRUCT_PORT:-30001}"
THINKING_CUDA_VISIBLE_DEVICES="${CHORDCRAFT_MOSS_THINKING_CUDA_VISIBLE_DEVICES:-0}"
INSTRUCT_CUDA_VISIBLE_DEVICES="${CHORDCRAFT_MOSS_INSTRUCT_CUDA_VISIBLE_DEVICES:-1}"

# one | dual | thinking | instruct
MODE="${1:-${CHORDCRAFT_MOSS_SERVE_MODE:-dual}}"
LOG_DIR="${CHORDCRAFT_LOG_DIR:-${PWD}/logs}"
mkdir -p "${LOG_DIR}"

require_path() {
  local label="$1"
  local path="$2"
  if [ ! -e "${path}" ]; then
    echo "Missing ${label}: ${path}" >&2
    echo "Set the corresponding CHORDCRAFT_* variable in .env." >&2
    exit 1
  fi
}

start_server() {
  local name="$1"
  local model_path="$2"
  local port="$3"
  local cuda_devices="$4"
  local log_file="${LOG_DIR}/sglang-${name}-${port}.log"

  require_path "${name} model path" "${model_path}"

  echo "Starting ${name} model"
  echo "  model: ${model_path}"
  echo "  port:  ${port}"
  echo "  gpu:   ${cuda_devices}"
  echo "  log:   ${log_file}"

  (
    cd "${SGLANG_WORKDIR}"
    CUDA_VISIBLE_DEVICES="${cuda_devices}" sglang serve \
      --model-path "${model_path}" \
      --port "${port}" \
      --trust-remote-code \
      ${CHORDCRAFT_SGLANG_EXTRA_ARGS:-}
  ) >"${log_file}" 2>&1 &

  echo $! > "${LOG_DIR}/sglang-${name}-${port}.pid"
}

require_path "SGLang workdir" "${SGLANG_WORKDIR}"

case "${MODE}" in
  dual)
    start_server "thinking" "${THINKING_MODEL_PATH}" "${THINKING_PORT}" "${THINKING_CUDA_VISIBLE_DEVICES}"
    start_server "instruct" "${INSTRUCT_MODEL_PATH}" "${INSTRUCT_PORT}" "${INSTRUCT_CUDA_VISIBLE_DEVICES}"
    ;;
  one|instruct)
    start_server "instruct" "${INSTRUCT_MODEL_PATH}" "${INSTRUCT_PORT}" "${INSTRUCT_CUDA_VISIBLE_DEVICES}"
    ;;
  thinking)
    start_server "thinking" "${THINKING_MODEL_PATH}" "${THINKING_PORT}" "${THINKING_CUDA_VISIBLE_DEVICES}"
    ;;
  *)
    echo "Usage: bash scripts/start_moss_music_sglang.sh [dual|one|thinking|instruct]" >&2
    exit 2
    ;;
esac

echo
echo "SGLang startup commands were launched in the background."
echo "Check logs under: ${LOG_DIR}"

