#!/usr/bin/env bash
set -euo pipefail

# Release authoring tool.
# Consumes a release payload tarball and produces a self-extracting shell installer.
#
# Usage:
#   bash scripts/build_self_extracting_installer.sh [OPTIONS] [tarball-path]
#
# Options:
#   --artifact-prefix PREFIX  Output filename prefix (default: blueprint-re)
#   --output-dir DIR          Write installer to DIR (default: ./dist)
#
# If no tarball is provided, looks in ./dist/ for the most recent tarball.
# Outputs: dist/<prefix>-<version>-linux-x86_64.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_DIR="${REPO_ROOT}/dist"
ARTIFACT_PREFIX="blueprint-re"

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

TARBALL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --artifact-prefix)
      [[ -n "${2:-}" ]] || die "--artifact-prefix requires a value"
      ARTIFACT_PREFIX="$2"
      shift 2
      ;;
    --output-dir)
      [[ -n "${2:-}" ]] || die "--output-dir requires a value"
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --help|-h)
      sed -n '1,/^# Outputs:/s/^# //p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    -*)
      die "Unknown option: $1"
      ;;
    *)
      if [[ -z "${TARBALL}" ]]; then
        TARBALL="$1"
      else
        die "Unexpected positional argument: $1"
      fi
      shift
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Locate tarball
# ---------------------------------------------------------------------------

if [[ -z "${TARBALL}" ]]; then
  # Find the newest tarball in dist/ that matches the default payload name.
  TARBALL="$(ls -t "${OUTPUT_DIR}"/*.tar.gz 2>/dev/null | head -n1 || true)"
fi

if [[ -z "${TARBALL}" || ! -f "${TARBALL}" ]]; then
  die "No tarball found. Build a release bundle first: bash scripts/build_release_bundle.sh"
fi

TARBALL="$(cd "$(dirname "${TARBALL}")" && pwd)/$(basename "${TARBALL}")"
TARBALL_BASENAME="$(basename "${TARBALL}" .tar.gz)"

# Extract version from tarball name: expects <prefix>-<version>-linux-<arch>
# The payload tarball currently uses the blueprint-re prefix regardless of the
# public installer filename.
if [[ "${TARBALL_BASENAME}" =~ -linux-([^-]+)$ ]]; then
  ARCH="${BASH_REMATCH[1]}"
  # Remove the -linux-<arch> suffix to get <prefix>-<version>.
  REMAINING="${TARBALL_BASENAME%-linux-${ARCH}}"
  # The tarball prefix is currently fixed to blueprint-re.
  TARBALL_PREFIX="blueprint-re"
  VERSION="${REMAINING#${TARBALL_PREFIX}-}"
else
  die "Could not parse version/arch from tarball name: ${TARBALL_BASENAME}"
fi

INSTALLER_NAME="${ARTIFACT_PREFIX}-${VERSION}-linux-${ARCH}.sh"
INSTALLER_PATH="${OUTPUT_DIR}/${INSTALLER_NAME}"

# ---------------------------------------------------------------------------
# Locate installer stub
# ---------------------------------------------------------------------------

STUB_SOURCE="${REPO_ROOT}/scripts/install.sh"
[[ -f "${STUB_SOURCE}" ]] || die "Installer stub not found: ${STUB_SOURCE}"

# ---------------------------------------------------------------------------
# Build self-extracting installer
# ---------------------------------------------------------------------------

echo "Building self-extracting installer..."
echo "  Source:  ${STUB_SOURCE}"
echo "  Payload: ${TARBALL}"
echo "  Output:  ${INSTALLER_PATH}"

mkdir -p "${OUTPUT_DIR}"

# Replace the version placeholder in the stub.
sed -e "s|__INSTALLER_VERSION__|${VERSION}|g" "${STUB_SOURCE}" > "${INSTALLER_PATH}"

# Append the payload tarball after the __PAYLOAD_START__ marker.
# The stub already ends with __PAYLOAD_START__ on its own line.
cat "${TARBALL}" >> "${INSTALLER_PATH}"
chmod +x "${INSTALLER_PATH}"

# ---------------------------------------------------------------------------
# Embed the payload byte offset into the installer
# ---------------------------------------------------------------------------

# Compute the byte offset where the payload begins (after __PAYLOAD_START__ + newline).
PAYLOAD_OFFSET=$(python3 -c "
import sys
path = sys.argv[1]
marker = b'__PAYLOAD_START__\\n'
with open(path, 'rb') as f:
    data = f.read()
idx = data.find(marker)
if idx < 0:
    print('ERROR: marker not found', file=sys.stderr)
    sys.exit(1)
# tail -c +N is 1-indexed; add 1 to the 0-indexed byte position.
print(idx + len(marker) + 1)
" "${INSTALLER_PATH}")

if [[ -z "${PAYLOAD_OFFSET}" || ! "${PAYLOAD_OFFSET}" =~ ^[0-9]+$ ]]; then
  die "Failed to compute payload byte offset."
fi

# The placeholder is exactly 18 chars. Replace it with a zero-padded number of
# the same length so the file size (and therefore the payload offset) does not
# change after substitution.
PLACEHOLDER_LEN=18
PADDED_OFFSET=$(printf "%0${PLACEHOLDER_LEN}d" "${PAYLOAD_OFFSET}")

# Verify the placeholder exists exactly once in the stub region. We locate it
# by grep before replacing so we can assert size invariance.
PLACEHOLDER_COUNT="$(LC_ALL=C grep -ao '__PAYLOAD_OFFSET__' "${INSTALLER_PATH}" | wc -l)"
if [[ "${PLACEHOLDER_COUNT}" -ne 1 ]]; then
  die "Expected exactly one __PAYLOAD_OFFSET__ placeholder, found ${PLACEHOLDER_COUNT}"
fi

SIZE_BEFORE="$(stat -c %s "${INSTALLER_PATH}")"

# Replace the placeholder with the padded offset.
sed -i -e "s|__PAYLOAD_OFFSET__|${PADDED_OFFSET}|g" "${INSTALLER_PATH}"

SIZE_AFTER="$(stat -c %s "${INSTALLER_PATH}")"
if [[ "${SIZE_BEFORE}" -ne "${SIZE_AFTER}" ]]; then
  die "Installer size changed after offset substitution (${SIZE_BEFORE} -> ${SIZE_AFTER}). The placeholder width is wrong."
fi

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

echo "Verifying installer..."

# Check the marker is present.
if ! LC_ALL=C grep -aq "^__PAYLOAD_START__$" "${INSTALLER_PATH}"; then
  die "Installer verification failed: __PAYLOAD_START__ marker not found."
fi

# Check payload follows the offset.
TAIL_BYTES="$(tail -c +"${PAYLOAD_OFFSET}" "${INSTALLER_PATH}" | wc -c)"
TARBALL_BYTES="$(wc -c < "${TARBALL}")"

if [[ "${TAIL_BYTES}" -ne "${TARBALL_BYTES}" ]]; then
  die "Installer verification failed: appended payload size mismatch (${TAIL_BYTES} vs ${TARBALL_BYTES})."
fi

# Verify the tarball is intact.
if ! tail -c +"${PAYLOAD_OFFSET}" "${INSTALLER_PATH}" | tar -tzf - > /dev/null 2>&1; then
  die "Installer verification failed: payload tarball is corrupt."
fi

echo "Installer verification passed."

# Compute checksum.
INSTALLER_CHECKSUM="$(sha256sum "${INSTALLER_PATH}" | awk '{print $1}')"
printf '%s  %s\n' "${INSTALLER_CHECKSUM}" "${INSTALLER_NAME}" > "${INSTALLER_PATH}.sha256"

echo ""
echo "Self-extracting installer complete:"
echo "  ${INSTALLER_PATH}"
echo "  ${INSTALLER_PATH}.sha256"
