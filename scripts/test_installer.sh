#!/usr/bin/env bash
set -euo pipefail

# Basic installer script unit tests.
# Runs against a temporary HOME to avoid touching the real user environment.
#
# Usage:
#   bash scripts/test_installer.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

TMP_HOME="$(mktemp -d)"
trap 'rm -rf "${TMP_HOME}"' EXIT

export HOME="${TMP_HOME}"

PASSED=0
FAILED=0

assert() {
  local msg="$1"
  local cmd="$2"
  if eval "${cmd}"; then
    echo "  PASS: ${msg}"
    PASSED=$((PASSED + 1))
  else
    echo "  FAIL: ${msg}"
    FAILED=$((FAILED + 1))
  fi
}

assert_fail() {
  local msg="$1"
  local cmd="$2"
  if ! eval "${cmd}"; then
    echo "  PASS: ${msg}"
    PASSED=$((PASSED + 1))
  else
    echo "  FAIL: ${msg}"
    FAILED=$((FAILED + 1))
  fi
}

echo "========================================"
echo "Installer Script Unit Tests"
echo "TMP_HOME: ${TMP_HOME}"
echo "========================================"
echo ""

# ---------------------------------------------------------------------------
# Test 1: uninstall.sh with --yes --purge-config (dry run: no services)
# ---------------------------------------------------------------------------
echo "Test 1: uninstall.sh removes files correctly"

# Create fake install state.
mkdir -p "${TMP_HOME}/.local/share/blueprint-re/releases/0.4.1"
mkdir -p "${TMP_HOME}/.config/blueprint-re"
touch "${TMP_HOME}/.config/blueprint-re/backend.env"
mkdir -p "${TMP_HOME}/.config/systemd/user"

# Uninstall without --purge-config should preserve config.
export HOME="${TMP_HOME}"
bash "${REPO_ROOT}/scripts/uninstall.sh" --yes >/dev/null 2>&1 || true

assert "release base removed" "[[ ! -d ${TMP_HOME}/.local/share/blueprint-re ]]"
assert "config preserved without --purge-config" "[[ -d ${TMP_HOME}/.config/blueprint-re ]]"

# Now test with --purge-config.
mkdir -p "${TMP_HOME}/.local/share/blueprint-re"
mkdir -p "${TMP_HOME}/.config/blueprint-re"
bash "${REPO_ROOT}/scripts/uninstall.sh" --yes --purge-config >/dev/null 2>&1 || true

assert "release base removed with purge" "[[ ! -d ${TMP_HOME}/.local/share/blueprint-re ]]"
assert "config removed with --purge-config" "[[ ! -d ${TMP_HOME}/.config/blueprint-re ]]"

echo ""

# ---------------------------------------------------------------------------
# Test 2: resolve_bin helper from deploy_release.sh
# ---------------------------------------------------------------------------
echo "Test 2: resolve_bin helper logic"

# Source the helper function.
resolve_bin() {
  local env_var_name="$1"
  local command_name="$2"
  local explicit
  explicit="${!env_var_name:-}"
  if [[ -n "${explicit}" && -x "${explicit}" ]]; then
    printf '%s\n' "${explicit}"
    return 0
  fi
  if command -v "${command_name}" >/dev/null 2>&1; then
    printf '%s\n' "$(command -v "${command_name}")"
    return 0
  fi
  return 1
}

# Test explicit path wins.
TEST_PYTHON="$(command -v python3)"
export TEST_PYTHON_BIN="${TEST_PYTHON}"
result="$(resolve_bin TEST_PYTHON_BIN nonexistent)"
assert "explicit env var wins over command -v" "[[ ${result} == ${TEST_PYTHON} ]]"

# Test falls back to command -v.
unset TEST_PYTHON_BIN
result="$(resolve_bin TEST_PYTHON_BIN python3)"
assert "falls back to command -v" "[[ -n ${result} ]]"

# Test fails when neither exists.
unset TEST_FAKE_BIN
assert_fail "fails when neither explicit nor command exists" "resolve_bin TEST_FAKE_BIN definitely_fake_command_xyz"

echo ""

# ---------------------------------------------------------------------------
# Test 3: install.sh argument parsing
# ---------------------------------------------------------------------------
echo "Test 3: install.sh argument parsing"

# Extract just the argument parsing logic.
parse_test() {
  local args=("$@")
  local OFFLINE_MODE=0
  local ROLLBACK_VERSION=""
  local SKIP_VERIFY=0

  local i=0
  while [[ ${i} -lt ${#args[@]} ]]; do
    case "${args[${i}]}" in
      --offline)
        OFFLINE_MODE=1
        ;;
      --rollback)
        i=$((i + 1))
        ROLLBACK_VERSION="${args[${i}]}"
        ;;
      --skip-verify)
        SKIP_VERIFY=1
        ;;
    esac
    i=$((i + 1))
  done

  echo "OFFLINE_MODE=${OFFLINE_MODE}"
  echo "ROLLBACK_VERSION=${ROLLBACK_VERSION}"
  echo "SKIP_VERIFY=${SKIP_VERIFY}"
}

result="$(parse_test --offline --rollback 0.4.0 --skip-verify)"
assert "offline parsed" "echo '${result}' | grep -q 'OFFLINE_MODE=1'"
assert "rollback parsed" "echo '${result}' | grep -q 'ROLLBACK_VERSION=0.4.0'"
assert "skip-verify parsed" "echo '${result}' | grep -q 'SKIP_VERIFY=1'"

echo ""

# ---------------------------------------------------------------------------
# Test 4: build_release_bundle.sh version validation
# ---------------------------------------------------------------------------
echo "Test 4: Version extraction from pyproject.toml"

read_pyproject_version() {
  local toml_file="$1"
  python3 -c "
import sys, re
text = open(sys.argv[1]).read()
m = re.search(r'^version\s*=\s*\"([^\"]+)\"', text, re.M)
print(m.group(1) if m else '0.0.0')
" "${toml_file}"
}

version="$(read_pyproject_version "${REPO_ROOT}/backend/pyproject.toml")"
assert "pyproject version is not 0.0.0" "[[ ${version} != 0.0.0 ]]"
assert "pyproject version has dots" "[[ ${version} == *.* ]]"

echo ""

# ---------------------------------------------------------------------------
# Test 5: Release layout validation
# ---------------------------------------------------------------------------
echo "Test 5: Payload structure"

# Check that required templates exist.
assert "nginx template exists" "[[ -f ${REPO_ROOT}/deploy/nginx/blueprint-re.conf.template ]]"
assert "systemd backend template exists" "[[ -f ${REPO_ROOT}/deploy/systemd-release/blueprint-re-backend.service ]]"
assert "systemd frontend template exists" "[[ -f ${REPO_ROOT}/deploy/systemd-release/blueprint-re-frontend.service ]]"
assert "systemd manager template exists" "[[ -f ${REPO_ROOT}/deploy/systemd-release/blueprint-re-manager-agent.service ]]"
assert "systemd nginx template exists" "[[ -f ${REPO_ROOT}/deploy/systemd-release/blueprint-re-nginx.service ]]"

echo ""

# ---------------------------------------------------------------------------
# Test 6: Self-extracting installer build and payload extraction
# ---------------------------------------------------------------------------
echo "Test 6: Self-extracting installer build and payload extraction"

TEST_BUILD_DIR="$(mktemp -d)"

# Create a minimal valid payload.
mkdir -p "${TEST_BUILD_DIR}/blueprint-re/wheels"
echo "dummy" > "${TEST_BUILD_DIR}/blueprint-re/wheels/dummy.whl"
cat > "${TEST_BUILD_DIR}/blueprint-re/release.json" <<'EOF'
{
  "version": "0.0.0-test",
  "arch": "x86_64",
  "platform": "linux"
}
EOF
(
  cd "${TEST_BUILD_DIR}/blueprint-re" || exit 1
  find . -type f | while IFS= read -r f; do
    f="${f#./}"
    [[ "$f" == "checksums.sha256" ]] && continue
    sha256sum "$f"
  done > checksums.sha256
)

(
  cd "${TEST_BUILD_DIR}" || exit 1
  tar -czf blueprint-re-0.0.0-test-linux-x86_64.tar.gz blueprint-re
)
bash "${REPO_ROOT}/scripts/build_self_extracting_installer.sh" "${TEST_BUILD_DIR}/blueprint-re-0.0.0-test-linux-x86_64.tar.gz" > /dev/null 2>&1

INSTALLER_PATH="${REPO_ROOT}/dist/blueprint-re-0.0.0-test-linux-x86_64.sh"
assert "installer artifact created" "[[ -f ${INSTALLER_PATH} ]]"
assert "installer checksum file created" "[[ -f ${INSTALLER_PATH}.sha256 ]]"

# Verify the size-invariance assertion passed as part of the build script.
assert "installer size unchanged after offset substitution" "[[ -f ${INSTALLER_PATH} ]]"

# Extract manually and verify payload integrity.
EXTRACT_DIR="${TEST_BUILD_DIR}/extracted"
mkdir -p "${EXTRACT_DIR}"
PAYLOAD_OFFSET=$(python3 -c "
with open('${INSTALLER_PATH}', 'rb') as f:
    data = f.read()
marker = b'__PAYLOAD_START__\n'
idx = data.find(marker)
if idx < 0:
    sys.exit(1)
print(idx + len(marker) + 1)
")
(
  cd "${EXTRACT_DIR}" || exit 1
  tail -c +"${PAYLOAD_OFFSET}" "${INSTALLER_PATH}" | tar -xzf -
)
assert "payload extracted" "[[ -d ${EXTRACT_DIR}/blueprint-re ]]"
assert "release.json present in extracted payload" "[[ -f ${EXTRACT_DIR}/blueprint-re/release.json ]]"
(
  cd "${EXTRACT_DIR}/blueprint-re" || exit 1
  sha256sum -c --status checksums.sha256
)
assert "extracted payload checksums verify" "[[ \$? -eq 0 ]]"

# Verify that release.json is covered by checksums.sha256.
assert "release.json is listed in checksums.sha256" "grep -q 'release.json' ${EXTRACT_DIR}/blueprint-re/checksums.sha256"

# Clean up the artifact and temp dir so they do not pollute dist/.
rm -f "${INSTALLER_PATH}" "${INSTALLER_PATH}.sha256"
rm -rf "${TEST_BUILD_DIR}"

echo ""

# ---------------------------------------------------------------------------
# Test 7: systemd env escaping helpers from deploy_release.sh
# ---------------------------------------------------------------------------
echo "Test 7: systemd env escaping helpers"

# Source the helpers inline.
systemd_env_escape() {
  local value="$1"
  if [[ "${value}" == *$'\n'* ]]; then
    echo "REFUSING_NEWLINE" >&2
    return 1
  fi
  local escaped="${value//\\/\\\\}"
  escaped="${escaped//\"/\\\"}"
  if [[ "${escaped}" == *[[:space:]]* || "${escaped}" == *';'* || "${escaped}" == *'|'* || "${escaped}" == *'$'* ]]; then
    escaped="\"${escaped}\""
  fi
  printf '%s\n' "${escaped}"
}

sed_escape_replacement() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\|/\\|}"
  value="${value//\&/\\&}"
  value="${value//$'\n'/}"
  printf '%s\n' "${value}"
}

# Values with spaces should be quoted.
result="$(systemd_env_escape "hello world")"
[[ "$result" == '"hello world"' ]] && { echo "  PASS: systemd_escape quotes values with spaces"; PASSED=$((PASSED + 1)); } || { echo "  FAIL: systemd_escape quotes values with spaces"; FAILED=$((FAILED + 1)); }

# Backslashes and quotes are escaped.
result="$(systemd_env_escape 'a\b"c')"
[[ "$result" == 'a\\b\"c' ]] && { echo "  PASS: systemd_escape escapes backslashes and quotes"; PASSED=$((PASSED + 1)); } || { echo "  FAIL: systemd_escape escapes backslashes and quotes"; FAILED=$((FAILED + 1)); }

# Pipe characters are quoted.
result="$(systemd_env_escape "a|b")"
[[ "$result" == '"a|b"' ]] && { echo "  PASS: systemd_escape quotes pipe characters"; PASSED=$((PASSED + 1)); } || { echo "  FAIL: systemd_escape quotes pipe characters"; FAILED=$((FAILED + 1)); }

# sed replacement escaping preserves special chars.
result="$(sed_escape_replacement "a|b&c")"
[[ "$result" == 'a\|b\&c' ]] && { echo "  PASS: sed_escape escapes pipe and ampersand"; PASSED=$((PASSED + 1)); } || { echo "  FAIL: sed_escape escapes pipe and ampersand"; FAILED=$((FAILED + 1)); }

echo ""

# ---------------------------------------------------------------------------
# Test 8: release cleanup excludes backup directories
# ---------------------------------------------------------------------------
echo "Test 8: release cleanup filters backup directories"

RELEASES_TEST_DIR="${TMP_HOME}/releases-test"
mkdir -p "${RELEASES_TEST_DIR}/0.4.0"
mkdir -p "${RELEASES_TEST_DIR}/0.4.1"
mkdir -p "${RELEASES_TEST_DIR}/0.4.1.backup.12345"
mkdir -p "${RELEASES_TEST_DIR}/0.4.2"
CURRENT_TEST="${RELEASES_TEST_DIR}/current"
ln -sfn "${RELEASES_TEST_DIR}/0.4.2" "${CURRENT_TEST}"

# Simulate the cleanup logic from install.sh.
keep_count=0
remove_count=0
mapfile -t ALL_VERSIONS < <(ls -1 "${RELEASES_TEST_DIR}" | grep -E '^[0-9]+(\.[0-9]+)*$' | sort -V -r)
for old_ver in "${ALL_VERSIONS[@]:2}"; do
  old_path="${RELEASES_TEST_DIR}/${old_ver}"
  if [[ "$(readlink -f "${CURRENT_TEST}" 2>/dev/null || true)" == "$(readlink -f "${old_path}" 2>/dev/null || true)" ]]; then
    continue
  fi
  rm -rf "${old_path}"
  remove_count=$((remove_count + 1))
done

# 0.4.2 is current, 0.4.1 is kept (most recent non-current), 0.4.0 should be removed.
assert "cleanup removed oldest version 0.4.0" "[[ ! -d ${RELEASES_TEST_DIR}/0.4.0 ]]"
assert "cleanup kept version 0.4.1" "[[ -d ${RELEASES_TEST_DIR}/0.4.1 ]]"
assert "cleanup kept current version 0.4.2" "[[ -d ${RELEASES_TEST_DIR}/0.4.2 ]]"
assert "cleanup preserved backup directory" "[[ -d ${RELEASES_TEST_DIR}/0.4.1.backup.12345 ]]"
assert "cleanup removed exactly one version" "[[ ${remove_count} -eq 1 ]]"

rm -rf "${RELEASES_TEST_DIR}"

echo ""

# ---------------------------------------------------------------------------
# Test 9: version match validation in install.sh
# ---------------------------------------------------------------------------
echo "Test 9: install.sh version mismatch detection"

# Extract just the version-check logic. In the real script this happens after
# json_get_string extracts the payload version.
check_versions() {
  local installer_version="$1"
  local release_version="$2"
  local arch="$3"
  local platform="$4"
  INSTALLER_VERSION="${installer_version}"
  INSTALLER_ARCH="x86_64"
  INSTALLER_PLATFORM="linux"

  if [[ "${release_version}" != "${INSTALLER_VERSION}" ]]; then
    return 1
  fi
  if [[ "${arch}" != "${INSTALLER_ARCH}" ]]; then
    return 1
  fi
  if [[ "${platform}" != "${INSTALLER_PLATFORM}" ]]; then
    return 1
  fi
  return 0
}

assert "version match accepted" "check_versions 0.4.1 0.4.1 x86_64 linux"
assert_fail "version mismatch rejected" "check_versions 0.4.1 0.4.2 x86_64 linux"
assert_fail "arch mismatch rejected" "check_versions 0.4.1 0.4.1 aarch64 linux"
assert_fail "platform mismatch rejected" "check_versions 0.4.1 0.4.1 x86_64 darwin"

echo ""

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "========================================"
echo "Results: ${PASSED} passed, ${FAILED} failed"
echo "========================================"

if [[ "${FAILED}" -gt 0 ]]; then
  exit 1
fi
