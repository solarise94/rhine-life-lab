#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 -m venv "${ROOT_DIR}/.venv/backend"
"${ROOT_DIR}/.venv/backend/bin/pip" install -e "${ROOT_DIR}/backend"
"${ROOT_DIR}/.venv/backend/bin/python" "${ROOT_DIR}/scripts/generate_backend_schemas.py"

pushd "${ROOT_DIR}/frontend" >/dev/null
npm install
popd >/dev/null

echo "Start backend:  ${ROOT_DIR}/.venv/backend/bin/uvicorn app.main:app --app-dir ${ROOT_DIR}/backend --reload --host 127.0.0.1 --port 8000"
echo "Start frontend: cd ${ROOT_DIR}/frontend && NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000/api npm run dev"

