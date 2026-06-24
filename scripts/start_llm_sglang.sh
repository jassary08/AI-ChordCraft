#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

LLM_RUNTIME_ROOT="${CHORDCRAFT_LLM_RUNTIME_ROOT:-./third_party/MOSS-Music}"
SGLANG_WORKDIR="${CHORDCRAFT_SGLANG_WORKDIR:-${LLM_RUNTIME_ROOT}/sglang}"
INSTRUCT_MODEL_PATH="${CHORDCRAFT_SGLANG_INSTRUCT_MODEL_PATH:-${LLM_RUNTIME_ROOT}/model/MOSS-Music-8B-Instruct}"
INSTRUCT_PORT="${CHORDCRAFT_SGLANG_INSTRUCT_PORT:-30000}"
INSTRUCT_CUDA_VISIBLE_DEVICES="${CHORDCRAFT_SGLANG_INSTRUCT_CUDA_VISIBLE_DEVICES:-0}"
LOG_DIR="${CHORDCRAFT_LOG_DIR:-./logs}"

mkdir -p "${LOG_DIR}"

require_path() {
  local label="$1"
  local path="$2"
  if [ ! -e "${path}" ]; then
    echo "Missing ${label}: ${path}" >&2
    echo "Prepare third_party/ first, or set the corresponding CHORDCRAFT_* variable in .env." >&2
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

  echo "Starting ${name} LLM service"
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

if [ "$#" -gt 0 ]; then
  echo "This script now starts only the Instruct model; command-line modes are no longer used." >&2
  echo "Usage: bash scripts/start_llm_sglang.sh" >&2
  exit 2
fi

if command -v curl >/dev/null 2>&1; then
  if curl -fsS "http://127.0.0.1:${INSTRUCT_PORT}/model_info" >/dev/null 2>&1; then
    echo "SGLang Instruct service is already running on port ${INSTRUCT_PORT}."
    echo "Skip starting another copy to avoid duplicate GPU memory allocation."
    exit 0
  fi
fi

start_server "instruct" "${INSTRUCT_MODEL_PATH}" "${INSTRUCT_PORT}" "${INSTRUCT_CUDA_VISIBLE_DEVICES}"

echo
echo "SGLang startup commands were launched in the background."
echo "Check logs under: ${LOG_DIR}"
