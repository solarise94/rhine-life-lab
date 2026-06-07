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
            return svc

    def test_creates_work_directory_and_registry(self) -> None:
        """Successful creation scaffolds work/, writes registry, .gitignore has work/**."""
        svc = self._svc()
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

        target = self.user_home / "oaa-2"
        self.assertTrue((target / "work").is_dir())
        self.assertTrue((target / "project.json").exists())

        gitignore = (target / ".gitignore").read_text(encoding="utf-8")
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

    def test_existing_non_empty_rejected(self) -> None:
        """Non-empty existing directory is rejected."""
        svc = self._svc()
        target = self.user_home / "has-stuff"
        target.mkdir()
        (target / "readme.txt").write_text("hello")

        with self.assertRaises(HTTPException) as ctx:
            svc.create_project_from_directory(
                root_id="home",
                parent_path="",
                directory_name="has-stuff",
                project_id="has-stuff",
                name="Has Stuff",
                current_goal="test",
            )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_existing_git_rejected(self) -> None:
        """Directory containing .git is rejected."""
        svc = self._svc()
        target = self.user_home / "git-repo"
        target.mkdir()
        (target / ".git").mkdir()

        with self.assertRaises(HTTPException) as ctx:
            svc.create_project_from_directory(
                root_id="home",
                parent_path="",
                directory_name="git-repo",
                project_id="git-repo",
                name="Git Repo",
                current_goal="test",
            )
        self.assertEqual(ctx.exception.status_code, 409)

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
            svc._workspace_roots = lambda: [
                {"root_id": "home", "label": "Home", "path": str(self.user_home.resolve())}
            ]
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
