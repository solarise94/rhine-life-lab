from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


class TestInstallDeployPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parents[2]
        cls.install_script = cls.repo_root / "scripts" / "install_blueprint_re.sh"
        cls.deploy_script = cls.repo_root / "scripts" / "deploy_user_systemd.sh"

    def test_install_script_rejects_missing_deepseek_key(self) -> None:
        """Install must fail early with a clear message when BLUEPRINT_DEEPSEEK_API_KEY is missing."""
        env = os.environ.copy()
        env.pop("BLUEPRINT_DEEPSEEK_API_KEY", None)
        result = subprocess.run(
            ["bash", "-n", str(self.install_script)],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, msg="Install script must have valid bash syntax")

        # Verify the script contains the pre-deploy key check
        script_text = self.install_script.read_text(encoding="utf-8")
        self.assertIn("BLUEPRINT_DEEPSEEK_API_KEY is required", script_text)
        self.assertIn("exit 1", script_text)

    def test_deploy_script_requires_deepseek_key(self) -> None:
        """Deploy must fail-fast when BLUEPRINT_DEEPSEEK_API_KEY is not set."""
        script_text = self.deploy_script.read_text(encoding="utf-8")
        self.assertIn("BLUEPRINT_DEEPSEEK_API_KEY is required for production deployment", script_text)
        self.assertIn("exit 1", script_text)


class TestDeployBackendEnvWhitelist(unittest.TestCase):
    """Verify that deploy_user_systemd.sh writes a complete backend.env whitelist."""

    REQUIRED_BACKEND_KEYS = {
        "PATH",
        "BACKEND_HOST",
        "BACKEND_PORT",
        "BLUEPRINT_FRONTEND_ORIGIN",
        "BLUEPRINT_MANAGER_BACKEND",
        "BLUEPRINT_DEFAULT_WORKER_TYPE",
        "BLUEPRINT_PI_MANAGER_URL",
        "BLUEPRINT_BACKEND_API_BASE_URL",
        "BLUEPRINT_DEFAULT_PYTHON_RUNTIME",
        "BLUEPRINT_DEFAULT_R_RUNTIME",
        "BLUEPRINT_EXECUTOR_CONDA_BASE",
        "BLUEPRINT_DEEPSEEK_API_KEY",
        "BLUEPRINT_DEEPSEEK_API_BASE_URL",
        "BLUEPRINT_PI_DEEPSEEK_BASE_URL",
        "BLUEPRINT_MANAGER_MODEL",
        "BLUEPRINT_MANAGER_TEMPERATURE",
        "BLUEPRINT_MANAGER_MAX_TOKENS",
        "BLUEPRINT_MANAGER_TIMEOUT_SECONDS",
        "BLUEPRINT_EXECUTOR_MODEL",
        "BLUEPRINT_REVIEWER_MODEL",
        "BLUEPRINT_LIBRARY_SUMMARIZER_MODEL",
        "BLUEPRINT_REVIEWER_MAX_TOKENS",
        "BLUEPRINT_REVIEWER_MAX_TURNS",
        "BLUEPRINT_EXECUTOR_SANDBOX_MODE",
        "BLUEPRINT_EXECUTOR_MAX_CONCURRENT_RUNS",
        "BLUEPRINT_EXECUTOR_HOST_ROOT_READONLY",
        "BLUEPRINT_EXECUTOR_EXTRA_RO_BINDS",
        "BLUEPRINT_INTERNAL_TOOL_TOKEN",
        "BLUEPRINT_PI_COMMAND_JSON",
        "BLUEPRINT_OPENCODE_COMMAND_JSON",
        "BLUEPRINT_CLAUDE_CODE_COMMAND_JSON",
    }

    @classmethod
    def setUpClass(cls) -> None:
        cls.deploy_script = (
            Path(__file__).resolve().parents[2] / "scripts" / "deploy_user_systemd.sh"
        )

    def test_backend_env_contains_all_required_keys(self) -> None:
        script_text = self.deploy_script.read_text(encoding="utf-8")

        # Extract the _write_env_once block for backend.env via line scan
        # (regex is too brittle for shell escaping across continuation lines)
        lines = script_text.splitlines()
        start_idx = None
        for idx, line in enumerate(lines):
            if "_write_env_once" in line and "backend.env" in line:
                start_idx = idx
                break
        self.assertIsNotNone(
            start_idx, "backend.env _write_env_once block not found in deploy script"
        )

        # Collect continuation lines: each starts with a quoted "KEY=..."
        collected = []
        j = start_idx + 1
        while j < len(lines):
            stripped = lines[j].strip()
            if not stripped.startswith('"'):
                break
            collected.append(stripped)
            if not stripped.rstrip().endswith("\\"):
                break
            j += 1

        # Extract key names from "KEY=value" lines
        found_keys = set()
        for line in collected:
            if line.startswith('"') and "=" in line:
                key = line[1:].split("=", 1)[0]
                found_keys.add(key)

        missing = self.REQUIRED_BACKEND_KEYS - found_keys
        self.assertEqual(
            missing,
            set(),
            f"backend.env whitelist missing keys: {sorted(missing)}",
        )

    def test_backend_env_does_not_auto_configure_codex(self) -> None:
        """Codex should not be in the default backend.env whitelist."""
        script_text = self.deploy_script.read_text(encoding="utf-8")
        self.assertNotIn(
            "BLUEPRINT_CODEX_COMMAND_JSON",
            script_text,
            "deploy script should not auto-write BLUEPRINT_CODEX_COMMAND_JSON",
        )


if __name__ == "__main__":
    unittest.main()
