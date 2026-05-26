"""Tests for structured argv command templates.

These tests verify that the JSON argv template system correctly handles paths
containing spaces, avoiding the shlex.split issues that can occur with the
legacy string template format.

Uses unittest (not pytest) to match the repo's test conventions.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.workers import agent_cli_executor
from app.workers.agent_cli_worker import AgentCliWorkerAdapter
from app.workers.command_worker import CommandTemplateWorkerAdapter


class TestArgvTemplateInnerWrapper(unittest.TestCase):
    """Test that agent_cli_executor argv JSON preserves paths with spaces."""

    def test_agent_cli_executor_launch_argv_template_preserves_spaces(self):
        """Verify _render_launch_argv_template preserves paths with spaces."""
        with tempfile.TemporaryDirectory() as tmp:
            # Set up directories with spaces
            project_root = Path(tmp) / "My Project"
            project_root.mkdir()
            run_dir = project_root / "runs" / "task-001"
            run_dir.mkdir(parents=True)
            result_dir = project_root / "results" / "card-001" / "task-001"
            result_dir.mkdir(parents=True)

            # Create a minimal task packet
            packet = {
                "task_id": "task-001",
                "project_id": "proj-001",
                "run_context": {
                    "result_dir": "results/card-001/task-001",
                },
                "expected_outputs": [],
            }
            packet_path = run_dir / "task_packet.json"
            packet_path.write_text(json.dumps(packet), encoding="utf-8")

            # Create a prompt path
            prompt_path = run_dir / "executor_prompt.md"
            prompt_path.write_text("# Test prompt", encoding="utf-8")

            # Define an argv template with {repo_root} and {executor_prompt_path}
            argv_template = [
                "bash",
                "{repo_root}/scripts/blueprint_pi_launch.sh",
                "{executor_prompt_path}",
            ]

            # Render the template
            command = agent_cli_executor._render_launch_argv_template(
                argv_template,
                provider="pi",
                packet_path=packet_path,
                run_dir=run_dir,
                project_root=project_root,
                prompt_path=prompt_path,
            )

            # Verify the script path is a single element (not split)
            self.assertEqual(command[0], "bash")
            # The second element should contain the full script path
            self.assertIn("/scripts/blueprint_pi_launch.sh", command[1])
            # The third element should be the full prompt path (with spaces preserved)
            self.assertEqual(command[2], str(prompt_path))
            # Verify the prompt path contains the expected path
            self.assertIn("My Project", command[2])

    def test_render_argv_template_with_all_placeholders(self):
        """Verify all placeholders are substituted in argv template."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            project_root.mkdir()
            run_dir = project_root / "runs" / "task-001"
            run_dir.mkdir(parents=True)
            result_dir = project_root / "results" / "card-001" / "task-001"
            result_dir.mkdir(parents=True)

            packet = {
                "task_id": "task-001",
                "project_id": "proj-001",
                "run_context": {"result_dir": "results/card-001/task-001"},
                "expected_outputs": [],
            }
            packet_path = run_dir / "task_packet.json"
            packet_path.write_text(json.dumps(packet), encoding="utf-8")

            prompt_path = run_dir / "executor_prompt.md"
            prompt_path.write_text("# Test", encoding="utf-8")

            argv_template = [
                "{python}",
                "-m",
                "some.module",
                "--project-root",
                "{project_root}",
                "--run-dir",
                "{run_dir}",
                "--prompt",
                "{executor_prompt_path}",
                "--worker",
                "{worker_type}",
            ]

            command = agent_cli_executor._render_launch_argv_template(
                argv_template,
                provider="test",
                packet_path=packet_path,
                run_dir=run_dir,
                project_root=project_root,
            )

            self.assertEqual(command[0], sys.executable)
            self.assertEqual(command[4], str(project_root))
            self.assertEqual(command[6], str(run_dir))
            self.assertEqual(command[10], "test")

    def test_render_argv_template_unknown_placeholder_raises(self):
        """Verify unknown placeholders raise a helpful error."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            project_root.mkdir()
            run_dir = project_root / "runs" / "task-001"
            run_dir.mkdir(parents=True)
            result_dir = project_root / "results"
            result_dir.mkdir(parents=True)

            packet = {
                "task_id": "task-001",
                "run_context": {"result_dir": "results"},
                "expected_outputs": [],
            }
            packet_path = run_dir / "task_packet.json"
            packet_path.write_text(json.dumps(packet), encoding="utf-8")

            argv_template = ["echo", "{unknown_placeholder}"]

            with self.assertRaises(RuntimeError) as ctx:
                agent_cli_executor._render_launch_argv_template(
                    argv_template,
                    provider="test",
                    packet_path=packet_path,
                    run_dir=run_dir,
                    project_root=project_root,
                )
            self.assertIn("unknown_placeholder", str(ctx.exception))


class TestArgvTemplateRenderMethod(unittest.TestCase):
    """Test CommandTemplateWorkerAdapter._render_argv_template static method."""

    def test_render_argv_template_basic(self):
        """Basic substitution works."""
        template = ["{python}", "-m", "module", "--path", "{project_root}"]
        mapping = {"python": "/usr/bin/python3", "project_root": "/home/user/project"}
        result = CommandTemplateWorkerAdapter._render_argv_template(template, mapping)
        self.assertEqual(result, ["/usr/bin/python3", "-m", "module", "--path", "/home/user/project"])

    def test_render_argv_template_preserves_spaces(self):
        """Paths with spaces remain as single argv elements."""
        template = ["bash", "{script_path}", "{data_path}"]
        mapping = {
            "script_path": "/path/with spaces/script.sh",
            "data_path": "/data/My Project/file.txt",
        }
        result = CommandTemplateWorkerAdapter._render_argv_template(template, mapping)
        self.assertEqual(result[0], "bash")
        self.assertEqual(result[1], "/path/with spaces/script.sh")
        self.assertEqual(result[2], "/data/My Project/file.txt")
        # Verify no splitting occurred
        self.assertEqual(len(result), 3)

    def test_render_argv_template_unknown_placeholder(self):
        """Unknown placeholders raise RuntimeError."""
        template = ["echo", "{missing}"]
        mapping = {"other": "value"}
        with self.assertRaises(RuntimeError) as ctx:
            CommandTemplateWorkerAdapter._render_argv_template(template, mapping)
        self.assertIn("missing", str(ctx.exception))


class TestAgentCliWorkerAdapterArgvTemplate(unittest.TestCase):
    """Test AgentCliWorkerAdapter argv template integration."""

    def test_adapter_returns_argv_template(self):
        """Verify resolve_command_argv_template returns the wrapper argv list."""
        settings = SimpleNamespace(
            pi_command="bash /path/to/launch.sh {executor_prompt_path}",
            pi_command_json=["bash", "/path/to/launch.sh", "{executor_prompt_path}"],
        )

        adapter = AgentCliWorkerAdapter()
        adapter.name = "pi"
        adapter.provider = "pi"
        adapter.launch_template_setting_name = "pi_command"

        argv_template = adapter.resolve_command_argv_template(settings)
        self.assertIsNotNone(argv_template)
        # Should be the wrapper command, not the provider command
        self.assertEqual(argv_template[0], "{python}")
        self.assertEqual(argv_template[1], "-m")
        self.assertEqual(argv_template[2], "app.workers.agent_cli_executor")

    def test_adapter_resolves_launch_argv_template(self):
        """Verify resolve_launch_argv_template reads the *_command_json setting."""
        settings = SimpleNamespace(
            pi_command="legacy string template",
            pi_command_json=["bash", "{repo_root}/scripts/launch.sh", "{executor_prompt_path}"],
        )

        adapter = AgentCliWorkerAdapter()
        adapter.name = "pi"
        adapter.provider = "pi"
        adapter.launch_template_setting_name = "pi_command"

        launch_argv = adapter.resolve_launch_argv_template(settings)
        self.assertIsNotNone(launch_argv)
        self.assertEqual(launch_argv[0], "bash")
        self.assertIn("{repo_root}", launch_argv[1])

    def test_adapter_is_configured_with_json_only(self):
        """Adapter is configured when only JSON template is set."""
        settings_json_only = SimpleNamespace(
            pi_command=None,
            pi_command_json=["bash", "/path/to/script.sh", "{executor_prompt_path}"],
        )

        adapter = AgentCliWorkerAdapter()
        adapter.name = "pi"
        adapter.provider = "pi"
        adapter.launch_template_setting_name = "pi_command"

        self.assertTrue(adapter.is_configured(settings_json_only))

    def test_adapter_is_configured_with_string_only(self):
        """Adapter is configured when only string template is set (legacy)."""
        settings_string_only = SimpleNamespace(
            pi_command="bash /path/to/script.sh {executor_prompt_path}",
            pi_command_json=None,
        )

        adapter = AgentCliWorkerAdapter()
        adapter.name = "pi"
        adapter.provider = "pi"
        adapter.launch_template_setting_name = "pi_command"

        self.assertTrue(adapter.is_configured(settings_string_only))

    def test_adapter_not_configured_without_either(self):
        """Adapter is not configured when neither template is set."""
        settings_empty = SimpleNamespace(
            pi_command=None,
            pi_command_json=None,
        )

        adapter = AgentCliWorkerAdapter()
        adapter.name = "pi"
        adapter.provider = "pi"
        adapter.launch_template_setting_name = "pi_command"

        self.assertFalse(adapter.is_configured(settings_empty))


class TestAgentCliWorkerAdapterSpacePaths(unittest.TestCase):
    """Test that AgentCliWorkerAdapter.build_launch_spec preserves paths with spaces."""

    def test_build_launch_spec_preserves_space_paths(self):
        """Verify build_launch_spec output preserves space paths as single argv elements."""
        import tempfile
        from app.models.runs import TaskPacket, RunContext, ExecutionPolicy
        from app.models.executor import ExecutorContext, RuntimeBindings

        with tempfile.TemporaryDirectory() as tmp:
            # Create paths with spaces
            project_root = Path(tmp) / "New Project"
            project_root.mkdir()
            run_dir = project_root / "runs" / "run abc"
            run_dir.mkdir(parents=True)
            packet_path = run_dir / "task_packet.json"

            packet = TaskPacket(
                task_id="task-001",
                project_id="proj-001",
                card_id="card-001",
                card_title="Test Card",
                goal="Test goal",
                worker_instructions="Test instructions",
                run_context=RunContext(
                    run_id="task-001",
                    worker_type="pi",
                    project_root=str(project_root),
                    run_dir=str(run_dir),
                    result_dir="results/card-001/task-001",
                ),
                executor_context=ExecutorContext(
                    runtime_bindings=RuntimeBindings(),
                ),
                execution_policy=ExecutionPolicy(),
                expected_outputs=[],
                allowed_paths=[],
                readonly_paths=[],
                forbidden_paths=[],
            )
            packet_path.write_text(packet.model_dump_json(), encoding="utf-8")

            settings = SimpleNamespace(
                pi_command="bash /path/to/launch.sh {executor_prompt_path}",
                pi_command_json=["bash", "{repo_root}/scripts/launch.sh", "{executor_prompt_path}"],
                executor_sandbox_mode="none",
            )

            adapter = AgentCliWorkerAdapter()
            adapter.name = "pi"
            adapter.provider = "pi"
            adapter.launch_template_setting_name = "pi_command"

            spec = adapter.build_launch_spec(
                packet=packet,
                packet_path=packet_path,
                run_dir=run_dir,
                project_root=project_root,
                settings=settings,
            )

            command = spec.command
            # Find indices of key arguments
            task_packet_idx = command.index("--task-packet")
            run_dir_idx = command.index("--run-dir")
            project_root_idx = command.index("--project-root")

            # Verify the values after each flag are single argv elements containing spaces
            self.assertIn("New Project", command[task_packet_idx + 1])
            self.assertIn("run abc", command[run_dir_idx + 1])
            self.assertIn("New Project", command[project_root_idx + 1])

            # Verify no unintended splitting occurred (paths should be single elements)
            self.assertNotIn("New", command[project_root_idx + 2] if len(command) > project_root_idx + 2 else "")
            # More precisely: the element after --project-root should be the complete path
            self.assertTrue(
                command[project_root_idx + 1].endswith(str(project_root)),
                f"Expected project_root path, got: {command[project_root_idx + 1]}",
            )


class TestWrapperLaunchArgvTemplateSpacePaths(unittest.TestCase):
    """Test that wrapper _render_launch_argv_template preserves paths with spaces."""

    def test_wrapper_preserves_space_paths(self):
        """Verify _render_launch_argv_template preserves paths with spaces."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "My Project"
            project_root.mkdir()
            run_dir = project_root / "runs" / "task 001"
            run_dir.mkdir(parents=True)
            result_dir = project_root / "results" / "card 001" / "task 001"
            result_dir.mkdir(parents=True)

            packet = {
                "task_id": "task-001",
                "project_id": "proj-001",
                "run_context": {
                    "result_dir": "results/card 001/task 001",
                },
                "expected_outputs": [],
            }
            packet_path = run_dir / "task_packet.json"
            packet_path.write_text(json.dumps(packet), encoding="utf-8")

            template = [
                "bash",
                "{repo_root}/scripts/launch.sh",
                "{executor_prompt_path}",
                "--root",
                "{project_root}",
            ]

            command = agent_cli_executor._render_launch_argv_template(
                template,
                provider="pi",
                packet_path=packet_path,
                run_dir=run_dir,
                project_root=project_root,
            )

            # Verify all placeholders rendered as single argv elements
            self.assertEqual(command[0], "bash")
            self.assertIn("/scripts/launch.sh", command[1])
            self.assertIn("My Project", command[4])
            # Verify the project_root value is a single element (not split)
            self.assertTrue(
                command[4].endswith(str(project_root)),
                f"Expected complete path, got: {command[4]}",
            )

    def test_wrapper_preserves_space_paths_in_all_placeholders(self):
        """Verify all path placeholders with spaces remain as single argv elements."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "Research Project"
            project_root.mkdir()
            run_dir = project_root / "runs" / "batch run"
            run_dir.mkdir(parents=True)

            packet = {
                "task_id": "task-001",
                "project_id": "proj-001",
                "run_context": {"result_dir": "results/current"},
                "expected_outputs": [],
            }
            packet_path = run_dir / "task_packet.json"
            packet_path.write_text(json.dumps(packet), encoding="utf-8")

            template = [
                "{python}",
                "-m",
                "my_module",
                "--packet",
                "{task_packet_path}",
                "--run-dir",
                "{run_dir}",
                "--project-root",
                "{project_root}",
            ]

            command = agent_cli_executor._render_launch_argv_template(
                template,
                provider="test",
                packet_path=packet_path,
                run_dir=run_dir,
                project_root=project_root,
            )

            # Find indices
            packet_idx = command.index("--packet")
            run_dir_idx = command.index("--run-dir")
            project_root_idx = command.index("--project-root")

            # Each path should be a single argv element containing spaces
            self.assertIn("Research Project", command[packet_idx + 1])
            self.assertIn("batch run", command[run_dir_idx + 1])
            self.assertIn("Research Project", command[project_root_idx + 1])

            # Verify no extra elements were created by splitting
            self.assertEqual(command[packet_idx + 1], str(packet_path))
            self.assertEqual(command[run_dir_idx + 1], str(run_dir))
            self.assertEqual(command[project_root_idx + 1], str(project_root))

    def test_claude_code_prompt_file_prefix_is_preserved(self):
        """Claude Code -p needs @path so it reads the prompt file."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "Claude Project"
            project_root.mkdir()
            run_dir = project_root / "runs" / "task 001"
            run_dir.mkdir(parents=True)

            packet = {
                "task_id": "task-001",
                "project_id": "proj-001",
                "run_context": {"result_dir": "results/current"},
                "expected_outputs": [],
            }
            packet_path = run_dir / "task_packet.json"
            packet_path.write_text(json.dumps(packet), encoding="utf-8")

            template = [
                "claude",
                "-p",
                "@{executor_prompt_path}",
                "--output-format",
                "stream-json",
                "--verbose",
            ]

            command = agent_cli_executor._render_launch_argv_template(
                template,
                provider="claude_code",
                packet_path=packet_path,
                run_dir=run_dir,
                project_root=project_root,
            )

            self.assertEqual(command[0:2], ["claude", "-p"])
            self.assertEqual(command[2], f"@{run_dir / 'executor_prompt.md'}")


if __name__ == "__main__":
    unittest.main()
