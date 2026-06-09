#!/usr/bin/env bash
set -euo pipefail

# Blueprint RE Self-Extracting Installer
#
# This script is both the installer stub and the self-extracting archive.
# An appended tar.gz payload begins after the __PAYLOAD_START__ marker.
#
# Usage:
#   bash blueprint-re-<version>-linux-x86_64.sh [--offline] [--rollback <version>]
#
# Note: this self-extracting file contains an appended binary tar.gz payload.
# Do not install it via `curl | bash`; download the file first, then execute it.

# ---------------------------------------------------------------------------
# Installer metadata (populated at build time)
# ---------------------------------------------------------------------------
INSTALLER_VERSION="__INSTALLER_VERSION__"
INSTALLER_ARCH="x86_64"
INSTALLER_PLATFORM="linux"
# At build time this is zero-padded to a fixed width so the script length stays
# constant when the placeholder is replaced. Strip padding for use with tail.
PAYLOAD_OFFSET="__PAYLOAD_OFFSET__"
PAYLOAD_OFFSET="$((10#${PAYLOAD_OFFSET}))"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
RELEASE_BASE="${HOME}/.local/share/blueprint-re"
RELEASES_DIR="${RELEASE_BASE}/releases"
CURRENT_LINK="${RELEASE_BASE}/current"
ENV_DIR="${RELEASE_BASE}/env"
DATA_ROOT="${RELEASE_BASE}/data"
APP_ENV_DIR="${HOME}/.config/blueprint-re"

OFFLINE_MODE=0
ROLLBACK_VERSION=""
SKIP_VERIFY=0

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --offline)
        OFFLINE_MODE=1
        shift
        ;;
      --rollback)
        if [[ -n "${2:-}" ]]; then
          ROLLBACK_VERSION="$2"
          shift 2
        else
          echo "ERROR: --rollback requires a version argument." >&2
          exit 1
        fi
        ;;
      --skip-verify)
        SKIP_VERIFY=1
        shift
        ;;
      --help|-h)
        cat <<'EOF'
Usage: bash install.sh [OPTIONS]

Options:
  --offline          Fail if the embedded package cache is missing.
  --rollback VERSION Switch to a previous release version.
  --skip-verify      Skip payload checksum verification (not recommended).
  --help             Show this message.

Environment:
  BLUEPRINT_RELEASE_BASE  Override the default release directory.
EOF
        exit 0
        ;;
      *)
        echo "WARNING: Unknown argument: $1" >&2
        shift
        ;;
    esac
  done
}

parse_args "$@"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

die() {
  echo "ERROR: $1" >&2
  exit 1
}

info() {
  echo "[install] $1"
}

warn() {
  echo "[install] WARNING: $1" >&2
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    die "Required command not found: $1"
  fi
}

version_gte() {
  local actual="$1"
  local required="$2"
  [[ "$(printf '%s\n%s\n' "${required}" "${actual}" | sort -V | head -n1)" == "${required}" ]]
}

# Extract a top-level JSON string value without requiring Python.
# Only works for simple "key": "value" pairs on a single line.
json_get_string() {
  local file="$1"
  local key="$2"
  grep -E "\"${key}\"[[:space:]]*:[[:space:]]*\"" "${file}" 2>/dev/null | head -1 | sed -E 's/.*"'"${key}"'"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/'
}

# Export runtime binary env vars as a helper so normal deploy and rollback can
# share the same values.
export_runtime_bin_env() {
  export BLUEPRINT_RELEASE_ROOT="${CURRENT_LINK}"
  export BLUEPRINT_DATA_ROOT="${DATA_ROOT}"
  export BLUEPRINT_PYTHON_BIN="${ENV_PYTHON}"
  export BLUEPRINT_NODE_BIN="${ENV_NODE}"
  export BLUEPRINT_NPM_BIN="${ENV_NPM}"
  export BLUEPRINT_NGINX_BIN="${ENV_NGINX}"
  export BLUEPRINT_BWRAP_BIN="${ENV_BWRAP}"
}

# Run deploy_release.sh with the current runtime binary env and optional flags.
run_deploy() {
  local deploy_flags=("$@")
  export_runtime_bin_env
  bash "${VERSION_DIR}/scripts/deploy_release.sh" "${deploy_flags[@]}"
}

# Run deploy_release.sh for an existing release directory (used by rollback).
# The runtime environment is global (shared across releases), not per-release.
run_deploy_for_release() {
  local release_dir="$1"
  shift
  export BLUEPRINT_RELEASE_ROOT="${release_dir}"
  export BLUEPRINT_DATA_ROOT="${DATA_ROOT}"
  export BLUEPRINT_PYTHON_BIN="${ENV_DIR}/bin/python"
  export BLUEPRINT_NODE_BIN="${ENV_DIR}/bin/node"
  export BLUEPRINT_NPM_BIN="${ENV_DIR}/bin/npm"
  export BLUEPRINT_NGINX_BIN="${ENV_DIR}/bin/nginx"
  export BLUEPRINT_BWRAP_BIN="${ENV_DIR}/bin/bwrap"
  bash "${release_dir}/scripts/deploy_release.sh" "$@"
}

# Wait for backend/nginx health endpoints to come up.
wait_for_health() {
  local timeout_secs=30
  local deadline=$(( $(date +%s) + timeout_secs ))
  local backend_ok=0 nginx_ok=0
  while [[ $(date +%s) -lt ${deadline} ]]; do
    if [[ ${backend_ok} -eq 0 ]] && curl -fsS http://127.0.0.1:18001/healthz >/dev/null 2>&1; then
      backend_ok=1
    fi
    if [[ ${nginx_ok} -eq 0 ]] && curl -I http://127.0.0.1:13001 >/dev/null 2>&1; then
      nginx_ok=1
    fi
    if [[ ${backend_ok} -eq 1 && ${nginx_ok} -eq 1 ]]; then
      return 0
    fi
    sleep 1
  done
  return 1
}

# ---------------------------------------------------------------------------
# Phase 1: Host Preflight
# ---------------------------------------------------------------------------

info "Phase 1: Host preflight"

if [[ "$(uname -s)" != "Linux" ]]; then
  die "This installer supports Linux only. Detected: $(uname -s)"
fi

if [[ "$(uname -m)" != "x86_64" ]]; then
  die "This installer supports x86_64 only. Detected: $(uname -m)"
fi

if [[ -z "${HOME:-}" || ! -w "${HOME}" ]]; then
  die "HOME directory must be set and writable."
fi

if ! systemctl --user show-environment >/dev/null 2>&1; then
  die "systemd --user is not available. Log into a full user session."
fi

require_cmd curl
require_cmd tar
require_cmd sha256sum

# ---------------------------------------------------------------------------
# Rollback mode
# ---------------------------------------------------------------------------

if [[ -n "${ROLLBACK_VERSION}" ]]; then
  info "Rollback mode: switching to version ${ROLLBACK_VERSION}"
  ROLLBACK_TARGET="${RELEASES_DIR}/${ROLLBACK_VERSION}"
  if [[ ! -d "${ROLLBACK_TARGET}" ]]; then
    die "Rollback target not found: ${ROLLBACK_TARGET}"
  fi

  # Validate the global runtime environment is still usable.
  if [[ ! -x "${ENV_DIR}/bin/python" ]]; then
    die "Global runtime environment is missing: ${ENV_DIR}/bin/python"
  fi

  info "Stopping services..."
  systemctl --user stop blueprint-re-nginx.service || true
  systemctl --user stop blueprint-re-frontend.service || true
  systemctl --user stop blueprint-re-backend.service || true
  systemctl --user stop blueprint-re-manager-agent.service || true
  sleep 2

  ln -sfn "${ROLLBACK_TARGET}" "${CURRENT_LINK}"

  info "Re-deploying previous release..."
  if ! run_deploy_for_release "${ROLLBACK_TARGET}" --upgrade; then
    die "Rollback deploy failed. Services may be in an inconsistent state."
  fi

  info "Waiting for health checks..."
  if wait_for_health; then
    info "Rollback to ${ROLLBACK_VERSION} complete. Services are healthy."
  else
    warn "Rollback services started but health checks did not pass."
  fi
  exit 0
fi

# ---------------------------------------------------------------------------
# Phase 2: Payload extraction
# ---------------------------------------------------------------------------

info "Phase 2: Extracting payload"

EXTRACT_DIR="$(mktemp -d)"
trap 'rm -rf "${EXTRACT_DIR}"' EXIT

# Direct execution only: the payload offset is embedded at build time.
SCRIPT_PATH=""
if [[ -f "${BASH_SOURCE[0]:-}" ]]; then
  SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
fi

if [[ -z "${SCRIPT_PATH}" ]]; then
  die "This installer cannot be run from a pipe. Save the file to disk and execute it directly."
fi

if [[ ! "${PAYLOAD_OFFSET}" =~ ^[0-9]+$ ]]; then
  die "Invalid PAYLOAD_OFFSET (${PAYLOAD_OFFSET}). This script must be built into a self-extracting installer."
fi

info "Extracting payload to ${EXTRACT_DIR}..."
tail -c +"${PAYLOAD_OFFSET}" "${SCRIPT_PATH}" | tar -xzf - -C "${EXTRACT_DIR}"

PAYLOAD_DIR="${EXTRACT_DIR}/blueprint-re"
if [[ ! -d "${PAYLOAD_DIR}" ]]; then
  die "Payload extraction failed: blueprint-re directory not found."
fi

# ---------------------------------------------------------------------------
# Phase 3: Payload validation
# ---------------------------------------------------------------------------

info "Phase 3: Validating payload"

if [[ ! -f "${PAYLOAD_DIR}/release.json" ]]; then
  die "release.json not found in payload."
fi

# Shell-based JSON extraction (no host Python required for preflight).
RELEASE_VERSION="$(json_get_string "${PAYLOAD_DIR}/release.json" "version")"
RELEASE_ARCH="$(json_get_string "${PAYLOAD_DIR}/release.json" "arch")"
RELEASE_PLATFORM="$(json_get_string "${PAYLOAD_DIR}/release.json" "platform")"

RELEASE_VERSION="${RELEASE_VERSION:-unknown}"
RELEASE_ARCH="${RELEASE_ARCH:-unknown}"
RELEASE_PLATFORM="${RELEASE_PLATFORM:-unknown}"

info "Release version: ${RELEASE_VERSION}"
info "Release arch:    ${RELEASE_ARCH}"
info "Release platform: ${RELEASE_PLATFORM}"

if [[ "${RELEASE_VERSION}" != "${INSTALLER_VERSION}" ]]; then
  die "Version mismatch: installer=${INSTALLER_VERSION}, payload=${RELEASE_VERSION}"
fi

if [[ "${RELEASE_ARCH}" != "${INSTALLER_ARCH}" ]]; then
  die "Architecture mismatch: payload=${RELEASE_ARCH}, installer=${INSTALLER_ARCH}"
fi

if [[ "${RELEASE_PLATFORM}" != "${INSTALLER_PLATFORM}" ]]; then
  die "Platform mismatch: payload=${RELEASE_PLATFORM}, installer=${INSTALLER_PLATFORM}"
fi

# Verify checksums with sha256sum (no host Python required).
if [[ "${SKIP_VERIFY}" -eq 0 ]]; then
  if [[ -f "${PAYLOAD_DIR}/checksums.sha256" ]]; then
    info "Verifying payload checksums..."
    (cd "${PAYLOAD_DIR}" && sha256sum -c --status checksums.sha256) || die "Payload checksum verification failed."
    info "Checksums OK"
  else
    warn "checksums.sha256 not found; skipping checksum verification."
  fi
else
  info "Skipping checksum verification (--skip-verify)"
fi

# Check for offline package cache
HAS_OFFLINE_CACHE=0
if [[ -d "${PAYLOAD_DIR}/runtime/packages" && "$(ls -A "${PAYLOAD_DIR}/runtime/packages")" ]]; then
  HAS_OFFLINE_CACHE=1
  info "Embedded offline package cache detected."
fi

if [[ "${OFFLINE_MODE}" -eq 1 && "${HAS_OFFLINE_CACHE}" -eq 0 ]]; then
  die "--offline requested but embedded package cache is missing."
fi

# ---------------------------------------------------------------------------
# Phase 4: Runtime bootstrap (micromamba/conda)
# ---------------------------------------------------------------------------

info "Phase 4: Runtime bootstrap"

MAMBA_EXE=""
CONDA_EXE=""

# 1. Check for embedded micromamba
if [[ -f "${PAYLOAD_DIR}/runtime/micromamba" ]]; then
  MAMBA_EXE="${PAYLOAD_DIR}/runtime/micromamba"
  chmod +x "${MAMBA_EXE}"
  info "Using embedded micromamba."
fi

# 2. Check for existing micromamba/mamba/conda
if [[ -z "${MAMBA_EXE}" ]]; then
  if command -v micromamba >/dev/null 2>&1; then
    MAMBA_EXE="$(command -v micromamba)"
    info "Using host micromamba: ${MAMBA_EXE}"
  elif command -v mamba >/dev/null 2>&1; then
    CONDA_EXE="$(command -v mamba)"
    info "Using host mamba: ${CONDA_EXE}"
  elif command -v conda >/dev/null 2>&1; then
    CONDA_EXE="$(command -v conda)"
    info "Using host conda: ${CONDA_EXE}"
  fi
fi

# 3. Download micromamba if allowed
if [[ -z "${MAMBA_EXE}" && -z "${CONDA_EXE}" && "${OFFLINE_MODE}" -eq 0 ]]; then
  info "Downloading micromamba bootstrap..."
  MICROMAMBA_URL="https://micro.mamba.pm/api/micromamba/linux-64/latest"
  curl -fsSL "${MICROMAMBA_URL}" | tar -xj -C "${EXTRACT_DIR}" bin/micromamba
  MAMBA_EXE="${EXTRACT_DIR}/bin/micromamba"
  if [[ ! -x "${MAMBA_EXE}" ]]; then
    die "micromamba download failed."
  fi
  info "Downloaded micromamba."
fi

if [[ -z "${MAMBA_EXE}" && -z "${CONDA_EXE}" ]]; then
  die "No conda/mamba/micromamba available and offline mode is active."
fi

# ---------------------------------------------------------------------------
# Phase 5: Create/update dedicated environment
# ---------------------------------------------------------------------------

info "Phase 5: Creating runtime environment at ${ENV_DIR}"

mkdir -p "${ENV_DIR}"

if [[ "${HAS_OFFLINE_CACHE}" -eq 1 ]]; then
  info "Creating environment from offline package cache..."
  if [[ -n "${MAMBA_EXE}" ]]; then
    "${MAMBA_EXE}" create -y -p "${ENV_DIR}" --offline \
      --channel "${PAYLOAD_DIR}/runtime/packages" \
      -f "${PAYLOAD_DIR}/runtime/environment.yml"
  else
    "${CONDA_EXE}" create -y -p "${ENV_DIR}" --offline \
      --channel "${PAYLOAD_DIR}/runtime/packages" \
      -f "${PAYLOAD_DIR}/runtime/environment.yml"
  fi
else
  info "Creating environment from conda-forge (online)..."
  if [[ -n "${MAMBA_EXE}" ]]; then
    "${MAMBA_EXE}" create -y -p "${ENV_DIR}" -f "${PAYLOAD_DIR}/runtime/environment.yml"
  else
    "${CONDA_EXE}" create -y -p "${ENV_DIR}" -f "${PAYLOAD_DIR}/runtime/environment.yml"
  fi
fi

# ---------------------------------------------------------------------------
# Phase 6: Resolve binary paths from the environment
# ---------------------------------------------------------------------------

info "Phase 6: Resolving environment binaries"

ENV_PYTHON="${ENV_DIR}/bin/python"
ENV_NODE="${ENV_DIR}/bin/node"
ENV_NPM="${ENV_DIR}/bin/npm"
ENV_NGINX="${ENV_DIR}/bin/nginx"
ENV_BWRAP="${ENV_DIR}/bin/bwrap"

for bin_path in "${ENV_PYTHON}" "${ENV_NODE}" "${ENV_NPM}" "${ENV_NGINX}" "${ENV_BWRAP}"; do
  if [[ ! -x "${bin_path}" ]]; then
    die "Expected binary missing after environment creation: ${bin_path}"
  fi
done

info "Python:  ${ENV_PYTHON} ($("${ENV_PYTHON}" --version))"
info "Node:    ${ENV_NODE} ($("${ENV_NODE}" -v))"
info "npm:     ${ENV_NPM}"
info "nginx:   ${ENV_NGINX}"
info "bwrap:   ${ENV_BWRAP}"

# ---------------------------------------------------------------------------
# Phase 7: Install backend wheel into the environment
# ---------------------------------------------------------------------------

info "Phase 7: Installing backend wheel"

WHEEL_FILE="$(ls "${PAYLOAD_DIR}/wheels/"*.whl 2>/dev/null | head -n1)"
if [[ -z "${WHEEL_FILE}" ]]; then
  die "No backend wheel found in payload."
fi

# Verify wheel hash against release manifest before installing.
WHEEL_BASENAME="$(basename "${WHEEL_FILE}")"
EXPECTED_WHEEL_HASH="$(${ENV_PYTHON} -c "
import json, sys
manifest = json.load(open(sys.argv[1]))
print(manifest.get('artifacts', {}).get('backend_wheel', {}).get('checksum_sha256', ''))
" "${PAYLOAD_DIR}/release.json")"

if [[ -z "${EXPECTED_WHEEL_HASH}" ]]; then
  die "Could not determine expected wheel hash from release.json."
fi

ACTUAL_WHEEL_HASH="$(sha256sum "${WHEEL_FILE}" | awk '{print $1}')"
if [[ "${ACTUAL_WHEEL_HASH}" != "${EXPECTED_WHEEL_HASH}" ]]; then
  die "Wheel hash mismatch for ${WHEEL_BASENAME}: expected ${EXPECTED_WHEEL_HASH}, got ${ACTUAL_WHEEL_HASH}"
fi
info "Wheel hash verified: ${ACTUAL_WHEEL_HASH}"

# Install the wheel and its bundled dependencies from the local wheels directory.
# The wheels/ directory may also contain vendored dependency wheels so this
# works fully offline when --offline is used.
"${ENV_PYTHON}" -m pip install --quiet --no-index --find-links "${PAYLOAD_DIR}/wheels" --force-reinstall "${WHEEL_FILE}"
info "Installed ${WHEEL_BASENAME}."

# ---------------------------------------------------------------------------
# Phase 8: Deploy release to releases directory
# ---------------------------------------------------------------------------

info "Phase 8: Deploying release"

VERSION_DIR="${RELEASES_DIR}/${RELEASE_VERSION}"

# If this version already exists, back it up.
if [[ -d "${VERSION_DIR}" ]]; then
  BACKUP_DIR="${VERSION_DIR}.backup.$(date +%s)"
  info "Existing version found; backing up to ${BACKUP_DIR}"
  mv "${VERSION_DIR}" "${BACKUP_DIR}"
fi

mkdir -p "${VERSION_DIR}"
cp -a "${PAYLOAD_DIR}/." "${VERSION_DIR}/"

# ---------------------------------------------------------------------------
# Phase 9: Atomic symlink switch (with upgrade handling)
# ---------------------------------------------------------------------------

info "Phase 9: Switching current symlink"

# Determine if this is an upgrade.
IS_UPGRADE=0
PREV_TARGET=""
if [[ -L "${CURRENT_LINK}" ]]; then
  PREV_TARGET="$(readlink -f "${CURRENT_LINK}" || true)"
  if [[ -n "${PREV_TARGET}" && "${PREV_TARGET}" != "${VERSION_DIR}" ]]; then
    IS_UPGRADE=1
    info "Upgrading from ${PREV_TARGET}"
  fi
fi

# For upgrades: stop-the-world before switching.
if [[ "${IS_UPGRADE}" -eq 1 ]]; then
  info "Stopping services for upgrade..."
  systemctl --user stop blueprint-re-nginx.service 2>/dev/null || true
  systemctl --user stop blueprint-re-frontend.service 2>/dev/null || true
  systemctl --user stop blueprint-re-backend.service 2>/dev/null || true
  systemctl --user stop blueprint-re-manager-agent.service 2>/dev/null || true
  sleep 2
fi

# Ensure log directory exists for migration hooks.
mkdir -p "${RELEASE_BASE}/logs"

# Snapshot config and data root metadata before upgrade.
SNAPSHOT_DIR=""
if [[ "${IS_UPGRADE}" -eq 1 ]]; then
  SNAPSHOT_DIR="${RELEASE_BASE}/snapshots/upgrade-$(date +%s)"
  mkdir -p "${SNAPSHOT_DIR}"
  info "Snapshotting config to ${SNAPSHOT_DIR}..."
  cp -a "${APP_ENV_DIR}/." "${SNAPSHOT_DIR}/config/" 2>/dev/null || true
  info "Snapshotting data root metadata..."
  mkdir -p "${SNAPSHOT_DIR}/data"
  if [[ -d "${DATA_ROOT}/_system" ]]; then
    cp -a "${DATA_ROOT}/_system" "${SNAPSHOT_DIR}/data/" 2>/dev/null || true
  fi
  # Snapshot project metadata and graph state so rollback can restore project listing.
  for proj_dir in "${DATA_ROOT}"/*/; do
    if [[ ! -d "${proj_dir}" ]]; then
      continue
    fi
    proj_name="$(basename "${proj_dir}")"
    # Skip non-project directories.
    [[ "${proj_name}" == "_system" ]] && continue
    mkdir -p "${SNAPSHOT_DIR}/data/${proj_name}/graph"
    if [[ -f "${proj_dir}/project.json" ]]; then
      cp -a "${proj_dir}/project.json" "${SNAPSHOT_DIR}/data/${proj_name}/" 2>/dev/null || true
    fi
    if [[ -d "${proj_dir}/graph" ]]; then
      cp -a "${proj_dir}/graph/." "${SNAPSHOT_DIR}/data/${proj_name}/graph/" 2>/dev/null || true
    fi
  done
fi

# Run migration hooks for upgrades (env Python is available now).
MIGRATION_FAILED=0
if [[ "${IS_UPGRADE}" -eq 1 ]]; then
  MIGRATION_PREFLIGHT="$(${ENV_PYTHON} -c "import json,sys; print(json.load(open(sys.argv[1])).get('migrations',{}).get('preflight',''))" "${VERSION_DIR}/release.json")"
  MIGRATION_APPLY="$(${ENV_PYTHON} -c "import json,sys; print(json.load(open(sys.argv[1])).get('migrations',{}).get('apply',''))" "${VERSION_DIR}/release.json")"

  if [[ -n "${MIGRATION_PREFLIGHT}" && -x "${VERSION_DIR}/${MIGRATION_PREFLIGHT}" ]]; then
    info "Running migration preflight..."
    if ! BLUEPRINT_DATA_ROOT="${DATA_ROOT}" BLUEPRINT_SNAPSHOT_DIR="${SNAPSHOT_DIR}" \
         BLUEPRINT_PREV_RELEASE="${PREV_TARGET}" \
         "${VERSION_DIR}/${MIGRATION_PREFLIGHT}" >>"${RELEASE_BASE}/logs/preflight.log" 2>&1; then
      warn "Migration preflight failed. See ${RELEASE_BASE}/logs/preflight.log"
      MIGRATION_FAILED=1
    fi
  fi

  if [[ "${MIGRATION_FAILED}" -eq 0 && -n "${MIGRATION_APPLY}" && -x "${VERSION_DIR}/${MIGRATION_APPLY}" ]]; then
    info "Running migration apply..."
    if ! BLUEPRINT_DATA_ROOT="${DATA_ROOT}" BLUEPRINT_SNAPSHOT_DIR="${SNAPSHOT_DIR}" \
         BLUEPRINT_PREV_RELEASE="${PREV_TARGET}" \
         "${VERSION_DIR}/${MIGRATION_APPLY}" >>"${RELEASE_BASE}/logs/apply.log" 2>&1; then
      warn "Migration apply failed. See ${RELEASE_BASE}/logs/apply.log"
      MIGRATION_FAILED=1
    fi
  fi
fi

# Rollback helper.
rollback() {
  info "ROLLBACK: restoring previous release..."
  if [[ -n "${PREV_TARGET}" && -d "${PREV_TARGET}" ]]; then
    ln -sfn "${PREV_TARGET}" "${CURRENT_LINK}"
    info "Restored current symlink to ${PREV_TARGET}"
  fi
  if [[ -n "${SNAPSHOT_DIR}" && -d "${SNAPSHOT_DIR}/config" ]]; then
    rm -rf "${APP_ENV_DIR}"
    cp -a "${SNAPSHOT_DIR}/config" "${APP_ENV_DIR}"
    info "Restored config from snapshot."
  fi
  # Re-deploy previous release with full env and health checks.
  if [[ -n "${PREV_TARGET}" && -d "${PREV_TARGET}" ]]; then
    info "Re-deploying previous release..."
    if run_deploy_for_release "${PREV_TARGET}" --upgrade; then
      if wait_for_health; then
        info "Previous release is healthy after rollback."
      else
        warn "Previous release deployed but health checks did not pass."
      fi
    else
      warn "Previous release deploy failed during rollback."
    fi
  fi
  die "Upgrade failed. Previous release has been restored."
}

# If migration failed, rollback immediately.
if [[ "${MIGRATION_FAILED}" -eq 1 ]]; then
  rollback
fi

ln -sfn "${VERSION_DIR}" "${CURRENT_LINK}"

# ---------------------------------------------------------------------------
# Phase 10: Run deploy
# ---------------------------------------------------------------------------

info "Phase 10: Running deploy"

DEPLOY_ARGS=()
if [[ "${IS_UPGRADE}" -eq 1 ]]; then
  DEPLOY_ARGS+=("--upgrade")
fi

if ! run_deploy "${DEPLOY_ARGS[@]}"; then
  if [[ "${IS_UPGRADE}" -eq 1 ]]; then
    rollback
  else
    die "Deploy failed."
  fi
fi

# Health check after fresh install or upgrade.
info "Waiting for health checks..."
if ! wait_for_health; then
  if [[ "${IS_UPGRADE}" -eq 1 ]]; then
    warn "Health checks failed after upgrade; rolling back..."
    rollback
  else
    die "Health checks failed after install."
  fi
fi
info "Health checks passed."

# ---------------------------------------------------------------------------
# Phase 11: Cleanup old releases (keep last 2)
# ---------------------------------------------------------------------------

info "Phase 11: Cleanup"

if [[ -d "${RELEASES_DIR}" ]]; then
  # Sort versions and keep the 2 most recent. Backup directories are not
  # considered release versions and are never auto-removed.
  mapfile -t ALL_VERSIONS < <(ls -1 "${RELEASES_DIR}" | grep -E '^[0-9]+(\.[0-9]+)*$' | sort -V -r)
  if [[ "${#ALL_VERSIONS[@]}" -gt 2 ]]; then
    for old_ver in "${ALL_VERSIONS[@]:2}"; do
      old_path="${RELEASES_DIR}/${old_ver}"
      # Never remove the currently active release.
      if [[ "$(readlink -f "${CURRENT_LINK}" 2>/dev/null || true)" == "$(readlink -f "${old_path}" 2>/dev/null || true)" ]]; then
        continue
      fi
      info "Removing old release: ${old_ver}"
      rm -rf "${old_path}"
    done
  fi
fi

# ---------------------------------------------------------------------------
# Complete
# ---------------------------------------------------------------------------

info "Installation complete."
info "Version:  ${RELEASE_VERSION}"
info "Release:  ${CURRENT_LINK} -> ${VERSION_DIR}"
info "Data:     ${DATA_ROOT}"
info ""
info "Frontend: http://127.0.0.1:13001"
info "Backend:  http://127.0.0.1:18001"

# Stop here; anything after exit 0 is the binary payload.
exit 0

# Mark the end of the script; anything after this line is the payload.
__PAYLOAD_START__
