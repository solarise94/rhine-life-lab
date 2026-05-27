from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestBlueprintPiLaunchScript(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[2]
        self.script_path = self.repo_root / "scripts" / "blueprint_pi_launch.sh"

    def _write_fake_pi_bin(self, bin_dir: Path) -> Path:
        pi_path = bin_dir / "pi"
        pi_path.write_text(
            """#!/bin/bash
printf 'pi=%s\n' "$0"
printf 'path=%s\n' "$PATH"
printf 'argv=%s\n' "$*"
""",
            encoding="utf-8",
        )
        pi_path.chmod(0o755)
        return pi_path

    def test_blueprint_pi_launch_uses_blueprint_pi_bin_directory_for_node_resolution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pi-launch-blueprint-pi-bin-") as tmpdir:
            root = Path(tmpdir)
            bin_dir = root / "custom-node" / "bin"
            bin_dir.mkdir(parents=True)
            pi_path = self._write_fake_pi_bin(bin_dir)
            prompt_path = root / "prompt.md"
            prompt_path.write_text("# Prompt\n", encoding="utf-8")

            env = os.environ.copy()
            env.update(
                {
                    "PATH": "/usr/bin:/bin",
                    "BLUEPRINT_AUTH_MODE": "cli_native",
                    "BLUEPRINT_PI_BIN": str(pi_path),
                }
            )

            result = subprocess.run(
                ["bash", str(self.script_path), str(prompt_path)],
                cwd=self.repo_root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn(f"pi={pi_path}", result.stdout)
            self.assertIn(f"path={bin_dir}:/usr/bin:/bin", result.stdout)
            self.assertIn(f"argv=--no-session --no-skills --no-context-files -p @{prompt_path}", result.stdout)

    def test_blueprint_pi_launch_finds_latest_home_nvm_install(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pi-launch-home-nvm-") as tmpdir:
            home_dir = Path(tmpdir)
            older_bin = home_dir / ".nvm" / "versions" / "node" / "v20.1.0" / "bin"
            newer_bin = home_dir / ".nvm" / "versions" / "node" / "v22.22.2" / "bin"
            older_bin.mkdir(parents=True)
            newer_bin.mkdir(parents=True)
            self._write_fake_pi_bin(older_bin)
            newer_pi = self._write_fake_pi_bin(newer_bin)
            prompt_path = home_dir / "prompt.md"
            prompt_path.write_text("# Prompt\n", encoding="utf-8")

            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home_dir),
                    "PATH": "/usr/bin:/bin",
                    "BLUEPRINT_AUTH_MODE": "cli_native",
                }
            )
            env.pop("BLUEPRINT_PI_BIN", None)

            result = subprocess.run(
                ["bash", str(self.script_path), str(prompt_path)],
                cwd=self.repo_root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn(f"pi={newer_pi}", result.stdout)
            self.assertIn(f"path={newer_bin}:/usr/bin:/bin", result.stdout)
            self.assertIn(f"argv=--no-session --no-skills --no-context-files -p @{prompt_path}", result.stdout)

    def test_blueprint_pi_launch_reports_install_guidance_when_pi_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pi-launch-missing-") as tmpdir:
            prompt_path = Path(tmpdir) / "prompt.md"
            prompt_path.write_text("# Prompt\n", encoding="utf-8")

            env = os.environ.copy()
            env.update(
                {
                    "HOME": tmpdir,
                    "PATH": "/usr/bin:/bin",
                    "BLUEPRINT_AUTH_MODE": "cli_native",
                }
            )
            env.pop("BLUEPRINT_PI_BIN", None)

            result = subprocess.run(
                ["bash", str(self.script_path), str(prompt_path)],
                cwd=self.repo_root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("pi CLI not found", result.stderr)
            self.assertIn("Agent should configure BLUEPRINT_PI_BIN", result.stderr)
            self.assertIn("managed pi install flow", result.stderr)

    def test_blueprint_pi_launch_project_api_uses_env_key_not_cli_arg(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pi-launch-project-api-") as tmpdir:
            root = Path(tmpdir)
            bin_dir = root / "custom-node" / "bin"
            bin_dir.mkdir(parents=True)
            pi_path = self._write_fake_pi_bin(bin_dir)
            prompt_path = root / "prompt.md"
            prompt_path.write_text("# Prompt\n", encoding="utf-8")

            env = os.environ.copy()
            env.update(
                {
                    "PATH": "/usr/bin:/bin",
                    "BLUEPRINT_AUTH_MODE": "project_api",
                    "BLUEPRINT_PI_BIN": str(pi_path),
                    "BLUEPRINT_DEEPSEEK_API_KEY": "sk-test-project-api",
                    "BLUEPRINT_EXECUTOR_MODEL": "deepseek-v4-pro",
                }
            )

            result = subprocess.run(
                ["bash", str(self.script_path), str(prompt_path)],
                cwd=self.repo_root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("argv=--provider deepseek --model deepseek-v4-pro --no-session --no-skills --no-context-files", result.stdout)
            self.assertNotIn("--api-key", result.stdout)
            self.assertNotIn("sk-test-project-api", result.stdout)
