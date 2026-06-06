import shutil
import tempfile
import unittest
from pathlib import Path

from app.core.config import get_settings
from app.services.project_service import ProjectService


class ProjectSnapshotSplitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="snapshot-split-test-")
        settings = get_settings()
        self._original_data_root = settings.data_root
        settings.data_root = Path(self.tmpdir)
        self.project_service = ProjectService()
        self.project_service.create_project(project_id="snap-test", name="Snap Test", current_goal="test")

    def tearDown(self) -> None:
        get_settings().data_root = self._original_data_root
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_core_snapshot_excludes_environment_fields(self):
        core = self.project_service.get_project_snapshot_core("snap-test")
        self.assertIn("summary", core)
        self.assertIn("project", core)
        self.assertIn("cards", core)
        self.assertIn("graph", core)
        self.assertIn("proposals", core)
        self.assertNotIn("worker_capabilities", core)
        self.assertNotIn("python_runtimes", core)
        self.assertNotIn("r_runtimes", core)
        self.assertNotIn("git_log", core)

    def test_environment_contains_runtime_fields(self):
        env = self.project_service.get_project_environment("snap-test")
        self.assertIn("worker_capabilities", env)
        self.assertIn("python_runtimes", env)
        self.assertIn("r_runtimes", env)
        self.assertIsInstance(env["worker_capabilities"], list)
        self.assertIsInstance(env["python_runtimes"], list)
        self.assertIsInstance(env["r_runtimes"], list)

    def test_full_snapshot_still_has_all_fields(self):
        full = self.project_service.get_project_snapshot("snap-test")
        self.assertIn("summary", full)
        self.assertIn("cards", full)
        self.assertIn("graph", full)
        self.assertIn("git_log", full)
        self.assertIn("worker_capabilities", full)
        self.assertIn("python_runtimes", full)
        self.assertIn("r_runtimes", full)


if __name__ == "__main__":
    unittest.main()
