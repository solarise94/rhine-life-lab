#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
APP_ENV_DIR="${HOME}/.config/blueprint-re"
APP_RELEASE_DIR="${HOME}/.local/share/blueprint-re"
FRONTEND_RELEASE_DIR="${APP_RELEASE_DIR}/frontend-release"
NODE_BIN="$(command -v node)"

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
  for command_name in bwrap python3 npm git systemctl; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
      missing_runtime=1
    fi
  done
  if ! python3 -m venv "${ROOT_DIR}/.venv/deploy-smoke" >/dev/null 2>&1; then
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
  for command_name in bwrap python3 npm git systemctl; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
      echo "Missing required runtime command: ${command_name}. See deploy/runtime-dependencies.yml." >&2
      exit 1
    fi
  done
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

check_node_version() {
  local node_major
  node_major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || true)"
  if [[ -z "${node_major}" || "${node_major}" -lt 18 ]]; then
    echo "Node.js 18+ is required. Current node: $(node -v 2>/dev/null || echo missing)" >&2
    exit 1
  fi
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
check_node_version
check_systemd_user

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi

CONDA_BASE=""
DEFAULT_PYTHON_RUNTIME=""
DEFAULT_R_RUNTIME=""
if CONDA_BASE="$(detect_conda_base 2>/dev/null)"; then
  DEFAULT_PYTHON_RUNTIME="$(detect_default_python_runtime "${CONDA_BASE}" 2>/dev/null || true)"
  DEFAULT_R_RUNTIME="$(detect_default_r_runtime "${CONDA_BASE}" 2>/dev/null || true)"
else
  DEFAULT_R_RUNTIME="$(detect_default_r_runtime "" 2>/dev/null || true)"
fi

python3 -m venv "${ROOT_DIR}/.venv/backend"
"${ROOT_DIR}/.venv/backend/bin/pip" install --upgrade pip
"${ROOT_DIR}/.venv/backend/bin/pip" install -e "${ROOT_DIR}/backend"

{
cat <<EOF
BACKEND_HOST=127.0.0.1
BACKEND_PORT=18001
BLUEPRINT_FRONTEND_ORIGIN=http://127.0.0.1:13001
BLUEPRINT_MANAGER_BACKEND=pi
BLUEPRINT_PI_MANAGER_URL=http://127.0.0.1:18002
BLUEPRINT_BACKEND_API_BASE_URL=http://127.0.0.1:18001/api
BLUEPRINT_DEFAULT_PYTHON_RUNTIME=${BLUEPRINT_DEFAULT_PYTHON_RUNTIME:-${DEFAULT_PYTHON_RUNTIME}}
BLUEPRINT_DEFAULT_R_RUNTIME=${BLUEPRINT_DEFAULT_R_RUNTIME:-${DEFAULT_R_RUNTIME}}
BLUEPRINT_EXECUTOR_CONDA_BASE=${BLUEPRINT_EXECUTOR_CONDA_BASE:-${CONDA_BASE}}
EOF
env | grep '^BLUEPRINT_' | sort || true
} > "${APP_ENV_DIR}/backend.env"

INTERNAL_TOOL_TOKEN="${BLUEPRINT_INTERNAL_TOOL_TOKEN:-$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)}"
if ! grep -q '^BLUEPRINT_INTERNAL_TOOL_TOKEN=' "${APP_ENV_DIR}/backend.env"; then
  echo "BLUEPRINT_INTERNAL_TOOL_TOKEN=${INTERNAL_TOOL_TOKEN}" >> "${APP_ENV_DIR}/backend.env"
fi

cat > "${APP_ENV_DIR}/manager-agent.env" <<EOF
MANAGER_AGENT_HOST=127.0.0.1
MANAGER_AGENT_PORT=18002
MANAGER_AGENT_PROVIDER=deepseek
MANAGER_AGENT_MODEL=${BLUEPRINT_MANAGER_MODEL:-deepseek-v4-pro}
MANAGER_AGENT_TIMEOUT_MS=600000
BLUEPRINT_DEEPSEEK_API_KEY=${BLUEPRINT_DEEPSEEK_API_KEY:-}
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
systemctl --user restart blueprint-re-manager-agent.service
systemctl --user restart blueprint-re-backend.service
systemctl --user restart blueprint-re-frontend.service

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
