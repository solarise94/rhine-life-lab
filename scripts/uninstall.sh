#!/usr/bin/env bash
set -euo pipefail

# Blueprint RE Uninstaller
#
# Stops and disables user services, removes release files, and optionally
# preserves or removes configuration and persistent project data.
#
# Usage:
#   bash uninstall.sh [--purge-config] [--purge-data] [--yes]

RELEASE_BASE="${HOME}/.local/share/blueprint-re"
APP_ENV_DIR="${HOME}/.config/blueprint-re"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
DATA_DIR="${RELEASE_BASE}/data"

PURGE_CONFIG=0
PURGE_DATA=0
ASSUME_YES=0

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

for arg in "$@"; do
  case "${arg}" in
    --purge-config)
      PURGE_CONFIG=1
      ;;
    --purge-data)
      PURGE_DATA=1
      ;;
    --yes)
      ASSUME_YES=1
      ;;
    --help|-h)
      cat <<'EOF'
Usage: bash uninstall.sh [OPTIONS]

Options:
  --purge-config  Also remove ~/.config/blueprint-re/
  --purge-data    Also remove ~/.local/share/blueprint-re/data/
                  (contains persistent project state!)
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
  if [[ "${PURGE_DATA}" -eq 1 ]]; then
    echo "  - Project data:          ${DATA_DIR}  <<< PERSISTENT PROJECT STATE WILL BE DELETED"
  else
    echo "  - Project data:          ${DATA_DIR} (preserved by default, use --purge-data to remove)"
  fi
  echo ""
  if [[ "${PURGE_DATA}" -eq 1 ]]; then
    echo "WARNING: --purge-data will permanently delete all project data including:"
    echo "  - project registry"
    echo "  - project graphs and cards"
    echo "  - run history and results"
    echo ""
  fi
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
# Handle data directory before removing release tree
# ---------------------------------------------------------------------------

if [[ "${PURGE_DATA}" -eq 0 && -d "${DATA_DIR}" ]]; then
  # Move data out of the release base so it survives release directory removal.
  DATA_BACKUP="${HOME}/.local/share/blueprint-re-data-backup-$(date +%s)"
  mv "${DATA_DIR}" "${DATA_BACKUP}"
  echo "Project data preserved at: ${DATA_BACKUP}"
  echo "  (Move it back to ${DATA_DIR} before reinstalling if you want to keep projects.)"
fi

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
