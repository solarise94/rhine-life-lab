#!/usr/bin/env bash
set -euo pipefail

# Release authoring tool.
# Builds a versioned release payload tarball containing:
#   - backend wheel
#   - prebuilt Next.js standalone frontend
#   - manager-agent source + lockfile
#   - deploy templates and installer scripts
#   - release.json manifest with checksums
#
# Usage:
#   bash scripts/build_release_bundle.sh [OPTIONS] [output-dir]
#
# Options:
#   --offline-cache    Also populate runtime/packages/ from conda-forge.
#                      Requires a working micromamba/mamba/conda.
#
# The output defaults to ./dist/.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_DIR="${REPO_ROOT}/dist"
BUILD_OFFLINE_CACHE=0
PAYLOAD_NAME="blueprint-re"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

die() {
  echo "ERROR: $1" >&2
  exit 1
}

warn() {
  echo "WARNING: $1" >&2
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    die "Required command not found: $1"
  fi
}

# Extract version from pyproject.toml (PEP 621).
read_pyproject_version() {
  local toml_file="$1"
  python3 -c "
import sys, re
text = open(sys.argv[1]).read()
m = re.search(r'^version\s*=\s*\"([^\"]+)\"', text, re.M)
print(m.group(1) if m else '0.0.0')
" "${toml_file}"
}

# Extract version from package.json.
read_package_version() {
  local json_file="$1"
  python3 -c "
import json, sys
print(json.load(open(sys.argv[1])).get('version', '0.0.0'))
" "${json_file}"
}

# Compute SHA-256 checksum file.
sha256_file() {
  sha256sum "$1" | awk '{print $1}'
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --offline-cache)
      BUILD_OFFLINE_CACHE=1
      shift
      ;;
    --help|-h)
      sed -n '1,/^# The output defaults/s/^# //p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    -*)
      die "Unknown option: $1"
      ;;
    *)
      OUTPUT_DIR="$1"
      shift
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

require_cmd python3
require_cmd npm
require_cmd node

BACKEND_VERSION="$(read_pyproject_version "${REPO_ROOT}/backend/pyproject.toml")"
FRONTEND_VERSION="$(read_package_version "${REPO_ROOT}/frontend/package.json")"
MANAGER_VERSION="$(read_package_version "${REPO_ROOT}/manager-agent/package.json")"

# Require all versions to match for a coherent release.
if [[ "${BACKEND_VERSION}" != "${FRONTEND_VERSION}" ]]; then
  die "Backend version (${BACKEND_VERSION}) does not match frontend version (${FRONTEND_VERSION})"
fi
if [[ "${BACKEND_VERSION}" != "${MANAGER_VERSION}" ]]; then
  die "Backend version (${BACKEND_VERSION}) does not match manager-agent version (${MANAGER_VERSION})"
fi

VERSION="${BACKEND_VERSION}"
echo "Building release bundle for Blueprint RE ${VERSION}"

# ---------------------------------------------------------------------------
# Prepare staging area
# ---------------------------------------------------------------------------

STAGING_DIR="$(mktemp -d)"
trap 'rm -rf "${STAGING_DIR}"' EXIT

BUNDLE_ROOT="${STAGING_DIR}/${PAYLOAD_NAME}"
mkdir -p "${BUNDLE_ROOT}/wheels"
mkdir -p "${BUNDLE_ROOT}/frontend-standalone"
mkdir -p "${BUNDLE_ROOT}/manager-agent"
mkdir -p "${BUNDLE_ROOT}/deploy"
mkdir -p "${BUNDLE_ROOT}/scripts"
mkdir -p "${BUNDLE_ROOT}/runtime"

# ---------------------------------------------------------------------------
# Build backend wheel
# ---------------------------------------------------------------------------

echo "Building backend wheel..."
python3 -m pip install --quiet build wheel
python3 -m build "${REPO_ROOT}/backend" --wheel --outdir "${BUNDLE_ROOT}/wheels"
WHEEL_FILE="$(ls "${BUNDLE_ROOT}/wheels/"*.whl | head -n1)"
[[ -f "${WHEEL_FILE}" ]] || die "Wheel build failed: no .whl produced"
echo "Wheel: $(basename "${WHEEL_FILE}")"

# Download all Python dependencies into wheels/ so the installer can work
# fully offline with --no-index --find-links.
echo "Downloading Python dependencies into wheels/..."
python3 -m pip download --quiet -d "${BUNDLE_ROOT}/wheels" "${WHEEL_FILE}"
echo "Python dependencies staged."

# ---------------------------------------------------------------------------
# Build frontend standalone
# ---------------------------------------------------------------------------

echo "Building frontend standalone..."
pushd "${REPO_ROOT}/frontend" >/dev/null

# Ensure dependencies are installed.
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi

# Build with the standalone output target.
NEXT_PUBLIC_API_BASE_URL=/api NEXT_PUBLIC_UPLOAD_API_BASE_URL=/upload-api npm run build

# The postbuild script creates a symlink for static assets.
# For the release bundle we copy real files so the artifact is self-contained.
STANDALONE_SRC="${REPO_ROOT}/frontend/.next/standalone"
STATIC_SRC="${REPO_ROOT}/frontend/.next/static"
PUBLIC_SRC="${REPO_ROOT}/frontend/public"

[[ -d "${STANDALONE_SRC}" ]] || die "Frontend standalone build failed: .next/standalone not found"

cp -a "${STANDALONE_SRC}/." "${BUNDLE_ROOT}/frontend-standalone/"

# Replace the symlinked static with real files if needed.
if [[ -L "${BUNDLE_ROOT}/frontend-standalone/frontend/.next/static" ]]; then
  rm -f "${BUNDLE_ROOT}/frontend-standalone/frontend/.next/static"
fi
if [[ -d "${STATIC_SRC}" ]]; then
  mkdir -p "${BUNDLE_ROOT}/frontend-standalone/frontend/.next/static"
  cp -a "${STATIC_SRC}/." "${BUNDLE_ROOT}/frontend-standalone/frontend/.next/static/"
fi

if [[ -d "${PUBLIC_SRC}" ]]; then
  mkdir -p "${BUNDLE_ROOT}/frontend-standalone/frontend/public"
  cp -a "${PUBLIC_SRC}/." "${BUNDLE_ROOT}/frontend-standalone/frontend/public/"
fi

popd >/dev/null
echo "Frontend standalone staged."

# ---------------------------------------------------------------------------
# Gather manager-agent
# ---------------------------------------------------------------------------

echo "Gathering manager-agent..."
cp -a "${REPO_ROOT}/manager-agent/src" "${BUNDLE_ROOT}/manager-agent/"
cp "${REPO_ROOT}/manager-agent/package.json" "${BUNDLE_ROOT}/manager-agent/"
if [[ -f "${REPO_ROOT}/manager-agent/package-lock.json" ]]; then
  cp "${REPO_ROOT}/manager-agent/package-lock.json" "${BUNDLE_ROOT}/manager-agent/"
fi
# Bundle node_modules so deploy_release.sh can skip npm ci when offline.
if [[ -d "${REPO_ROOT}/manager-agent/node_modules" ]]; then
  echo "Bundling manager-agent node_modules..."
  cp -a "${REPO_ROOT}/manager-agent/node_modules" "${BUNDLE_ROOT}/manager-agent/"
fi

# ---------------------------------------------------------------------------
# Gather deploy templates
# ---------------------------------------------------------------------------

echo "Gathering deploy templates..."
cp -a "${REPO_ROOT}/deploy/systemd-release/"* "${BUNDLE_ROOT}/deploy/"
cp -a "${REPO_ROOT}/deploy/nginx/"* "${BUNDLE_ROOT}/deploy/"

# ---------------------------------------------------------------------------
# Gather installer scripts
# ---------------------------------------------------------------------------

echo "Gathering installer scripts..."
cp "${REPO_ROOT}/scripts/install.sh" "${BUNDLE_ROOT}/scripts/"
cp "${REPO_ROOT}/scripts/deploy_release.sh" "${BUNDLE_ROOT}/scripts/"
cp "${REPO_ROOT}/scripts/uninstall.sh" "${BUNDLE_ROOT}/scripts/"
# The pi executor launcher is referenced by default BLUEPRINT_PI_COMMAND_JSON.
cp "${REPO_ROOT}/scripts/blueprint_pi_launch.sh" "${BUNDLE_ROOT}/scripts/"

# ---------------------------------------------------------------------------
# Gather runtime dependency metadata
# ---------------------------------------------------------------------------

echo "Gathering runtime metadata..."
cp "${REPO_ROOT}/deploy/runtime-dependencies.yml" "${BUNDLE_ROOT}/runtime/"

# Write a proper conda environment file for online installs.
cat > "${BUNDLE_ROOT}/runtime/environment.yml" <<'EOF'
name: blueprint-re-env
channels:
  - conda-forge
dependencies:
  - python >=3.13
  - nodejs >=22.19
  - nginx
  - bubblewrap
  - git
EOF

# Optionally populate the offline package cache for tagged releases.
if [[ "${BUILD_OFFLINE_CACHE}" -eq 1 ]]; then
  echo "Populating offline package cache (this may take a while)..."
  mkdir -p "${BUNDLE_ROOT}/runtime/packages"
  if command -v micromamba >/dev/null 2>&1; then
    # Best-effort: fetch packages into the local cache.
    micromamba create -y -p "${BUNDLE_ROOT}/runtime/.tmp-env" \
      -f "${BUNDLE_ROOT}/runtime/environment.yml" \
      --download-only 2>/dev/null || true
  elif command -v conda >/dev/null 2>&1; then
    conda create -y -p "${BUNDLE_ROOT}/runtime/.tmp-env" \
      -f "${BUNDLE_ROOT}/runtime/environment.yml" \
      --download-only 2>/dev/null || true
  else
    warn "No micromamba/conda found; cannot build offline cache."
  fi
  rm -rf "${BUNDLE_ROOT}/runtime/.tmp-env"
  # Conda package caches are host-specific; we export the explicit spec too.
  if command -v micromamba >/dev/null 2>&1; then
    micromamba env export -p "${BUNDLE_ROOT}/runtime/.tmp-env" > "${BUNDLE_ROOT}/runtime/explicit.txt" 2>/dev/null || true
  fi
fi

# ---------------------------------------------------------------------------
# Generate release.json manifest with checksums
# ---------------------------------------------------------------------------

echo "Generating release.json..."

WHEEL_BASENAME="$(basename "${WHEEL_FILE}")"
WHEEL_CHECKSUM="$(sha256_file "${WHEEL_FILE}")"

# Build checksum map and shell-verifiable manifest with Python to avoid
# quoting/escaping issues from hand-rolled JSON.
python3 - <<PY
import json
import hashlib
import os
from pathlib import Path

bundle_root = Path("${BUNDLE_ROOT}")

# First pass: compute checksums for all existing payload files.
checksums = {}
for f in sorted(bundle_root.rglob("*")):
    if not f.is_file():
        continue
    rel = f.relative_to(bundle_root).as_posix()
    if rel == "checksums.sha256":
        continue
    checksums[rel] = hashlib.sha256(f.read_bytes()).hexdigest()

# Write release.json so it is also covered by the payload manifest.
data = {
    "version": "${VERSION}",
    "state_schema_version": "${VERSION}",
    "platform": "linux",
    "arch": "x86_64",
    "build_time": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "migrations": {
        "preflight": None,
        "apply": None,
        "rollback_supported": False,
    },
    "artifacts": {
        "backend_wheel": {
            "path": f"wheels/{WHEEL_BASENAME}",
            "checksum_sha256": WHEEL_CHECKSUM,
        },
        "frontend_standalone": {
            "path": "frontend-standalone",
        },
        "manager_agent": {
            "path": "manager-agent",
        },
    },
    "checksums": checksums,
}

with open(bundle_root / "release.json", "w") as fh:
    json.dump(data, fh, indent=2)

# Second pass: write checksums.sha256 including release.json itself.
checksum_lines = []
for f in sorted(bundle_root.rglob("*")):
    if not f.is_file():
        continue
    rel = f.relative_to(bundle_root).as_posix()
    if rel == "checksums.sha256":
        continue
    digest = hashlib.sha256(f.read_bytes()).hexdigest()
    checksum_lines.append(f"{digest}  {rel}\n")

(bundle_root / "checksums.sha256").write_text("".join(checksum_lines))
PY

echo "Release manifest written."

# ---------------------------------------------------------------------------
# Create tarball
# ---------------------------------------------------------------------------

mkdir -p "${OUTPUT_DIR}"
TARBALL="${OUTPUT_DIR}/blueprint-re-${VERSION}-linux-x86_64.tar.gz"

echo "Creating tarball: ${TARBALL}"
tar -czf "${TARBALL}" -C "${STAGING_DIR}" "${PAYLOAD_NAME}"

# Compute tarball checksum.
TARBALL_CHECKSUM="$(sha256_file "${TARBALL}")"
echo "Tarball checksum (SHA-256): ${TARBALL_CHECKSUM}"

# Write checksum file.
printf '%s  %s\n' "${TARBALL_CHECKSUM}" "$(basename "${TARBALL}")" > "${TARBALL}.sha256"

echo ""
echo "Release bundle complete:"
echo "  ${TARBALL}"
echo "  ${TARBALL}.sha256"
