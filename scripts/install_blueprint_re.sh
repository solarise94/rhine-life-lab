#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

prompt_default() {
  local var_name="$1"
  local prompt_text="$2"
  local default_value="$3"
  local current_value="${!var_name:-}"
  if [[ -n "${current_value}" ]]; then
    printf "%s [%s]: " "${prompt_text}" "${current_value}"
    read -r input || true
    if [[ -n "${input}" ]]; then
      printf -v "${var_name}" "%s" "${input}"
    fi
    return
  fi
  if [[ -n "${default_value}" ]]; then
    printf "%s [%s]: " "${prompt_text}" "${default_value}"
  else
    printf "%s: " "${prompt_text}"
  fi
  read -r input || true
  if [[ -n "${input}" ]]; then
    printf -v "${var_name}" "%s" "${input}"
  else
    printf -v "${var_name}" "%s" "${default_value}"
  fi
}

write_env_file() {
  umask 077
  cat > "${ENV_FILE}" <<EOF
BLUEPRINT_DEEPSEEK_API_BASE_URL=${BLUEPRINT_DEEPSEEK_API_BASE_URL}
BLUEPRINT_DEEPSEEK_API_KEY=${BLUEPRINT_DEEPSEEK_API_KEY}
BLUEPRINT_PI_DEEPSEEK_BASE_URL=${BLUEPRINT_PI_DEEPSEEK_BASE_URL}
BLUEPRINT_MANAGER_MODEL=${BLUEPRINT_MANAGER_MODEL}
BLUEPRINT_MANAGER_BACKEND=pi
BLUEPRINT_PI_MANAGER_URL=${BLUEPRINT_PI_MANAGER_URL}
BLUEPRINT_BACKEND_API_BASE_URL=${BLUEPRINT_BACKEND_API_BASE_URL}
BLUEPRINT_INTERNAL_TOOL_TOKEN=${BLUEPRINT_INTERNAL_TOOL_TOKEN}
BLUEPRINT_MANAGER_TEMPERATURE=${BLUEPRINT_MANAGER_TEMPERATURE}
BLUEPRINT_MANAGER_MAX_TOKENS=${BLUEPRINT_MANAGER_MAX_TOKENS}
BLUEPRINT_MANAGER_TIMEOUT_SECONDS=${BLUEPRINT_MANAGER_TIMEOUT_SECONDS}
BLUEPRINT_DEFAULT_WORKER_TYPE=${BLUEPRINT_DEFAULT_WORKER_TYPE}
BLUEPRINT_EXECUTOR_MODEL=${BLUEPRINT_EXECUTOR_MODEL}
BLUEPRINT_REVIEWER_MODEL=${BLUEPRINT_REVIEWER_MODEL}
BLUEPRINT_REVIEWER_MAX_TOKENS=${BLUEPRINT_REVIEWER_MAX_TOKENS}
BLUEPRINT_REVIEWER_MAX_TURNS=${BLUEPRINT_REVIEWER_MAX_TURNS}
BLUEPRINT_EXECUTOR_SANDBOX_MODE=${BLUEPRINT_EXECUTOR_SANDBOX_MODE}
BLUEPRINT_EXECUTOR_MAX_CONCURRENT_RUNS=${BLUEPRINT_EXECUTOR_MAX_CONCURRENT_RUNS}
BLUEPRINT_EXECUTOR_CONDA_BASE=${BLUEPRINT_EXECUTOR_CONDA_BASE}
BLUEPRINT_EXECUTOR_HOST_ROOT_READONLY=${BLUEPRINT_EXECUTOR_HOST_ROOT_READONLY}
BLUEPRINT_EXECUTOR_EXTRA_RO_BINDS=${BLUEPRINT_EXECUTOR_EXTRA_RO_BINDS}
MANAGER_WEBSEARCH_ENABLED=${MANAGER_WEBSEARCH_ENABLED}
TAVILY_API_KEY=${TAVILY_API_KEY}
TAVILY_BASE_URL=${TAVILY_BASE_URL}
MANAGER_CONTEXT_WINDOW_TOKENS=${MANAGER_CONTEXT_WINDOW_TOKENS}
MANAGER_COMPACTION_ENABLED=${MANAGER_COMPACTION_ENABLED}
MANAGER_COMPACTION_KEEP_RECENT_TOKENS=${MANAGER_COMPACTION_KEEP_RECENT_TOKENS}
MANAGER_COMPACTION_RESERVE_TOKENS=${MANAGER_COMPACTION_RESERVE_TOKENS}
EOF
}

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

printf "Blueprint RE installer\n"
printf "Root: %s\n\n" "${ROOT_DIR}"

prompt_default BLUEPRINT_DEEPSEEK_API_KEY "DeepSeek API key" "${BLUEPRINT_DEEPSEEK_API_KEY:-}"
if [[ -z "${BLUEPRINT_DEEPSEEK_API_KEY:-}" ]]; then
  echo "DeepSeek API key is required." >&2
  exit 1
fi

prompt_default BLUEPRINT_DEEPSEEK_API_BASE_URL "DeepSeek Anthropic-compatible base URL" "${BLUEPRINT_DEEPSEEK_API_BASE_URL:-https://api.deepseek.com/anthropic}"
prompt_default BLUEPRINT_PI_DEEPSEEK_BASE_URL "DeepSeek native base URL for Pi executor" "${BLUEPRINT_PI_DEEPSEEK_BASE_URL:-https://api.deepseek.com}"
prompt_default BLUEPRINT_MANAGER_MODEL "Manager model" "${BLUEPRINT_MANAGER_MODEL:-deepseek-v4-pro}"
prompt_default BLUEPRINT_EXECUTOR_MODEL "Executor model" "${BLUEPRINT_EXECUTOR_MODEL:-deepseek-v4-flash}"
prompt_default BLUEPRINT_REVIEWER_MODEL "Reviewer model" "${BLUEPRINT_REVIEWER_MODEL:-deepseek-v4-flash}"
prompt_default BLUEPRINT_REVIEWER_MAX_TOKENS "Reviewer max tokens" "${BLUEPRINT_REVIEWER_MAX_TOKENS:-2400}"
prompt_default BLUEPRINT_REVIEWER_MAX_TURNS "Reviewer max turns" "${BLUEPRINT_REVIEWER_MAX_TURNS:-24}"
prompt_default BLUEPRINT_MANAGER_TEMPERATURE "Manager temperature" "${BLUEPRINT_MANAGER_TEMPERATURE:-0.2}"
prompt_default BLUEPRINT_MANAGER_MAX_TOKENS "Manager max tokens" "${BLUEPRINT_MANAGER_MAX_TOKENS:-2400}"
prompt_default BLUEPRINT_MANAGER_TIMEOUT_SECONDS "Manager timeout seconds" "${BLUEPRINT_MANAGER_TIMEOUT_SECONDS:-600}"
prompt_default BLUEPRINT_DEFAULT_WORKER_TYPE "Default worker type" "${BLUEPRINT_DEFAULT_WORKER_TYPE:-pi}"
prompt_default BLUEPRINT_EXECUTOR_SANDBOX_MODE "Executor sandbox mode" "${BLUEPRINT_EXECUTOR_SANDBOX_MODE:-bwrap}"
prompt_default BLUEPRINT_EXECUTOR_MAX_CONCURRENT_RUNS "Max concurrent executor runs" "${BLUEPRINT_EXECUTOR_MAX_CONCURRENT_RUNS:-3}"
prompt_default BLUEPRINT_EXECUTOR_CONDA_BASE "Conda base path" "${BLUEPRINT_EXECUTOR_CONDA_BASE:-/home/${USER}/miniconda3}"
prompt_default BLUEPRINT_EXECUTOR_HOST_ROOT_READONLY "Host root read-only" "${BLUEPRINT_EXECUTOR_HOST_ROOT_READONLY:-true}"
prompt_default BLUEPRINT_EXECUTOR_EXTRA_RO_BINDS "Extra read-only binds (comma-separated)" "${BLUEPRINT_EXECUTOR_EXTRA_RO_BINDS:-/home/${USER}/.nvm,/home/${USER}/.local}"
prompt_default MANAGER_WEBSEARCH_ENABLED "Enable manager web search (true/false)" "${MANAGER_WEBSEARCH_ENABLED:-false}"
if [[ "${MANAGER_WEBSEARCH_ENABLED}" == "true" ]]; then
  prompt_default TAVILY_API_KEY "Tavily API key" "${TAVILY_API_KEY:-}"
else
  TAVILY_API_KEY="${TAVILY_API_KEY:-}"
fi
prompt_default TAVILY_BASE_URL "Tavily base URL" "${TAVILY_BASE_URL:-https://api.tavily.com}"
prompt_default MANAGER_CONTEXT_WINDOW_TOKENS "Manager context window tokens" "${MANAGER_CONTEXT_WINDOW_TOKENS:-1000000}"
prompt_default MANAGER_COMPACTION_ENABLED "Enable automatic compaction (true/false)" "${MANAGER_COMPACTION_ENABLED:-true}"
prompt_default MANAGER_COMPACTION_KEEP_RECENT_TOKENS "Compaction keep-recent tokens" "${MANAGER_COMPACTION_KEEP_RECENT_TOKENS:-120000}"
prompt_default MANAGER_COMPACTION_RESERVE_TOKENS "Compaction reserve tokens" "${MANAGER_COMPACTION_RESERVE_TOKENS:-16000}"

BLUEPRINT_INTERNAL_TOOL_TOKEN="${BLUEPRINT_INTERNAL_TOOL_TOKEN:-$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)}"

BLUEPRINT_PI_MANAGER_URL="${BLUEPRINT_PI_MANAGER_URL:-http://127.0.0.1:18002}"
BLUEPRINT_BACKEND_API_BASE_URL="${BLUEPRINT_BACKEND_API_BASE_URL:-http://127.0.0.1:18001/api}"

write_env_file

echo
echo "Wrote ${ENV_FILE}"
echo "Starting full deploy..."

bash "${ROOT_DIR}/scripts/deploy_user_systemd.sh"

echo
echo "Install complete."
echo "Frontend: http://127.0.0.1:13001"
echo "Backend:  http://127.0.0.1:18001"
