#!/usr/bin/env bash
set -euo pipefail

prompt_path="${1:-${BLUEPRINT_EXECUTOR_PROMPT:-}}"
if [[ -z "${prompt_path}" ]]; then
  echo "BLUEPRINT pi launch error: missing executor prompt path." >&2
  exit 2
fi
auth_mode="${BLUEPRINT_AUTH_MODE:-project_api}"
if [[ "${auth_mode}" == "project_api" ]]; then
  if [[ -z "${BLUEPRINT_DEEPSEEK_API_KEY:-}" ]]; then
    echo "BLUEPRINT pi launch error: BLUEPRINT_DEEPSEEK_API_KEY is not configured." >&2
    exit 2
  fi
  export DEEPSEEK_API_KEY="${BLUEPRINT_DEEPSEEK_API_KEY}"
fi

prepend_path_dir() {
  local dir_path="$1"
  if [[ -z "${dir_path}" || ! -d "${dir_path}" ]]; then
    return 0
  fi
  case ":${PATH:-}:" in
    *":${dir_path}:"*) ;;
    *)
      if [[ -n "${PATH:-}" ]]; then
        export PATH="${dir_path}:${PATH}"
      else
        export PATH="${dir_path}"
      fi
      ;;
  esac
}

resolve_pi_bin() {
  local configured_bin="${BLUEPRINT_PI_BIN:-}"
  local candidate=""
  if [[ -n "${configured_bin}" ]]; then
    prepend_path_dir "$(dirname "${configured_bin}")"
    if [[ -x "${configured_bin}" ]]; then
      RESOLVED_PI_BIN="${configured_bin}"
      return 0
    fi
  fi

  if command -v pi >/dev/null 2>&1; then
    RESOLVED_PI_BIN="$(command -v pi)"
    return 0
  fi

  local home_dir="${HOME:-}"
  local xdg_bin_home="${XDG_BIN_HOME:-}"
  local -a literal_candidates=()
  if [[ -n "${xdg_bin_home}" ]]; then
    literal_candidates+=("${xdg_bin_home}/pi")
  fi
  if [[ -n "${home_dir}" ]]; then
    literal_candidates+=(
      "${home_dir}/.local/bin/pi"
      "${home_dir}/bin/pi"
    )
  fi
  literal_candidates+=(
    "/usr/local/bin/pi"
    "/opt/homebrew/bin/pi"
  )

  for candidate in "${literal_candidates[@]}"; do
    if [[ -x "${candidate}" ]]; then
      prepend_path_dir "$(dirname "${candidate}")"
      RESOLVED_PI_BIN="${candidate}"
      return 0
    fi
  done

  if [[ -n "${home_dir}" ]]; then
    local -a nvm_candidates=()
    shopt -s nullglob
    nvm_candidates=("${home_dir}/.nvm/versions/node/"*/bin/pi)
    shopt -u nullglob
    if (( ${#nvm_candidates[@]} > 0 )); then
      while IFS= read -r sorted_candidate; do
        candidate="${sorted_candidate}"
      done < <(printf '%s\n' "${nvm_candidates[@]}" | sort -V)
      if [[ -n "${candidate}" && -x "${candidate}" ]]; then
        prepend_path_dir "$(dirname "${candidate}")"
        RESOLVED_PI_BIN="${candidate}"
        return 0
      fi
    fi
  fi

  return 1
}

RESOLVED_PI_BIN=""
resolve_pi_bin || true
pi_bin="${RESOLVED_PI_BIN}"
if [[ -z "${pi_bin}" || ! -x "${pi_bin}" ]]; then
  echo "BLUEPRINT pi launch error: pi CLI not found. Checked BLUEPRINT_PI_BIN, PATH, ~/.nvm/versions/node/*/bin/pi, ~/.local/bin/pi, ~/bin/pi, /usr/local/bin/pi, and /opt/homebrew/bin/pi. Agent should configure BLUEPRINT_PI_BIN to the pi executable path, or run the managed pi install flow and retry. PATH=${PATH:-}" >&2
  exit 2
fi

run_dir="${BLUEPRINT_RUN_DIR:-}"
if [[ -n "${run_dir}" ]]; then
  run_key="$(basename "${run_dir}")"
  pi_state_root="${BLUEPRINT_PI_STATE_ROOT:-${run_dir}/state/pi}"
  export PI_CODING_AGENT_DIR="${PI_CODING_AGENT_DIR:-${pi_state_root}/agent}"
  export PI_CODING_AGENT_SESSION_DIR="${PI_CODING_AGENT_SESSION_DIR:-${pi_state_root}/sessions}"
  mkdir -p "${PI_CODING_AGENT_DIR}"
  mkdir -p "${PI_CODING_AGENT_SESSION_DIR}"
fi

if [[ "${auth_mode}" == "project_api" && -n "${PI_CODING_AGENT_DIR:-}" ]]; then
  pi_deepseek_base_url="${BLUEPRINT_PI_DEEPSEEK_BASE_URL:-https://api.deepseek.com}"
  python3 - "${PI_CODING_AGENT_DIR}/models.json" "${pi_deepseek_base_url}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
base_url = sys.argv[2].rstrip("/")
path.parent.mkdir(parents=True, exist_ok=True)
payload = {"providers": {"deepseek": {"baseUrl": base_url}}}
path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
PY
fi

skill_args=()
while IFS= read -r skill_path; do
  if [[ -n "${skill_path}" ]]; then
    skill_args+=(--skill "${skill_path}")
  fi
done < <(
  python3 - <<'PY'
import json
import os

raw = os.environ.get("BLUEPRINT_PI_SKILL_PATHS", "[]")
try:
    items = json.loads(raw)
except json.JSONDecodeError:
    items = []
if isinstance(items, list):
    for item in items:
        if item:
            print(str(item))
PY
)

provider_args=()
if [[ "${auth_mode}" == "project_api" ]]; then
  provider_args+=(
    --provider deepseek
    --model "${BLUEPRINT_EXECUTOR_MODEL:-${BLUEPRINT_MANAGER_MODEL:-deepseek-v4-pro}}"
  )
else
  if [[ -n "${BLUEPRINT_PI_NATIVE_PROVIDER:-}" ]]; then
    provider_args+=(--provider "${BLUEPRINT_PI_NATIVE_PROVIDER}")
  fi
  if [[ -n "${BLUEPRINT_PI_NATIVE_MODEL:-}" ]]; then
    provider_args+=(--model "${BLUEPRINT_PI_NATIVE_MODEL}")
  fi
fi

exec "${pi_bin}" \
  "${provider_args[@]}" \
  --no-session \
  --no-skills \
  --no-context-files \
  "${skill_args[@]}" \
  -p "@${prompt_path}"
