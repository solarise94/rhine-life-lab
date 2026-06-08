"""Tests for Feature 50: Server Project Directory Workspace Management (Phase 1)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException

from app.core.config import Settings, get_settings
from app.models.project import ProjectRegistry, ProjectRegistryEntry
from app.services.project_service import ProjectService
from app.services.utils import atomic_write_json, read_json, utc_now


def _make_minimal_project_json(root: Path, project_id: str, name: str) -> None:
    (root / "project.json").write_text(
        json.dumps({
            "project_id": project_id,
            "name": name,
            "status": "active",
            "schema_version": "0.1.0",
            "current_goal": "test",
            "created_at": utc_now(),
            "updated_at": utc_now(),
        })
    )
    (root / "graph").mkdir(exist_ok=True)
    for fname in ["cards.json", "modules.json", "assets.json", "claims.json", "runs.json", "report.json", "graph.json"]:
        (root / "graph" / fname).write_text("[]" if fname != "graph.json" else "{}")


class TestProjectRegistry(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmpdir.name) / "workspace"
        self.data_root.mkdir(parents=True)
        self.settings = Settings(data_root=self.data_root)
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()
        self.tmpdir.cleanup()

    def _svc(self) -> ProjectService:
        with patch("app.services.project_service.get_settings", return_value=self.settings):
            return ProjectService()

    def test_registry_missing_legacy_projects_still_listed(self) -> None:
        """Legacy projects under data_root appear even when registry is missing."""
        svc = self._svc()
        legacy_root = self.data_root / "legacy-proj"
        legacy_root.mkdir()
        _make_minimal_project_json(legacy_root, "legacy-proj", "Legacy")

        projects = svc.list_projects()
        ids = {p.project_id for p in projects}
        self.assertIn("legacy-proj", ids)

    def test_corrupted_registry_does_not_hide_legacy(self) -> None:
        """Corrupted registry file does not hide legacy projects."""
        svc = self._svc()
        registry_path = self.data_root / "_system" / "project_registry.json"
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text("NOT JSON AT ALL", encoding="utf-8")

        legacy_root = self.data_root / "legacy-proj"
        legacy_root.mkdir()
        _make_minimal_project_json(legacy_root, "legacy-proj", "Legacy")

        projects = svc.list_projects()
        ids = {p.project_id for p in projects}
        self.assertIn("legacy-proj", ids)

    def test_registry_wins_over_legacy_dedupe(self) -> None:
        """When registry and legacy have same project_id, registry entry wins."""
        svc = self._svc()
        managed_dir = Path(self.tmpdir.name) / "managed"
        managed_dir.mkdir()

        # Legacy project in data_root
        legacy_root = self.data_root / "shared-id"
        legacy_root.mkdir()
        _make_minimal_project_json(legacy_root, "shared-id", "Legacy")

        # Managed project outside data_root
        _make_minimal_project_json(managed_dir, "shared-id", "Managed")
        registry = ProjectRegistry(
            items=[
                ProjectRegistryEntry(
                    project_id="shared-id",
                    name="Managed",
                    project_root=str(managed_dir),
                    root_kind="managed_project_directory",
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            ]
        )
        atomic_write_json(self.data_root / "_system" / "project_registry.json", registry.model_dump())

        projects = svc.list_projects()
        match = next((p for p in projects if p.project_id == "shared-id"), None)
        self.assertIsNotNone(match)
        self.assertEqual(match.name, "Managed")
        self.assertEqual(match.project_root, str(managed_dir))


class TestCreateFromDirectory(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmpdir.name) / "workspace"
        self.data_root.mkdir(parents=True)
        self.user_home = Path(self.tmpdir.name) / "home" / "user"
        self.user_home.mkdir(parents=True)
        self.settings = Settings(data_root=self.data_root)
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()
        self.tmpdir.cleanup()

    def _svc(self) -> ProjectService:
        with patch("app.services.project_service.get_settings", return_value=self.settings):
            svc = ProjectService()
            svc._workspace_roots = lambda: [
                {"root_id": "home", "label": "Home", "path": str(self.user_home.resolve())}
            ]
            svc.data_directory_roots = lambda: [
                {"root_id": "home", "label": "Home", "path": str(self.user_home.resolve())}
            ]
            return svc

    def test_creates_work_directory_and_registry(self) -> None:
        """Successful creation scaffolds work/, writes registry, .gitignore has work/**."""
        svc = self._svc()
        # Create the data directory first (it must exist for mounting)
        (self.user_home / "oaa-2").mkdir()
        state = svc.create_project_from_directory(
            root_id="home",
            parent_path="",
            directory_name="oaa-2",
            project_id="oaa-2",
            name="OAA 2",
            current_goal="Analyze OAA 2 data",
        )
        self.assertEqual(state.project_id, "oaa-2")
        self.assertEqual(state.root_kind, "managed_project_directory")

        # Managed project is under data_root, NOT inside the data directory
        managed_root = svc.project_path("oaa-2")
        self.assertTrue((managed_root / "work").is_dir())
        self.assertTrue((managed_root / "project.json").exists())

        # Data directory is untouched (no Blueprint state scaffolded inside it)
        data_dir = self.user_home / "oaa-2"
        self.assertTrue(data_dir.is_dir())
        self.assertFalse((data_dir / "project.json").exists())

        gitignore = (managed_root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("work/**", gitignore)

        registry_path = self.data_root / "_system" / "project_registry.json"
        self.assertTrue(registry_path.exists())
        raw = read_json(registry_path, {"items": []})
        self.assertEqual(len(raw["items"]), 1)
        self.assertEqual(raw["items"][0]["project_id"], "oaa-2")

    def test_project_id_conflict_does_not_create_target(self) -> None:
        """If project_id already exists, no directory should be created."""
        svc = self._svc()
        svc.create_project(
            project_id="existing-proj",
            name="Existing",
            current_goal="test",
        )
        with self.assertRaises(HTTPException) as ctx:
            svc.create_project_from_directory(
                root_id="home",
                parent_path="",
                directory_name="new-dir",
                project_id="existing-proj",
                name="New",
                current_goal="test",
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertFalse((self.user_home / "new-dir").exists())

    def test_existing_non_empty_allowed(self) -> None:
        """Non-empty existing data directory is allowed (plan: mounted data directories may be existing and non-empty)."""
        svc = self._svc()
        target = self.user_home / "has-stuff"
        target.mkdir()
        (target / "readme.txt").write_text("hello")

        state = svc.create_project_from_directory(
            root_id="home",
            parent_path="",
            directory_name="has-stuff",
            project_id="has-stuff",
            name="Has Stuff",
            current_goal="test",
        )
        self.assertEqual(state.project_id, "has-stuff")
        # Managed project is under data_root, not inside the data directory
        project_root = svc.project_path("has-stuff")
        self.assertTrue(project_root.exists())
        # Data directory is untouched (no Blueprint state scaffolded inside it)
        self.assertFalse((target / "project.json").exists())

    def test_existing_git_allowed(self) -> None:
        """Directory containing .git is allowed (plan: MVP should not write git state into mounted data directory, so .git is not a blocker)."""
        svc = self._svc()
        target = self.user_home / "git-repo"
        target.mkdir()
        (target / ".git").mkdir()

        state = svc.create_project_from_directory(
            root_id="home",
            parent_path="",
            directory_name="git-repo",
            project_id="git-repo",
            name="Git Repo",
            current_goal="test",
        )
        self.assertEqual(state.project_id, "git-repo")
        # Managed project root is under data_root, not inside the data directory
        self.assertFalse((target / "project.json").exists())
        # .git inside data directory is untouched
        self.assertTrue((target / ".git").exists())

    def test_path_traversal_rejected(self) -> None:
        """Path traversal is rejected."""
        svc = self._svc()
        with self.assertRaises(HTTPException) as ctx:
            svc.create_project_from_directory(
                root_id="home",
                parent_path="../..",
                directory_name="etc",
                project_id="etc",
                name="Etc",
                current_goal="test",
            )
        self.assertIn(ctx.exception.status_code, {403, 404})

    def test_rollback_on_scaffold_failure(self) -> None:
        """If scaffold fails after creating a new directory, the directory is removed."""
        svc = self._svc()
        read_only_parent = self.user_home / "readonly"
        read_only_parent.mkdir()
        os.chmod(str(read_only_parent), 0o555)
        try:
            with self.assertRaises(Exception):
                svc.create_project_from_directory(
                    root_id="home",
                    parent_path="readonly",
                    directory_name="will-fail",
                    project_id="will-fail",
                    name="Will Fail",
                    current_goal="test",
                )
            self.assertFalse((read_only_parent / "will-fail").exists())
        finally:
            os.chmod(str(read_only_parent), 0o755)


class TestWorkspaceRootsEndpoint(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmpdir.name) / "workspace"
        self.data_root.mkdir(parents=True)
        self.user_home = Path(self.tmpdir.name) / "home" / "user"
        self.user_home.mkdir(parents=True)
        self.settings = Settings(data_root=self.data_root)
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()
        self.tmpdir.cleanup()

    def _svc(self) -> ProjectService:
        with patch("app.services.project_service.get_settings", return_value=self.settings):
            svc = ProjectService()
            test_roots = [
                {"root_id": "home", "label": "Home", "path": str(self.user_home.resolve())}
            ]
            svc._workspace_roots = lambda: test_roots
            svc.data_directory_roots = lambda: test_roots
            return svc

    def test_workspace_roots_list_directories(self) -> None:
        """Endpoint lists directories only and respects pagination."""
        from app.api.workspace_roots import list_workspace_entries

        svc = self._svc()
        # Create a mix of files and dirs
        for i in range(5):
            (self.user_home / f"dir_{i}").mkdir()
            (self.user_home / f"file_{i}.txt").write_text("x")

        resp = list_workspace_entries("home", "", "directory", None, False, svc)
        self.assertEqual(resp["root_id"], "home")
        names = {item["name"] for item in resp["items"]}
        self.assertTrue(all(n.startswith("dir_") for n in names))
        self.assertFalse(any(n.startswith("file_") for n in names))
        self.assertEqual(len(resp["items"]), 5)
        self.assertIsNone(resp["next_cursor"])

    def test_workspace_roots_rejects_traversal(self) -> None:
        """Path traversal is rejected by endpoint."""
        from app.api.workspace_roots import list_workspace_entries
        from fastapi import HTTPException

        svc = self._svc()
        with self.assertRaises(HTTPException) as ctx:
            list_workspace_entries("home", "../..", "directory", None, False, svc)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_workspace_roots_rejects_symlink_escape(self) -> None:
        """Symlink pointing outside root is rejected."""
        from app.api.workspace_roots import list_workspace_entries

        svc = self._svc()
        evil = Path(self.tmpdir.name) / "evil"
        evil.mkdir()
        (self.user_home / "escape").symlink_to(evil)

        resp = list_workspace_entries("home", "", "all", None, False, svc)
        names = {item["name"] for item in resp["items"]}
        # The symlink itself should not appear because it escapes the root
        self.assertNotIn("escape", names)


class TestWorkEntriesEndpoint(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmpdir.name) / "workspace"
        self.data_root.mkdir(parents=True)
        self.settings = Settings(data_root=self.data_root)
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()
        self.tmpdir.cleanup()

    def _svc(self) -> ProjectService:
        with patch("app.services.project_service.get_settings", return_value=self.settings):
            return ProjectService()

    def test_work_entries_lists_files(self) -> None:
        """work-entries endpoint lists files and directories under work/."""
        from app.api.files import list_project_work_entries

        svc = self._svc()
        svc.create_project(project_id="test-proj", name="Test", current_goal="test")
        work = self.data_root / "test-proj" / "work"
        (work / "data.csv").write_text("a,b\n1,2")
        (work / "subdir").mkdir()

        resp = list_project_work_entries("test-proj", "", "all", None, False, svc)
        self.assertEqual(resp["project_id"], "test-proj")
        names = {item["name"] for item in resp["items"]}
        self.assertIn("data.csv", names)
        self.assertIn("subdir", names)

    def test_work_entries_404_for_missing_project(self) -> None:
        """work-entries returns 404 when project does not exist."""
        from app.api.files import list_project_work_entries
        from fastapi import HTTPException

        svc = self._svc()
        with self.assertRaises(HTTPException) as ctx:
            list_project_work_entries("no-such-proj", "", "all", None, False, svc)
        self.assertEqual(ctx.exception.status_code, 404)


class TestWorkspaceWriteMode(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmpdir.name) / "workspace"
        self.data_root.mkdir(parents=True)
        self.settings = Settings(data_root=self.data_root)
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()
        self.tmpdir.cleanup()

    def _svc(self) -> ProjectService:
        with patch("app.services.project_service.get_settings", return_value=self.settings):
            return ProjectService()

    def test_execution_guard_serializes_workspace_write(self) -> None:
        """workspace_write runs use per-project lock regardless of sandbox."""
        from app.services.worker_service import WorkerService
        from app.services.manifest_service import ManifestService
        from app.services.runtime_approval_service import RuntimeApprovalService

        svc = self._svc()
        svc.create_project(project_id="test-proj", name="Test", current_goal="test")

        worker_service = WorkerService(
            project_service=svc,
            manifest_service=ManifestService(svc),
            runtime_approval_service=RuntimeApprovalService(svc),
        )

        # workspace_write should acquire a lock (not None)
        guard, kind = worker_service._acquire_execution_guard("test-proj", "card-1", sandboxed=True, execution_mode="workspace_write")
        self.assertIsNotNone(guard)
        self.assertEqual(kind, "composite")
        guard.release()

    def test_update_runtime_preferences_persists_execution_mode(self) -> None:
        """PUT runtime-preferences with execution_mode persists and affects task packet."""
        svc = self._svc()
        svc.create_project(project_id="test-proj", name="Test", current_goal="test")
        updated = svc.update_project_runtime_preferences("test-proj", {"execution_mode": "workspace_write"})
        self.assertEqual(updated.execution_mode, "workspace_write")
        prefs = svc.get_project_runtime_preferences("test-proj")
        self.assertEqual(prefs.execution_mode, "workspace_write")

    def test_task_packet_workspace_write_allows_work_path(self) -> None:
        """workspace_write task packet includes work/ in allowed_paths."""
        from app.models.cards import Card
        from app.models.output_contracts import CardOutputSpec
        from app.services.worker_service import WorkerService
        from app.services.manifest_service import ManifestService
        from app.services.runtime_approval_service import RuntimeApprovalService

        svc = self._svc()
        svc.create_project(project_id="test-proj", name="Test", current_goal="test")
        svc.update_project_runtime_preferences("test-proj", {"execution_mode": "workspace_write"})

        # Seed a minimal graph with one card so _task_packet can resolve inputs.
        store = svc.graph_store("test-proj")
        card = Card(
            card_id="card-1",
            card_type="run",
            title="Test Card",
            status="planned",
            summary="Test",
            inputs=[],
            outputs=[
                CardOutputSpec(
                    role="output",
                    label="Output",
                    artifact_class="document",
                    accepted_formats=["csv"],
                    path_hint="results/card-1/run-1/output.csv",
                ),
            ],
        )
        store.save_cards([card])

        ws = WorkerService(
            project_service=svc,
            manifest_service=ManifestService(svc),
            runtime_approval_service=RuntimeApprovalService(svc),
        )
        packet = ws._task_packet(
            project_id="test-proj",
            run_id="run-1",
            card=card,
            assets=[],
            worker_type="test",
        )
        self.assertEqual(packet.execution_policy.mode, "workspace_write")
        self.assertIn("work/", packet.allowed_paths)
        self.assertIn("runs/run-1/", packet.allowed_paths)
        self.assertIn("scripts/generated/run-1/", packet.allowed_paths)

    def test_workspace_write_launch_uses_absolute_env_paths_with_relative_run_context(self) -> None:
        """workspace_write uses absolute paths in env even when run_context has relative run_dir."""
        from app.workers.command_worker import CommandTemplateWorkerAdapter
        from app.models.runs import TaskPacket, RunContext, ExecutionPolicy
        from app.models.executor import ExecutorContext, ExecutorToolPolicy, RuntimeBindings

        adapter = CommandTemplateWorkerAdapter()
        adapter.command_template = "echo hello"
        project_root = self.data_root / "test-proj"
        project_root.mkdir()
        run_dir = project_root / "runs" / "run-1"
        run_dir.mkdir(parents=True)

        packet = TaskPacket(
            task_id="run-1",
            project_id="test-proj",
            card_id="card-1",
            goal="test",
            worker_instructions="test",
            run_context=RunContext(
                run_id="run-1",
                worker_type="test",
                project_root=str(project_root),
                run_dir="runs/run-1",
                result_dir="results/card-1/run-1",
            ),
            execution_policy=ExecutionPolicy(mode="workspace_write"),
            executor_context=ExecutorContext(
                tool_policy=ExecutorToolPolicy(),
                runtime_bindings=RuntimeBindings(),
            ),
        )
        packet_path = run_dir / "task_packet.json"
        packet_path.write_text(packet.model_dump_json())

        spec = adapter.build_launch_spec(
            packet=packet,
            packet_path=packet_path,
            run_dir=run_dir,
            project_root=project_root,
            settings=self.settings,
        )
        work_dir = project_root / "work"
        self.assertEqual(str(spec.cwd), str(work_dir))
        self.assertTrue(work_dir.exists())
        self.assertEqual(spec.environment["BLUEPRINT_RESULT_DIR"], str(project_root / "results/card-1/run-1"))
        self.assertEqual(spec.environment["BLUEPRINT_USER_WORKSPACE"], str(work_dir))
        self.assertEqual(spec.environment["BLUEPRINT_RUNTIME_WORKING_DIR"], str(work_dir))
        # Prompt should use env var references, not relative paths
        prompt_text = Path(spec.environment["BLUEPRINT_EXECUTOR_PROMPT"]).read_text()
        self.assertIn("$BLUEPRINT_TASK_PACKET", prompt_text)
        self.assertIn("$BLUEPRINT_MANIFEST_CANDIDATE_PATH", prompt_text)

    def test_workspace_write_creates_missing_work_dir_for_legacy_project(self) -> None:
        """workspace_write launch auto-creates work/ if missing."""
        from app.workers.command_worker import CommandTemplateWorkerAdapter
        from app.models.runs import TaskPacket, RunContext, ExecutionPolicy
        from app.models.executor import ExecutorContext, ExecutorToolPolicy, RuntimeBindings

        adapter = CommandTemplateWorkerAdapter()
        adapter.command_template = "echo hello"
        project_root = self.data_root / "test-proj"
        project_root.mkdir()
        run_dir = project_root / "runs" / "run-1"
        run_dir.mkdir(parents=True)
        # work/ does NOT exist yet
        self.assertFalse((project_root / "work").exists())

        packet = TaskPacket(
            task_id="run-1",
            project_id="test-proj",
            card_id="card-1",
            goal="test",
            worker_instructions="test",
            run_context=RunContext(
                run_id="run-1",
                worker_type="test",
                project_root=str(project_root),
                run_dir="runs/run-1",
                result_dir="results/card-1/run-1",
            ),
            execution_policy=ExecutionPolicy(mode="workspace_write"),
            executor_context=ExecutorContext(
                tool_policy=ExecutorToolPolicy(),
                runtime_bindings=RuntimeBindings(),
            ),
        )
        packet_path = run_dir / "task_packet.json"
        packet_path.write_text(packet.model_dump_json())

        spec = adapter.build_launch_spec(
            packet=packet,
            packet_path=packet_path,
            run_dir=run_dir,
            project_root=project_root,
            settings=self.settings,
        )
        self.assertTrue((project_root / "work").exists())
        self.assertEqual(str(spec.cwd), str(project_root / "work"))

    def test_workspace_write_bwrap_includes_work_bind(self) -> None:
        """workspace_write bwrap args include --bind for work/."""
        from app.workers.command_worker import CommandTemplateWorkerAdapter
        from app.models.runs import TaskPacket, RunContext, ExecutionPolicy
        from app.models.executor import ExecutorContext, ExecutorToolPolicy, RuntimeBindings

        # Skip if bwrap is not available
        import shutil
        if not shutil.which("bwrap"):
            self.skipTest("bwrap not available")

        adapter = CommandTemplateWorkerAdapter()
        adapter.command_template = "echo hello"
        project_root = self.data_root / "test-proj"
        project_root.mkdir()
        run_dir = project_root / "runs" / "run-1"
        run_dir.mkdir(parents=True)
        work_dir = project_root / "work"
        work_dir.mkdir()

        # Mock sandbox mode to bwrap
        class FakeSettings:
            executor_sandbox_mode = "bwrap"
            executor_host_root_readonly = True
            executor_conda_base = ""
            executor_extra_ro_binds = ""
            executor_max_concurrent_runs = 1

        packet = TaskPacket(
            task_id="run-1",
            project_id="test-proj",
            card_id="card-1",
            goal="test",
            worker_instructions="test",
            run_context=RunContext(
                run_id="run-1",
                worker_type="test",
                project_root=str(project_root),
                run_dir=str(run_dir),
                result_dir="results/card-1/run-1",
            ),
            execution_policy=ExecutionPolicy(mode="workspace_write"),
            executor_context=ExecutorContext(
                tool_policy=ExecutorToolPolicy(),
                runtime_bindings=RuntimeBindings(),
            ),
        )
        packet_path = run_dir / "task_packet.json"
        packet_path.write_text(packet.model_dump_json())

        spec = adapter.build_launch_spec(
            packet=packet,
            packet_path=packet_path,
            run_dir=run_dir,
            project_root=project_root,
            settings=FakeSettings(),
        )
        self.assertTrue(spec.sandboxed)
        cmd_str = " ".join(spec.command)
        self.assertIn(f"--bind {work_dir} {work_dir}", cmd_str)
        self.assertIn(f"--chdir {work_dir}", cmd_str)

    def _exec_report_executor_result_script(self, run_dir: Path, project_root: Path, run_id: str) -> dict:
        """Generate and exec the report_executor_result.py script, returning its namespace."""
        from app.workers.command_worker import CommandTemplateWorkerAdapter
        script_path = run_dir / "report_executor_result.py"
        CommandTemplateWorkerAdapter._write_executor_result_tool(script_path)
        script = script_path.read_text()
        namespace: dict = {"__name__": "not_main"}
        # The script imports from app.*; ensure backend is on path
        import sys
        if str(Path(__file__).parent.parent) not in sys.path:
            sys.path.insert(0, str(Path(__file__).parent.parent))
        exec(compile(script, str(script_path), "exec"), namespace)
        return namespace

    def test_workspace_write_manifest_rejects_work_dir_output(self) -> None:
        """Generated executor script rejects manifest created_assets placed in work/."""
        import os
        from app.models.runs import TaskPacket, ExecutionPolicy, TaskOutputSpec

        project_root = self.data_root / "test-proj"
        project_root.mkdir()
        run_dir = project_root / "runs" / "run-1"
        run_dir.mkdir(parents=True)
        work_dir = project_root / "work"
        work_dir.mkdir()
        results_dir = project_root / "results" / "card-1" / "run-1"
        results_dir.mkdir(parents=True)

        # Create manifest with output in work/
        manifest_path = run_dir / "manifest.candidate.json"
        manifest_path.write_text(json.dumps({
            "run_id": "run-1",
            "status": "success",
            "summary": "test",
            "created_assets": [
                {
                    "role": "output",
                    "path": "work/output.csv",
                    "label": "Output",
                    "artifact_class": "document",
                    "format": "csv",
                }
            ],
        }))
        (work_dir / "output.csv").write_text("a,b\n1,2")

        packet = TaskPacket(
            task_id="run-1",
            project_id="test-proj",
            card_id="card-1",
            goal="test",
            worker_instructions="test",
            allowed_paths=["work/", "runs/run-1/", "results/card-1/run-1/", "scripts/generated/run-1/"],
            expected_outputs=[
                TaskOutputSpec(
                    role="output",
                    label="Output",
                    artifact_class="document",
                    accepted_formats=["csv"],
                    path_hint="results/card-1/run-1/output.csv",
                ),
            ],
            execution_policy=ExecutionPolicy(mode="workspace_write"),
        )

        env = os.environ.copy()
        env["BLUEPRINT_RUN_DIR"] = str(run_dir)
        env["BLUEPRINT_PROJECT_ROOT"] = str(project_root)
        env["BLUEPRINT_RUN_ID"] = "run-1"
        old_environ = dict(os.environ)
        try:
            os.environ.update(env)
            ns = self._exec_report_executor_result_script(run_dir, project_root, "run-1")
            manifest, errors = ns["_validate_candidate_manifest"](manifest_path, packet)
        finally:
            os.environ.clear()
            os.environ.update(old_environ)

        self.assertIsNotNone(manifest)
        self.assertTrue(
            any("must not be placed in work/" in e for e in errors),
            f"Expected work/ rejection, got: {errors}"
        )

    def test_guarded_manifest_work_dir_rejected_by_allowed_paths(self) -> None:
        """guarded mode: manifest with created_assets in work/ rejected by allowed_paths."""
        import os
        from app.models.runs import TaskPacket, ExecutionPolicy, TaskOutputSpec

        project_root = self.data_root / "test-proj"
        project_root.mkdir()
        run_dir = project_root / "runs" / "run-1"
        run_dir.mkdir(parents=True)
        work_dir = project_root / "work"
        work_dir.mkdir()

        manifest_path = run_dir / "manifest.candidate.json"
        manifest_path.write_text(json.dumps({
            "run_id": "run-1",
            "status": "success",
            "summary": "test",
            "created_assets": [
                {
                    "role": "output",
                    "path": "work/output.csv",
                    "label": "Output",
                    "artifact_class": "document",
                    "format": "csv",
                }
            ],
        }))
        (work_dir / "output.csv").write_text("a,b\n1,2")

        packet = TaskPacket(
            task_id="run-1",
            project_id="test-proj",
            card_id="card-1",
            goal="test",
            worker_instructions="test",
            allowed_paths=["runs/run-1/", "results/card-1/run-1/", "scripts/generated/run-1/"],
            expected_outputs=[
                TaskOutputSpec(
                    role="output",
                    label="Output",
                    artifact_class="document",
                    accepted_formats=["csv"],
                    path_hint="results/card-1/run-1/output.csv",
                ),
            ],
            execution_policy=ExecutionPolicy(mode="guarded"),
        )

        env = os.environ.copy()
        env["BLUEPRINT_RUN_DIR"] = str(run_dir)
        env["BLUEPRINT_PROJECT_ROOT"] = str(project_root)
        env["BLUEPRINT_RUN_ID"] = "run-1"
        old_environ = dict(os.environ)
        try:
            os.environ.update(env)
            ns = self._exec_report_executor_result_script(run_dir, project_root, "run-1")
            manifest, errors = ns["_validate_candidate_manifest"](manifest_path, packet)
        finally:
            os.environ.clear()
            os.environ.update(old_environ)

        self.assertIsNotNone(manifest)
        self.assertTrue(
            any("outside allowed_paths" in e for e in errors),
            f"Expected allowed_paths rejection, got: {errors}"
        )

    def test_workspace_write_prompt_states_input_paths_project_root_relative(self) -> None:
        """Prompt explicitly states input paths are project-root-relative."""
        from app.workers.command_worker import CommandTemplateWorkerAdapter
        from app.models.runs import TaskPacket, ExecutionPolicy
        from app.models.executor import ExecutorContext, ExecutorToolPolicy, RuntimeBindings

        adapter = CommandTemplateWorkerAdapter()
        adapter.command_template = "echo hello"
        packet = TaskPacket(
            task_id="run-1",
            project_id="test-proj",
            card_id="card-1",
            goal="test",
            worker_instructions="test",
            execution_policy=ExecutionPolicy(mode="workspace_write"),
            executor_context=ExecutorContext(
                tool_policy=ExecutorToolPolicy(),
                runtime_bindings=RuntimeBindings(),
            ),
        )
        prompt = adapter._render_executor_prompt(packet)
        self.assertIn("project-root-relative", prompt)
        self.assertIn("$BLUEPRINT_PROJECT_ROOT", prompt)

    def test_workspace_write_prompt_warns_work_dir_persists(self) -> None:
        """workspace_write prompt warns that work/ persists across runs."""
        from app.workers.command_worker import CommandTemplateWorkerAdapter
        from app.models.runs import TaskPacket, ExecutionPolicy
        from app.models.executor import ExecutorContext, ExecutorToolPolicy, RuntimeBindings

        adapter = CommandTemplateWorkerAdapter()
        adapter.command_template = "echo hello"
        packet = TaskPacket(
            task_id="run-1",
            project_id="test-proj",
            card_id="card-1",
            goal="test",
            worker_instructions="test",
            execution_policy=ExecutionPolicy(mode="workspace_write"),
            executor_context=ExecutorContext(
                tool_policy=ExecutorToolPolicy(),
                runtime_bindings=RuntimeBindings(),
            ),
        )
        prompt = adapter._render_executor_prompt(packet)
        self.assertIn("persist across runs", prompt)

        # Guarded mode should NOT contain the persistence warning
        packet_guarded = packet.model_copy(update={"execution_policy": ExecutionPolicy(mode="guarded")})
        prompt_guarded = adapter._render_executor_prompt(packet_guarded)
        self.assertNotIn("persist across runs", prompt_guarded)

    def test_workspace_write_brief_states_input_paths_project_root_relative(self) -> None:
        """Brief explicitly states input paths are project-root-relative."""
        from app.workers.command_worker import CommandTemplateWorkerAdapter
        from app.models.runs import TaskPacket, ExecutionPolicy

        adapter = CommandTemplateWorkerAdapter()
        adapter.command_template = "echo hello"
        packet = TaskPacket(
            task_id="run-1",
            project_id="test-proj",
            card_id="card-1",
            goal="test",
            worker_instructions="test",
            execution_policy=ExecutionPolicy(mode="workspace_write"),
        )
        brief = adapter._render_executor_brief(packet)
        self.assertIn("project-root-relative", brief)

    def test_workspace_write_lock_returns_specific_error_code(self) -> None:
        """workspace_write project lock busy returns specific error_code."""
        from app.services.worker_service import WorkerService
        from app.services.manifest_service import ManifestService
        from app.services.runtime_approval_service import RuntimeApprovalService

        svc = self._svc()
        svc.create_project(project_id="test-proj", name="Test", current_goal="test")
        svc.update_project_runtime_preferences("test-proj", {"execution_mode": "workspace_write"})

        worker_service = WorkerService(
            project_service=svc,
            manifest_service=ManifestService(svc),
            runtime_approval_service=RuntimeApprovalService(svc),
        )

        # First acquisition should succeed
        guard1, kind1 = worker_service._acquire_execution_guard("test-proj", "card-1", sandboxed=True, execution_mode="workspace_write")
        self.assertIsNotNone(guard1)
        self.assertEqual(kind1, "composite")

        # Second acquisition should fail with specific kind
        guard2, kind2 = worker_service._acquire_execution_guard("test-proj", "card-2", sandboxed=True, execution_mode="workspace_write")
        self.assertIsNone(guard2)
        self.assertEqual(kind2, "workspace_write_project_lock")

        guard1.release()

    def test_result_dir_env_handles_absolute_result_dir(self) -> None:
        """_resolve_project_path handles both relative and absolute result_dir."""
        from app.workers.command_worker import CommandTemplateWorkerAdapter

        project_root = self.data_root / "test-proj"
        project_root.mkdir()

        adapter = CommandTemplateWorkerAdapter()

        # Relative path
        rel = adapter._resolve_project_path(project_root, "results/card-1/run-1")
        self.assertEqual(rel, project_root / "results" / "card-1" / "run-1")

        # Absolute path (should be returned as-is)
        abs_path = str(self.data_root / "absolute" / "results")
        result = adapter._resolve_project_path(project_root, abs_path)
        self.assertEqual(result, Path(abs_path))

    def test_workspace_write_bwrap_includes_work_bind_when_host_root_not_readonly(self) -> None:
        """workspace_write bwrap includes work bind even when host root is not readonly."""
        from app.workers.command_worker import CommandTemplateWorkerAdapter
        from app.models.runs import TaskPacket, RunContext, ExecutionPolicy
        from app.models.executor import ExecutorContext, ExecutorToolPolicy, RuntimeBindings

        import shutil
        if not shutil.which("bwrap"):
            self.skipTest("bwrap not available")

        adapter = CommandTemplateWorkerAdapter()
        adapter.command_template = "echo hello"
        project_root = self.data_root / "test-proj"
        project_root.mkdir()
        run_dir = project_root / "runs" / "run-1"
        run_dir.mkdir(parents=True)
        work_dir = project_root / "work"
        work_dir.mkdir()

        class FakeSettings:
            executor_sandbox_mode = "bwrap"
            executor_host_root_readonly = False  # <-- key difference
            executor_conda_base = ""
            executor_extra_ro_binds = ""
            executor_max_concurrent_runs = 1

        packet = TaskPacket(
            task_id="run-1",
            project_id="test-proj",
            card_id="card-1",
            goal="test",
            worker_instructions="test",
            run_context=RunContext(
                run_id="run-1",
                worker_type="test",
                project_root=str(project_root),
                run_dir=str(run_dir),
                result_dir="results/card-1/run-1",
            ),
            execution_policy=ExecutionPolicy(mode="workspace_write"),
            executor_context=ExecutorContext(
                tool_policy=ExecutorToolPolicy(),
                runtime_bindings=RuntimeBindings(),
            ),
        )
        packet_path = run_dir / "task_packet.json"
        packet_path.write_text(packet.model_dump_json())

        spec = adapter.build_launch_spec(
            packet=packet,
            packet_path=packet_path,
            run_dir=run_dir,
            project_root=project_root,
            settings=FakeSettings(),
        )
        self.assertTrue(spec.sandboxed)
        cmd_str = " ".join(spec.command)
        self.assertIn(f"--bind {work_dir} {work_dir}", cmd_str)
        self.assertIn(f"--chdir {work_dir}", cmd_str)


class TestRegisterWorkAsset(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmpdir.name) / "workspace"
        self.data_root.mkdir(parents=True)
        self.settings = Settings(data_root=self.data_root)
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()
        self.tmpdir.cleanup()

    def _svc(self) -> ProjectService:
        with patch("app.services.project_service.get_settings", return_value=self.settings):
            return ProjectService()

    def test_register_work_asset_creates_asset(self) -> None:
        """Registering a work/ file creates an asset with correct metadata."""
        from app.api.files import register_work_asset
        from fastapi import HTTPException

        svc = self._svc()
        svc.create_project(project_id="test-proj", name="Test", current_goal="test")
        work = self.data_root / "test-proj" / "work"
        (work / "data.csv").write_text("a,b\n1,2")

        resp = register_work_asset("test-proj", type("Req", (), {"path": "data.csv"})(), svc)
        asset = resp["asset"]
        self.assertEqual(asset["path"], "work/data.csv")
        self.assertEqual(asset["asset_type"], "document")
        self.assertEqual(asset["metadata"]["source"], "work_directory")

    def test_register_work_asset_rejects_directory(self) -> None:
        """Registering a directory is rejected."""
        from app.api.files import register_work_asset
        from fastapi import HTTPException

        svc = self._svc()
        svc.create_project(project_id="test-proj", name="Test", current_goal="test")
        work = self.data_root / "test-proj" / "work"
        (work / "subdir").mkdir()

        with self.assertRaises(HTTPException) as ctx:
            register_work_asset("test-proj", type("Req", (), {"path": "subdir"})(), svc)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_register_work_asset_rejects_traversal(self) -> None:
        """Path traversal in register is rejected."""
        from app.api.files import register_work_asset
        from fastapi import HTTPException

        svc = self._svc()
        svc.create_project(project_id="test-proj", name="Test", current_goal="test")

        with self.assertRaises(HTTPException) as ctx:
            register_work_asset("test-proj", type("Req", (), {"path": "../secret"})(), svc)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_register_work_asset_is_idempotent_by_path(self) -> None:
        """Registering the same work file twice returns the existing asset."""
        from app.api.files import register_work_asset

        svc = self._svc()
        svc.create_project(project_id="test-proj", name="Test", current_goal="test")
        work = self.data_root / "test-proj" / "work"
        (work / "data.csv").write_text("a,b\n1,2")

        resp1 = register_work_asset("test-proj", type("Req", (), {"path": "data.csv"})(), svc)
        resp2 = register_work_asset("test-proj", type("Req", (), {"path": "data.csv"})(), svc)
        self.assertEqual(resp1["asset"]["asset_id"], resp2["asset"]["asset_id"])
