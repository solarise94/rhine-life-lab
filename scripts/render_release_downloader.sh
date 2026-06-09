#!/usr/bin/env bash
set -euo pipefail

# Release authoring tool.
# Renders a release-specific public downloader script (install.sh) from a
# template. The rendered script hardcodes the paired versioned installer URL
# and checksum URL so each GitHub Release is self-contained.
#
# Usage:
#   bash scripts/render_release_downloader.sh [OPTIONS]
#
# Options:
#   --version VERSION          Release version (required)
#   --repo OWNER/NAME          GitHub repository path (default: solarise94/RhineDataLab)
#   --artifact-prefix PREFIX   Installer filename prefix (default: rhinedatalab)
#   --arch ARCH                Target architecture (default: x86_64)
#   --output PATH              Output path (default: ./dist/install.sh)
#   --template PATH            Template path (default: scripts/templates/install_downloader.template.sh)
#   --help|-h                  Show this message

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

VERSION=""
REPO="solarise94/RhineDataLab"
ARTIFACT_PREFIX="rhinedatalab"
ARCH="x86_64"
OUTPUT="${REPO_ROOT}/dist/install.sh"
TEMPLATE="${REPO_ROOT}/scripts/templates/install_downloader.template.sh"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

die() {
  echo "ERROR: $1" >&2
  exit 1
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
    --artifact-prefix)
      [[ -n "${2:-}" ]] || die "--artifact-prefix requires a value"
      ARTIFACT_PREFIX="$2"
      shift 2
      ;;
    --arch)
      [[ -n "${2:-}" ]] || die "--arch requires a value"
      ARCH="$2"
      shift 2
      ;;
    --output)
      [[ -n "${2:-}" ]] || die "--output requires a value"
      OUTPUT="$2"
      shift 2
      ;;
    --template)
      [[ -n "${2:-}" ]] || die "--template requires a value"
      TEMPLATE="$2"
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

if [[ ! -f "${TEMPLATE}" ]]; then
  die "Downloader template not found: ${TEMPLATE}"
fi

# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

ARTIFACT_NAME="${ARTIFACT_PREFIX}-${VERSION}-linux-${ARCH}.sh"
INSTALLER_URL="https://github.com/${REPO}/releases/download/v${VERSION}/${ARTIFACT_NAME}"
CHECKSUM_URL="${INSTALLER_URL}.sha256"

mkdir -p "$(dirname "${OUTPUT}")"

# Use a delimiter unlikely to appear in URLs.
sed \
  -e "s|__RELEASE_VERSION__|${VERSION}|g" \
  -e "s|__RELEASE_REPO__|${REPO}|g" \
  -e "s|__RELEASE_ARCH__|${ARCH}|g" \
  -e "s|__RELEASE_ARTIFACT_NAME__|${ARTIFACT_NAME}|g" \
  -e "s|__RELEASE_INSTALLER_URL__|${INSTALLER_URL}|g" \
  -e "s|__RELEASE_CHECKSUM_URL__|${CHECKSUM_URL}|g" \
  "${TEMPLATE}" > "${OUTPUT}"

chmod +x "${OUTPUT}"

echo "Rendered public downloader:"
echo "  ${OUTPUT}"
echo "  version:  ${VERSION}"
echo "  artifact: ${ARTIFACT_NAME}"
echo "  repo:     ${REPO}"
