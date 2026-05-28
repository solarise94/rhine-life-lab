#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
APP_ENV_DIR="${HOME}/.config/blueprint-re"
APP_RELEASE_DIR="${HOME}/.local/share/blueprint-re"
FRONTEND_RELEASE_DIR="${APP_RELEASE_DIR}/frontend-release"
REQUIRED_PYTHON_VERSION="3.13.0"
REQUIRED_NODE_VERSION="22.19.0"
NODE_BIN=""
PYTHON_BIN=""
PI_BIN=""
OPENCODE_BIN=""
CLAUDE_BIN=""
DEPLOY_WARNINGS=()

version_gte() {
  local actual="$1"
  local required="$2"
  [[ "$(printf '%s\n%s\n' "${required}" "${actual}" | sort -V | head -n1)" == "${required}" ]]
}

python_version_of() {
  local python_bin="$1"
  "${python_bin}" -c 'import sys; print(".".join(str(part) for part in sys.version_info[:3]))'
}

find_python_bin() {
  local candidate
  local version
  for candidate in python3.13 python3; do
    if ! command -v "${candidate}" >/dev/null 2>&1; then
      continue
    fi
    version="$(python_version_of "${candidate}" 2>/dev/null || true)"
    if [[ -n "${version}" ]] && version_gte "${version}" "${REQUIRED_PYTHON_VERSION}"; then
      printf '%s\n' "$(command -v "${candidate}")"
      return 0
    fi
  done
  return 1
}

node_version_of() {
  local node_bin="$1"
  "${node_bin}" -p 'process.versions.node'
}

find_node_bin() {
  local candidate
  local version
  candidate="$(command -v node 2>/dev/null || true)"
  [[ -n "${candidate}" ]] || return 1
  version="$(node_version_of "${candidate}" 2>/dev/null || true)"
  if [[ -n "${version}" ]] && version_gte "${version}" "${REQUIRED_NODE_VERSION}"; then
    printf '%s\n' "${candidate}"
    return 0
  fi
  return 1
}

find_optional_bin() {
  local name="$1"
  command -v "${name}" 2>/dev/null || true
}

warn_deploy() {
  DEPLOY_WARNINGS+=("$1")
}

check_unknown_blueprint_env_keys() {
  local env_file="${ROOT_DIR}/.env"
  [[ -f "${env_file}" ]] || return 0
  local known_keys=(
    BLUEPRINT_BACKEND_API_BASE_URL
    BLUEPRINT_CLAUDE_CODE_COMMAND_JSON
    BLUEPRINT_CODEX_COMMAND_JSON
    BLUEPRINT_DEEPSEEK_API_BASE_URL
    BLUEPRINT_DEEPSEEK_API_KEY
    BLUEPRINT_DEFAULT_PYTHON_RUNTIME
    BLUEPRINT_DEFAULT_R_RUNTIME
    BLUEPRINT_DEFAULT_WORKER_TYPE
    BLUEPRINT_EXECUTOR_CONDA_BASE
    BLUEPRINT_EXECUTOR_EXTRA_RO_BINDS
    BLUEPRINT_EXECUTOR_HOST_ROOT_READONLY
    BLUEPRINT_EXECUTOR_MAX_CONCURRENT_RUNS
    BLUEPRINT_EXECUTOR_MODEL
    BLUEPRINT_EXECUTOR_SANDBOX_MODE
    BLUEPRINT_INTERNAL_TOOL_TOKEN
    BLUEPRINT_LIBRARY_SUMMARIZER_MODEL
    BLUEPRINT_MANAGER_BACKEND
    BLUEPRINT_MANAGER_MAX_TOKENS
    BLUEPRINT_MANAGER_MODEL
    BLUEPRINT_MANAGER_TEMPERATURE
    BLUEPRINT_MANAGER_TIMEOUT_SECONDS
    BLUEPRINT_OPENCODE_COMMAND_JSON
    BLUEPRINT_PI_COMMAND_JSON
    BLUEPRINT_PI_DEEPSEEK_BASE_URL
    BLUEPRINT_PI_MANAGER_URL
    BLUEPRINT_REVIEWER_MAX_TOKENS
    BLUEPRINT_REVIEWER_MAX_TURNS
    BLUEPRINT_REVIEWER_MODEL
  )
  local known_blob
  known_blob="$(printf '\n%s\n' "${known_keys[@]}")"
  local line key
  while IFS= read -r line; do
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -n "${line}" && "${line}" != \#* && "${line}" == BLUEPRINT_*"="* ]] || continue
    key="${line%%=*}"
    if [[ "${known_blob}" != *$'\n'"${key}"$'\n'* ]]; then
      warn_deploy "Unknown BLUEPRINT_* key in .env will not be written to managed env files: ${key}"
    fi
  done < "${env_file}"
}

detect_conda_base() {
  local candidates=(
    "${BLUEPRINT_EXECUTOR_CONDA_BASE:-}"
    "${CONDA_PREFIX:-}"
    "${HOME}/miniconda3"
    "${HOME}/miniforge3"
    "${HOME}/anaconda3"
    "/opt/conda"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    [[ -n "${candidate}" ]] || continue
    if [[ -x "${candidate}/bin/conda" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  return 1
}

detect_default_python_runtime() {
  local conda_base="$1"
  local env_name="${BLUEPRINT_DEFAULT_PYTHON_RUNTIME:-}"
  if [[ -n "${env_name}" ]]; then
    printf '%s\n' "${env_name}"
    return 0
  fi
  [[ -n "${conda_base}" ]] || return 1
  local candidates=(omicverse analysis base)
  local name
  for name in "${candidates[@]}"; do
    if [[ "${name}" == "base" && -x "${conda_base}/bin/python" ]]; then
      printf '%s\n' "base"
      return 0
    fi
    if [[ -x "${conda_base}/envs/${name}/bin/python" ]]; then
      printf '%s\n' "${name}"
      return 0
    fi
  done
  return 1
}

detect_default_r_runtime() {
  local conda_base="$1"
  local env_name="${BLUEPRINT_DEFAULT_R_RUNTIME:-}"
  if [[ -n "${env_name}" ]]; then
    printf '%s\n' "${env_name}"
    return 0
  fi
  if [[ -n "${conda_base}" ]]; then
    local candidates=(bioconductor r-bio base)
    local name
    for name in "${candidates[@]}"; do
      if [[ "${name}" == "base" && -x "${conda_base}/bin/Rscript" ]]; then
        printf '%s\n' "base"
        return 0
      fi
      if [[ -x "${conda_base}/envs/${name}/bin/Rscript" ]]; then
        printf '%s\n' "${name}"
        return 0
      fi
    done
  fi
  if command -v Rscript >/dev/null 2>&1; then
    printf '%s\n' "__system__"
    return 0
  fi
  return 1
}

mkdir -p "${SYSTEMD_USER_DIR}" "${APP_ENV_DIR}" "${APP_RELEASE_DIR}"

install_runtime_dependencies() {
  local missing_runtime=0
  local venv_python_bin=""
  for command_name in bwrap npm git systemctl; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
      missing_runtime=1
    fi
  done
  if ! venv_python_bin="$(find_python_bin 2>/dev/null)"; then
    missing_runtime=1
  elif ! "${venv_python_bin}" -m venv "${ROOT_DIR}/.venv/deploy-smoke" >/dev/null 2>&1; then
    missing_runtime=1
  fi
  rm -rf "${ROOT_DIR}/.venv/deploy-smoke"
  if [[ "${missing_runtime}" -eq 0 ]]; then
    return
  fi
  if command -v apt-get >/dev/null 2>&1; then
    if [[ "$(id -u)" -eq 0 ]]; then
      apt-get update
      apt-get install -y bubblewrap python3 python3-venv python3-pip nodejs npm git systemd
    elif command -v sudo >/dev/null 2>&1; then
      sudo apt-get update
      sudo apt-get install -y bubblewrap python3 python3-venv python3-pip nodejs npm git systemd
    else
      echo "Missing runtime dependencies from deploy/runtime-dependencies.yml and sudo is unavailable." >&2
      exit 1
    fi
  else
    echo "Automatic dependency installation currently supports apt-based hosts only." >&2
    echo "Install the required packages from deploy/runtime-dependencies.yml, then rerun this script." >&2
    exit 1
  fi
}

check_runtime_dependencies() {
  for command_name in bwrap npm git systemctl; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
      echo "Missing required runtime command: ${command_name}. See deploy/runtime-dependencies.yml." >&2
      exit 1
    fi
  done
  if ! find_python_bin >/dev/null 2>&1; then
    echo "Missing required Python ${REQUIRED_PYTHON_VERSION}+ runtime." >&2
    exit 1
  fi
  if ! bwrap \
    --die-with-parent \
    --ro-bind /usr /usr \
    --ro-bind /bin /bin \
    --ro-bind-try /lib /lib \
    --ro-bind-try /lib64 /lib64 \
    --proc /proc \
    --dev /dev \
    --tmpfs /tmp \
    -- /bin/true; then
    echo "bubblewrap smoke test failed. Fix host namespace/setuid support before deploying Blueprint executors." >&2
    exit 1
  fi
}

check_language_versions() {
  local python_version
  local node_version
  if ! PYTHON_BIN="$(find_python_bin)"; then
    echo "Python ${REQUIRED_PYTHON_VERSION}+ is required for the backend. Current python3: $(python3 --version 2>/dev/null || echo missing)" >&2
    exit 1
  fi
  python_version="$(python_version_of "${PYTHON_BIN}")"
  if ! NODE_BIN="$(find_node_bin)"; then
    echo "Node.js ${REQUIRED_NODE_VERSION}+ is required. Current node: $(node -v 2>/dev/null || echo missing)" >&2
    exit 1
  fi
  node_version="$(node_version_of "${NODE_BIN}")"
  PI_BIN="$(find_optional_bin pi)"
  OPENCODE_BIN="$(find_optional_bin opencode)"
  CLAUDE_BIN="$(find_optional_bin claude)"
  echo "Using Python ${python_version} via ${PYTHON_BIN}"
  echo "Using Node ${node_version} via ${NODE_BIN}"
  [[ -n "${PI_BIN}" ]] && echo "Pi CLI found: ${PI_BIN}" || echo "Pi CLI not found (optional)"
  [[ -n "${OPENCODE_BIN}" ]] && echo "OpenCode found: ${OPENCODE_BIN}" || echo "OpenCode not found (optional)"
  [[ -n "${CLAUDE_BIN}" ]] && echo "Claude Code found: ${CLAUDE_BIN}" || echo "Claude Code not found (optional)"
}

check_systemd_user() {
  if ! systemctl --user show-environment >/dev/null 2>&1; then
    echo "systemd --user is not available in the current session." >&2
    echo "Log into a full user session or enable a user manager before deploying Blueprint RE." >&2
    exit 1
  fi
}

install_runtime_dependencies
check_runtime_dependencies
check_language_versions
check_systemd_user

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi
check_unknown_blueprint_env_keys

CONDA_BASE=""
DEFAULT_PYTHON_RUNTIME=""
DEFAULT_R_RUNTIME=""
if CONDA_BASE="$(detect_conda_base 2>/dev/null)"; then
  DEFAULT_PYTHON_RUNTIME="$(detect_default_python_runtime "${CONDA_BASE}" 2>/dev/null || true)"
  DEFAULT_R_RUNTIME="$(detect_default_r_runtime "${CONDA_BASE}" 2>/dev/null || true)"
else
  warn_deploy "No conda base detected. Python/R runtime defaults may be empty unless explicitly configured."
  DEFAULT_R_RUNTIME="$(detect_default_r_runtime "" 2>/dev/null || true)"
fi
if [[ -z "${BLUEPRINT_DEFAULT_PYTHON_RUNTIME:-}" && -z "${DEFAULT_PYTHON_RUNTIME}" ]]; then
  warn_deploy "No default Python runtime detected. Cards without an explicit python_runtime may require manual runtime selection."
fi
if [[ -z "${BLUEPRINT_DEFAULT_R_RUNTIME:-}" && -z "${DEFAULT_R_RUNTIME}" ]]; then
  warn_deploy "No default R runtime detected. R-capable cards may require BLUEPRINT_DEFAULT_R_RUNTIME or system Rscript."
fi

"${PYTHON_BIN}" -m venv "${ROOT_DIR}/.venv/backend"
"${ROOT_DIR}/.venv/backend/bin/pip" install --upgrade pip
"${ROOT_DIR}/.venv/backend/bin/pip" install -e "${ROOT_DIR}/backend"

# Fail fast if critical manager key is missing in production deploy
if [[ -z "${BLUEPRINT_DEEPSEEK_API_KEY:-}" ]]; then
  echo "BLUEPRINT_DEEPSEEK_API_KEY is required for production deployment." >&2
  echo "Set it in .env or environment before running this script." >&2
  exit 1
fi

INTERNAL_TOOL_TOKEN="${BLUEPRINT_INTERNAL_TOOL_TOKEN:-$("${PYTHON_BIN}" - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)}"
DEFAULT_EXTRA_RO_BINDS="${HOME}/.nvm,${HOME}/.local"
DEFAULT_PI_COMMAND_JSON="[\"bash\",\"{repo_root}/scripts/blueprint_pi_launch.sh\",\"{executor_prompt_path}\"]"
DEFAULT_OPENCODE_COMMAND_JSON="[\"${OPENCODE_BIN:-opencode}\",\"run\",\"--file\",\"{executor_prompt_path}\",\"--format\",\"json\",\"--dangerously-skip-permissions\",\"Read {executor_prompt_path} and complete the Blueprint executor contract exactly.\"]"
DEFAULT_CLAUDE_CODE_COMMAND_JSON="[\"${CLAUDE_BIN:-claude}\",\"-p\",\"@{executor_prompt_path}\",\"--output-format\",\"stream-json\",\"--verbose\"]"

# Whitelist-based, single-write backend environment
# NOTE: this list is the runtime contract. When backend Settings adds a new
# deployment-relevant field, it must be added here so systemd services pick it up.
_write_env_once() {
  local file="$1"
  shift
  : > "${file}"
  for entry in "$@"; do
    printf '%s\n' "${entry}" >> "${file}"
  done
}

_write_env_once "${APP_ENV_DIR}/backend.env" \
  "PATH=$(dirname "${NODE_BIN}"):${HOME}/.local/bin:${CONDA_BASE}/bin:${PATH}" \
  "BACKEND_HOST=127.0.0.1" \
  "BACKEND_PORT=18001" \
  "BLUEPRINT_FRONTEND_ORIGIN=http://127.0.0.1:13001" \
  "BLUEPRINT_MANAGER_BACKEND=pi" \
  "BLUEPRINT_DEFAULT_WORKER_TYPE=pi" \
  "BLUEPRINT_PI_MANAGER_URL=http://127.0.0.1:18002" \
  "BLUEPRINT_BACKEND_API_BASE_URL=http://127.0.0.1:18001/api" \
  "BLUEPRINT_DEFAULT_PYTHON_RUNTIME=${BLUEPRINT_DEFAULT_PYTHON_RUNTIME:-${DEFAULT_PYTHON_RUNTIME}}" \
  "BLUEPRINT_DEFAULT_R_RUNTIME=${BLUEPRINT_DEFAULT_R_RUNTIME:-${DEFAULT_R_RUNTIME}}" \
  "BLUEPRINT_EXECUTOR_CONDA_BASE=${BLUEPRINT_EXECUTOR_CONDA_BASE:-${CONDA_BASE}}" \
  "BLUEPRINT_DEEPSEEK_API_KEY=${BLUEPRINT_DEEPSEEK_API_KEY}" \
  "BLUEPRINT_DEEPSEEK_API_BASE_URL=${BLUEPRINT_DEEPSEEK_API_BASE_URL:-https://api.deepseek.com/anthropic}" \
  "BLUEPRINT_PI_DEEPSEEK_BASE_URL=${BLUEPRINT_PI_DEEPSEEK_BASE_URL:-https://api.deepseek.com}" \
  "BLUEPRINT_MANAGER_MODEL=${BLUEPRINT_MANAGER_MODEL:-deepseek-v4-pro}" \
  "BLUEPRINT_MANAGER_TEMPERATURE=${BLUEPRINT_MANAGER_TEMPERATURE:-0.2}" \
  "BLUEPRINT_MANAGER_MAX_TOKENS=${BLUEPRINT_MANAGER_MAX_TOKENS:-2400}" \
  "BLUEPRINT_MANAGER_TIMEOUT_SECONDS=${BLUEPRINT_MANAGER_TIMEOUT_SECONDS:-600}" \
  "BLUEPRINT_EXECUTOR_MODEL=${BLUEPRINT_EXECUTOR_MODEL:-deepseek-v4-flash}" \
  "BLUEPRINT_REVIEWER_MODEL=${BLUEPRINT_REVIEWER_MODEL:-deepseek-v4-flash}" \
  "BLUEPRINT_LIBRARY_SUMMARIZER_MODEL=${BLUEPRINT_LIBRARY_SUMMARIZER_MODEL:-deepseek-v4-flash}" \
  "BLUEPRINT_REVIEWER_MAX_TOKENS=${BLUEPRINT_REVIEWER_MAX_TOKENS:-2400}" \
  "BLUEPRINT_REVIEWER_MAX_TURNS=${BLUEPRINT_REVIEWER_MAX_TURNS:-24}" \
  "BLUEPRINT_EXECUTOR_SANDBOX_MODE=${BLUEPRINT_EXECUTOR_SANDBOX_MODE:-bwrap}" \
  "BLUEPRINT_EXECUTOR_MAX_CONCURRENT_RUNS=${BLUEPRINT_EXECUTOR_MAX_CONCURRENT_RUNS:-3}" \
  "BLUEPRINT_EXECUTOR_HOST_ROOT_READONLY=${BLUEPRINT_EXECUTOR_HOST_ROOT_READONLY:-true}" \
  "BLUEPRINT_EXECUTOR_EXTRA_RO_BINDS=${BLUEPRINT_EXECUTOR_EXTRA_RO_BINDS-${DEFAULT_EXTRA_RO_BINDS}}" \
  "BLUEPRINT_INTERNAL_TOOL_TOKEN=${INTERNAL_TOOL_TOKEN}" \
  "BLUEPRINT_PI_COMMAND_JSON=${BLUEPRINT_PI_COMMAND_JSON:-${DEFAULT_PI_COMMAND_JSON}}" \
  "BLUEPRINT_OPENCODE_COMMAND_JSON=${BLUEPRINT_OPENCODE_COMMAND_JSON:-${DEFAULT_OPENCODE_COMMAND_JSON}}" \
  "BLUEPRINT_CLAUDE_CODE_COMMAND_JSON=${BLUEPRINT_CLAUDE_CODE_COMMAND_JSON:-${DEFAULT_CLAUDE_CODE_COMMAND_JSON}}"

# Codex is manual-only: if the operator added it to .env, forward it into
# backend.env so the managed deploy path does not silently drop it.
if [[ -n "${BLUEPRINT_CODEX_COMMAND_JSON:-}" ]]; then
  printf 'BLUEPRINT_CODEX_COMMAND_JSON=%s\n' "${BLUEPRINT_CODEX_COMMAND_JSON}" >> "${APP_ENV_DIR}/backend.env"
fi

cat > "${APP_ENV_DIR}/manager-agent.env" <<EOF
MANAGER_AGENT_HOST=127.0.0.1
MANAGER_AGENT_PORT=18002
MANAGER_AGENT_PROVIDER=deepseek
MANAGER_AGENT_MODEL=${BLUEPRINT_MANAGER_MODEL:-deepseek-v4-pro}
MANAGER_AGENT_TIMEOUT_MS=600000
BLUEPRINT_DEEPSEEK_API_KEY=${BLUEPRINT_DEEPSEEK_API_KEY}
BLUEPRINT_INTERNAL_TOOL_TOKEN=${INTERNAL_TOOL_TOKEN}
MANAGER_WEBSEARCH_ENABLED=${MANAGER_WEBSEARCH_ENABLED:-}
TAVILY_API_KEY=${TAVILY_API_KEY:-}
TAVILY_BASE_URL=${TAVILY_BASE_URL:-https://api.tavily.com}
MANAGER_CONTEXT_WINDOW_TOKENS=${MANAGER_CONTEXT_WINDOW_TOKENS:-1000000}
MANAGER_COMPACTION_ENABLED=${MANAGER_COMPACTION_ENABLED:-true}
MANAGER_COMPACTION_KEEP_RECENT_TOKENS=${MANAGER_COMPACTION_KEEP_RECENT_TOKENS:-120000}
MANAGER_COMPACTION_RESERVE_TOKENS=${MANAGER_COMPACTION_RESERVE_TOKENS:-16000}
EOF

cat > "${APP_ENV_DIR}/frontend.env" <<EOF
FRONTEND_HOST=127.0.0.1
FRONTEND_PORT=13001
NEXT_PUBLIC_API_BASE_URL=/api
BACKEND_PROXY_TARGET=http://127.0.0.1:18001
EOF

pushd "${ROOT_DIR}/frontend" >/dev/null
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi
set -a
source "${APP_ENV_DIR}/frontend.env"
set +a
npm run build
# Keep the deployed server isolated from repo-local builds so later `npm run build`
# calls do not invalidate the live chunk manifest under the running process.
rm -rf "${FRONTEND_RELEASE_DIR}"
mkdir -p "${FRONTEND_RELEASE_DIR}/frontend/.next"
cp -a "${ROOT_DIR}/frontend/.next/standalone/." "${FRONTEND_RELEASE_DIR}/"
rm -rf "${FRONTEND_RELEASE_DIR}/frontend/.next/static"
cp -a "${ROOT_DIR}/frontend/.next/static" "${FRONTEND_RELEASE_DIR}/frontend/.next/"
if [[ -d "${ROOT_DIR}/frontend/public" ]]; then
  cp -a "${ROOT_DIR}/frontend/public" "${FRONTEND_RELEASE_DIR}/frontend/"
fi
popd >/dev/null

pushd "${ROOT_DIR}/manager-agent" >/dev/null
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi
popd >/dev/null

sed -e "s|__ROOT__|${ROOT_DIR}|g" -e "s|__NODE_BIN__|${NODE_BIN}|g" "${ROOT_DIR}/deploy/systemd/blueprint-re-manager-agent.service" > "${SYSTEMD_USER_DIR}/blueprint-re-manager-agent.service"
sed -e "s|__ROOT__|${ROOT_DIR}|g" -e "s|__NODE_BIN__|${NODE_BIN}|g" "${ROOT_DIR}/deploy/systemd/blueprint-re-backend.service" > "${SYSTEMD_USER_DIR}/blueprint-re-backend.service"
sed -e "s|__ROOT__|${ROOT_DIR}|g" -e "s|__FRONTEND_RELEASE_DIR__|${FRONTEND_RELEASE_DIR}|g" -e "s|__NODE_BIN__|${NODE_BIN}|g" "${ROOT_DIR}/deploy/systemd/blueprint-re-frontend.service" > "${SYSTEMD_USER_DIR}/blueprint-re-frontend.service"

systemctl --user daemon-reload
systemctl --user enable blueprint-re-manager-agent.service
systemctl --user enable blueprint-re-backend.service
systemctl --user enable blueprint-re-frontend.service

# Stop the frontend before backend restart so Next.js proxy/SSE connections do
# not keep the old backend process alive during systemd's graceful shutdown.
systemctl --user stop blueprint-re-frontend.service || true
systemctl --user restart blueprint-re-manager-agent.service
systemctl --user restart blueprint-re-backend.service
systemctl --user start blueprint-re-frontend.service

echo "Blueprint RE deployed."
echo "Frontend: http://127.0.0.1:13001"
echo "Backend:  http://127.0.0.1:18001"
if [[ -n "${CONDA_BASE}" ]]; then
  echo "Conda base: ${CONDA_BASE}"
fi
if [[ -n "${DEFAULT_PYTHON_RUNTIME}" ]]; then
  echo "Default Python runtime: ${DEFAULT_PYTHON_RUNTIME}"
fi
if [[ -n "${DEFAULT_R_RUNTIME}" ]]; then
  echo "Default R runtime: ${DEFAULT_R_RUNTIME}"
fi
if [[ "${#DEPLOY_WARNINGS[@]}" -gt 0 ]]; then
  echo "Deploy warnings:"
  for warning in "${DEPLOY_WARNINGS[@]}"; do
    echo "  - ${warning}"
  done
fi
