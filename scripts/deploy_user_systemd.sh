#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
APP_ENV_DIR="${HOME}/.config/blueprint-re"

mkdir -p "${SYSTEMD_USER_DIR}" "${APP_ENV_DIR}"

python3 -m venv "${ROOT_DIR}/.venv/backend"
"${ROOT_DIR}/.venv/backend/bin/pip" install --upgrade pip
"${ROOT_DIR}/.venv/backend/bin/pip" install -e "${ROOT_DIR}/backend"

cat > "${APP_ENV_DIR}/backend.env" <<EOF
BACKEND_HOST=127.0.0.1
BACKEND_PORT=18001
BLUEPRINT_FRONTEND_ORIGIN=http://127.0.0.1:13001
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
# standalone mode requires static assets to be linked manually
ln -sfn "${ROOT_DIR}/frontend/.next/static" "${ROOT_DIR}/frontend/.next/standalone/frontend/.next/static"
popd >/dev/null

sed "s|__ROOT__|${ROOT_DIR}|g" "${ROOT_DIR}/deploy/systemd/blueprint-re-backend.service" > "${SYSTEMD_USER_DIR}/blueprint-re-backend.service"
sed "s|__ROOT__|${ROOT_DIR}|g" "${ROOT_DIR}/deploy/systemd/blueprint-re-frontend.service" > "${SYSTEMD_USER_DIR}/blueprint-re-frontend.service"

systemctl --user daemon-reload
systemctl --user enable blueprint-re-backend.service
systemctl --user enable blueprint-re-frontend.service
systemctl --user restart blueprint-re-backend.service
systemctl --user restart blueprint-re-frontend.service

echo "Blueprint RE deployed."
echo "Frontend: http://127.0.0.1:13001"
echo "Backend:  http://127.0.0.1:18001"
