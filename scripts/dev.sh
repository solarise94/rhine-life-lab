#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REQUIRED_PYTHON_VERSION="3.13.0"
PYTHON_BIN="${PYTHON_BIN:-python3.13}"

version_gte() {
  local actual="$1"
  local required="$2"
  [[ "$(printf '%s\n%s\n' "${required}" "${actual}" | sort -V | head -n1)" == "${required}" ]]
}

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Python ${REQUIRED_PYTHON_VERSION}+ is required for local backend setup." >&2
  exit 1
fi

PYTHON_VERSION="$("${PYTHON_BIN}" -c 'import sys; print(".".join(str(part) for part in sys.version_info[:3]))')"
if ! version_gte "${PYTHON_VERSION}" "${REQUIRED_PYTHON_VERSION}"; then
  echo "Python ${REQUIRED_PYTHON_VERSION}+ is required for local backend setup. Current: ${PYTHON_VERSION}" >&2
  exit 1
fi

"${PYTHON_BIN}" -m venv "${ROOT_DIR}/.venv/backend"
"${ROOT_DIR}/.venv/backend/bin/pip" install -e "${ROOT_DIR}/backend"
"${ROOT_DIR}/.venv/backend/bin/python" "${ROOT_DIR}/scripts/generate_backend_schemas.py"

pushd "${ROOT_DIR}/frontend" >/dev/null
npm install
popd >/dev/null

echo "Start backend:  ${ROOT_DIR}/.venv/backend/bin/uvicorn app.main:app --app-dir ${ROOT_DIR}/backend --reload --host 127.0.0.1 --port 18001"
echo "Start frontend: cd ${ROOT_DIR}/frontend && NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:18001/api NEXT_PUBLIC_UPLOAD_API_BASE_URL=http://127.0.0.1:18001/api npm run dev"
