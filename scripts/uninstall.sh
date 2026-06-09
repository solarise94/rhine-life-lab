#!/usr/bin/env bash
set -euo pipefail

# Blueprint RE Uninstaller
#
# Stops and disables user services, removes release files, and optionally
# preserves or removes configuration.
#
# Usage:
#   bash uninstall.sh [--purge-config] [--yes]

RELEASE_BASE="${HOME}/.local/share/blueprint-re"
APP_ENV_DIR="${HOME}/.config/blueprint-re"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

PURGE_CONFIG=0
ASSUME_YES=0

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

for arg in "$@"; do
  case "${arg}" in
    --purge-config)
      PURGE_CONFIG=1
      ;;
    --yes)
      ASSUME_YES=1
      ;;
    --help|-h)
      cat <<'EOF'
Usage: bash uninstall.sh [OPTIONS]

Options:
  --purge-config  Also remove ~/.config/blueprint-re/
  --yes           Do not prompt for confirmation
  --help          Show this message
EOF
      exit 0
      ;;
    *)
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------

if [[ "${ASSUME_YES}" -eq 0 ]]; then
  echo "This will uninstall Blueprint RE from your user account."
  echo ""
  echo "The following will be REMOVED:"
  echo "  - User systemd services: blueprint-re-*"
  echo "  - Release directory:     ${RELEASE_BASE}"
  if [[ "${PURGE_CONFIG}" -eq 1 ]]; then
    echo "  - Configuration:         ${APP_ENV_DIR}"
  else
    echo "  - Configuration:         ${APP_ENV_DIR} (preserved, use --purge-config to remove)"
  fi
  echo ""
  echo -n "Are you sure? [y/N] "
  read -r response
  if [[ "${response}" != "y" && "${response}" != "Y" ]]; then
    echo "Uninstall cancelled."
    exit 0
  fi
fi

# ---------------------------------------------------------------------------
# Stop and disable services
# ---------------------------------------------------------------------------

echo "Stopping services..."

for svc in blueprint-re-nginx blueprint-re-frontend blueprint-re-backend blueprint-re-manager-agent; do
  if systemctl --user list-unit-files "${svc}.service" >/dev/null 2>&1; then
    systemctl --user stop "${svc}.service" 2>/dev/null || true
    systemctl --user disable "${svc}.service" 2>/dev/null || true
  fi
done

systemctl --user daemon-reload 2>/dev/null || true

# ---------------------------------------------------------------------------
# Remove service units
# ---------------------------------------------------------------------------

echo "Removing service units..."

for svc in blueprint-re-nginx.service blueprint-re-frontend.service blueprint-re-backend.service blueprint-re-manager-agent.service; do
  unit_file="${SYSTEMD_USER_DIR}/${svc}"
  if [[ -f "${unit_file}" ]]; then
    rm -f "${unit_file}"
  fi
done

# ---------------------------------------------------------------------------
# Remove release directory
# ---------------------------------------------------------------------------

echo "Removing release directory: ${RELEASE_BASE}"
if [[ -d "${RELEASE_BASE}" ]]; then
  rm -rf "${RELEASE_BASE}"
fi

# ---------------------------------------------------------------------------
# Optionally remove configuration
# ---------------------------------------------------------------------------

if [[ "${PURGE_CONFIG}" -eq 1 ]]; then
  echo "Removing configuration: ${APP_ENV_DIR}"
  if [[ -d "${APP_ENV_DIR}" ]]; then
    rm -rf "${APP_ENV_DIR}"
  fi
else
  echo "Configuration preserved at: ${APP_ENV_DIR}"
fi

echo ""
echo "Blueprint RE has been uninstalled."
