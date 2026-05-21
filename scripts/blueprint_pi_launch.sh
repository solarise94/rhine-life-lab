#!/usr/bin/env bash
set -euo pipefail

prompt_path="${1:-${BLUEPRINT_EXECUTOR_PROMPT:-}}"
if [[ -z "${prompt_path}" ]]; then
  echo "BLUEPRINT pi launch error: missing executor prompt path." >&2
  exit 2
fi
if [[ -z "${BLUEPRINT_DEEPSEEK_API_KEY:-}" ]]; then
  echo "BLUEPRINT pi launch error: BLUEPRINT_DEEPSEEK_API_KEY is not configured." >&2
  exit 2
fi

export PATH="/home/solarise/.nvm/versions/node/v22.22.2/bin:${PATH:-}"
pi_bin="${BLUEPRINT_PI_BIN:-}"
if [[ -z "${pi_bin}" ]]; then
  if command -v pi >/dev/null 2>&1; then
    pi_bin="$(command -v pi)"
  elif [[ -x "/home/solarise/.nvm/versions/node/v22.22.2/bin/pi" ]]; then
    pi_bin="/home/solarise/.nvm/versions/node/v22.22.2/bin/pi"
  fi
fi
if [[ -z "${pi_bin}" || ! -x "${pi_bin}" ]]; then
  echo "BLUEPRINT pi launch error: pi CLI is not installed or executable. Set BLUEPRINT_PI_BIN or install pi under /home/solarise/.nvm/versions/node/v22.22.2/bin/pi. PATH=${PATH:-}" >&2
  exit 2
fi

run_dir="${BLUEPRINT_RUN_DIR:-}"
if [[ -n "${run_dir}" ]]; then
  run_key="$(basename "${run_dir}")"
  pi_state_root="${BLUEPRINT_PI_STATE_ROOT:-/tmp/blueprint-pi-agent/${run_key}}"
  export PI_CODING_AGENT_DIR="${PI_CODING_AGENT_DIR:-${pi_state_root}/agent}"
  export PI_CODING_AGENT_SESSION_DIR="${PI_CODING_AGENT_SESSION_DIR:-${pi_state_root}/sessions}"
  mkdir -p "${PI_CODING_AGENT_DIR}"
  mkdir -p "${PI_CODING_AGENT_SESSION_DIR}"
fi

exec "${pi_bin}" \
  --provider deepseek \
  --model "${BLUEPRINT_MANAGER_MODEL:-deepseek-v4-pro}" \
  --api-key "${BLUEPRINT_DEEPSEEK_API_KEY}" \
  --no-session \
  --no-context-files \
  -p "@${prompt_path}"
