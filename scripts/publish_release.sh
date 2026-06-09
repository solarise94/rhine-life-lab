#!/usr/bin/env bash
set -euo pipefail

# Release publishing automation.
#
# Orchestrates the full release pipeline for a tagged version:
#   - validate version metadata
#   - build release bundle tarball
#   - build versioned self-extracting installer
#   - render public install.sh downloader
#   - generate checksums
#   - create or update GitHub Release
#   - upload all assets
#   - validate public URLs
#
# Usage:
#   bash scripts/publish_release.sh [OPTIONS]
#
# Options:
#   --version VERSION         Release version (required, e.g. 0.4.2)
#   --repo OWNER/NAME         GitHub repository path (default: solarise94/RhineDataLab)
#   --skip-build              Skip bundle/installer build (use existing dist/)
#   --skip-upload             Prepare assets locally but do not push to GitHub
#   --draft                   Create release as draft
#   --prerelease              Mark release as prerelease
#   --notes-file PATH         Release notes file
#   --notes-string TEXT       Release notes text
#   --help|-h                 Show this message

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

VERSION=""
REPO="solarise94/RhineDataLab"
SKIP_BUILD=0
SKIP_UPLOAD=0
DRAFT=""
PRERELEASE=""
NOTES_FILE=""
NOTES_STRING=""

OUTPUT_DIR="${REPO_ROOT}/dist"
ASSET_DIR_FROM_CLI=""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

die() {
  echo "ERROR: $1" >&2
  exit 1
}

info() {
  echo "[publish] $1"
}

warn() {
  echo "[publish] WARNING: $1" >&2
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    die "Required command not found: $1"
  fi
}

# Extract version from pyproject.toml.
read_pyproject_version() {
  python3 -c "
import sys, re
text = open('${REPO_ROOT}/backend/pyproject.toml').read()
m = re.search(r'^version\s*=\s*\"([^\"]+)\"', text, re.M)
print(m.group(1) if m else '0.0.0')
"
}

# Compute SHA-256 checksum file.
sha256_file() {
  sha256sum "$1" | awk '{print $1}'
}

# Generate checksum sidecar file for an artifact.
write_checksum() {
  local artifact="$1"
  local checksum
  checksum="$(sha256_file "${artifact}")"
  printf '%s  %s\n' "${checksum}" "$(basename "${artifact}")" > "${artifact}.sha256"
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      [[ -n "${2:-}" ]] || die "--version requires a value"
      VERSION="$2"
      shift 2
      ;;
    --repo)
      [[ -n "${2:-}" ]] || die "--repo requires a value"
      REPO="$2"
      shift 2
      ;;
    --skip-build)
      SKIP_BUILD=1
      shift
      ;;
    --skip-upload)
      SKIP_UPLOAD=1
      shift
      ;;
    --output-dir)
      [[ -n "${2:-}" ]] || die "--output-dir requires a value"
      ASSET_DIR_FROM_CLI="$2"
      shift 2
      ;;
    --draft)
      DRAFT="--draft"
      shift
      ;;
    --prerelease)
      PRERELEASE="--prerelease"
      shift
      ;;
    --notes-file)
      [[ -n "${2:-}" ]] || die "--notes-file requires a value"
      NOTES_FILE="$2"
      shift 2
      ;;
    --notes-string)
      [[ -n "${2:-}" ]] || die "--notes-string requires a value"
      NOTES_STRING="$2"
      shift 2
      ;;
    --help|-h)
      sed -n '1,/^# Options:/s/^# //p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    -*)
      die "Unknown option: $1"
      ;;
    *)
      die "Unexpected positional argument: $1"
      ;;
  esac
done

[[ -n "${VERSION}" ]] || die "--version is required"

if [[ -n "${ASSET_DIR_FROM_CLI}" ]]; then
  OUTPUT_DIR="${ASSET_DIR_FROM_CLI}"
fi

# ---------------------------------------------------------------------------
# Phase 0: Validate version metadata
# ---------------------------------------------------------------------------

PYPROJECT_VERSION="$(read_pyproject_version)"
if [[ "${PYPROJECT_VERSION}" != "${VERSION}" ]]; then
  die "Version mismatch: pyproject.toml has ${PYPROJECT_VERSION}, requested ${VERSION}"
fi

info "Publishing release v${VERSION} to ${REPO}"

# ---------------------------------------------------------------------------
# Phase 1: Build bundle and installer
# ---------------------------------------------------------------------------

if [[ "${SKIP_BUILD}" -eq 0 ]]; then
  info "Building release bundle..."
  bash "${REPO_ROOT}/scripts/build_release_bundle.sh" "${OUTPUT_DIR}"

  info "Building self-extracting installer (rhinedatalab)..."
  bash "${REPO_ROOT}/scripts/build_self_extracting_installer.sh" \
    --artifact-prefix "rhinedatalab" \
    --output-dir "${OUTPUT_DIR}"
else
  info "Skipping build; using existing assets in ${OUTPUT_DIR}"
fi

# ---------------------------------------------------------------------------
# Phase 2: Render public downloader
# ---------------------------------------------------------------------------

INSTALLER_NAME="rhinedatalab-${VERSION}-linux-x86_64.sh"
INSTALLER_PATH="${OUTPUT_DIR}/${INSTALLER_NAME}"

[[ -f "${INSTALLER_PATH}" ]] || die "Installer not found: ${INSTALLER_PATH}"

info "Rendering public downloader install.sh..."
bash "${REPO_ROOT}/scripts/render_release_downloader.sh" \
  --version "${VERSION}" \
  --repo "${REPO}" \
  --artifact-prefix "rhinedatalab" \
  --arch "x86_64" \
  --output "${OUTPUT_DIR}/install.sh"

DOWNLOADER_PATH="${OUTPUT_DIR}/install.sh"
[[ -f "${DOWNLOADER_PATH}" ]] || die "Rendered downloader not found: ${DOWNLOADER_PATH}"

# ---------------------------------------------------------------------------
# Phase 3: Generate checksums
# ---------------------------------------------------------------------------

info "Generating checksums..."
write_checksum "${INSTALLER_PATH}"
write_checksum "${DOWNLOADER_PATH}"

TARBALL_PATH="${OUTPUT_DIR}/rhinedatalab-${VERSION}-linux-x86_64.tar.gz"
[[ -f "${TARBALL_PATH}" ]] || die "Tarball not found: ${TARBALL_PATH}"
write_checksum "${TARBALL_PATH}"

# ---------------------------------------------------------------------------
# Phase 4: Build asset list
# ---------------------------------------------------------------------------

ASSETS=(
  "${DOWNLOADER_PATH}"
  "${DOWNLOADER_PATH}.sha256"
  "${INSTALLER_PATH}"
  "${INSTALLER_PATH}.sha256"
  "${TARBALL_PATH}"
  "${TARBALL_PATH}.sha256"
)

info "Release assets:"
for a in "${ASSETS[@]}"; do
  echo "  $(basename "$a")"
done

if [[ "${SKIP_UPLOAD}" -eq 1 ]]; then
  info "--skip-upload specified. Assets prepared locally in ${OUTPUT_DIR}."
  echo ""
  echo "To upload manually, run:"
  echo "  gh release create v${VERSION} ${ASSETS[*]} --repo ${REPO} --title \"v${VERSION}\" --notes \"...\""
  exit 0
fi

# ---------------------------------------------------------------------------
# Phase 5: Create or update GitHub Release
# ---------------------------------------------------------------------------

require_cmd gh

# Verify gh authentication.
if ! gh auth status >/dev/null 2>&1; then
  die "gh CLI is not authenticated. Run: gh auth login"
fi

TAG="v${VERSION}"
RELEASE_EXISTS=0
if gh release view "${TAG}" --repo "${REPO}" >/dev/null 2>&1; then
  RELEASE_EXISTS=1
  info "Release ${TAG} already exists. Will upload/overwrite assets."
else
  info "Creating GitHub Release ${TAG}..."
  NOTES_ARGS=()
  if [[ -n "${NOTES_FILE}" && -f "${NOTES_FILE}" ]]; then
    NOTES_ARGS=(--notes-file "${NOTES_FILE}")
  elif [[ -n "${NOTES_STRING}" ]]; then
    NOTES_ARGS=(--notes "${NOTES_STRING}")
  else
    NOTES_ARGS=(--generate-notes)
  fi
  # shellcheck disable=SC2086
  gh release create "${TAG}" \
    --repo "${REPO}" \
    --title "${TAG}" \
    ${DRAFT} \
    ${PRERELEASE} \
    "${NOTES_ARGS[@]}"
fi

# ---------------------------------------------------------------------------
# Phase 6: Upload assets
# ---------------------------------------------------------------------------

info "Uploading assets..."
for asset in "${ASSETS[@]}"; do
  info "  -> $(basename "${asset}")"
  gh release upload "${TAG}" "${asset}" --repo "${REPO}" --clobber
done

info "Upload complete."

# ---------------------------------------------------------------------------
# Phase 7: Post-publish validation
# ---------------------------------------------------------------------------

info "Validating public URLs..."

validate_url() {
  local url="$1"
  local expected_status="${2:-200}"
  local status
  status="$(curl -fsSL -o /dev/null -w "%{http_code}" "${url}" 2>/dev/null || true)"
  if [[ "${status}" == "${expected_status}" ]]; then
    info "  OK ${url}"
  else
    warn "  FAILED ${url} (HTTP ${status})"
    return 1
  fi
}

VALIDATION_ERRORS=0

validate_url "https://github.com/${REPO}/releases/download/${TAG}/install.sh" || VALIDATION_ERRORS=$((VALIDATION_ERRORS + 1))
validate_url "https://github.com/${REPO}/releases/download/${TAG}/install.sh.sha256" || VALIDATION_ERRORS=$((VALIDATION_ERRORS + 1))
validate_url "https://github.com/${REPO}/releases/download/${TAG}/${INSTALLER_NAME}" || VALIDATION_ERRORS=$((VALIDATION_ERRORS + 1))
validate_url "https://github.com/${REPO}/releases/download/${TAG}/${INSTALLER_NAME}.sha256" || VALIDATION_ERRORS=$((VALIDATION_ERRORS + 1))

# Validate latest entrypoint only if this is the newest non-draft release.
if [[ -z "${DRAFT}" ]]; then
  LATEST_INSTALLER_URL="https://github.com/${REPO}/releases/latest/download/install.sh"
  LATEST_STATUS="$(curl -fsSL -o /dev/null -w "%{http_code}" "${LATEST_INSTALLER_URL}" 2>/dev/null || true)"
  if [[ "${LATEST_STATUS}" == "200" ]]; then
    info "  OK ${LATEST_INSTALLER_URL}"
  else
    warn "  FAILED ${LATEST_INSTALLER_URL} (HTTP ${LATEST_STATUS}). This may be expected if ${TAG} is not the latest release."
  fi
fi

if [[ "${VALIDATION_ERRORS}" -gt 0 ]]; then
  die "Post-publish validation failed with ${VALIDATION_ERRORS} error(s)."
fi

info "Release ${TAG} published successfully."
