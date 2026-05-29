import shutil
import tempfile
import unittest
from pathlib import Path

from app.core.config import get_settings
from app.services.project_event_service import ProjectEventService
from app.services.project_service import ProjectService


class ProjectEventServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="project-events-test-")
        settings = get_settings()
        self._original_data_root = settings.data_root
        settings.data_root = Path(self.tmpdir)
        self.project_service = ProjectService()
        self.project_service.create_project(
            project_id="event-project",
            name="Event Project",
            current_goal="Test project events",
            seed_demo=False,
        )
        self.service = ProjectEventService(self.project_service)

    def tearDown(self) -> None:
        get_settings().data_root = self._original_data_root
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_subscribe_yields_baseline_then_emitted_event(self) -> None:
        stream = self.service.subscribe_events("event-project")
        baseline = next(stream)
        self.assertEqual(baseline["type"], "project_state_baseline")
        self.assertTrue(baseline["requires_refetch"])

        emitted = self.service.emit(
            "event-project",
            reason="run_status_changed",
            card_id="card_1",
            run_id="run_1",
            status="running",
        )
        received = next(stream)
        self.assertEqual(received, emitted)
        self.assertEqual(received["seq"], baseline["seq"] + 1)
        self.assertEqual(received["card_id"], "card_1")
        self.assertEqual(received["run_id"], "run_1")

    def test_revision_is_persisted_in_graph_metadata(self) -> None:
        self.service.emit("event-project", reason="card_updated", card_id="card_1")
        self.service.emit("event-project", reason="card_updated", card_id="card_1")

        metadata = self.project_service.graph_store("event-project").load_metadata()
        self.assertEqual(metadata["project_event_revision"], 2)


if __name__ == "__main__":
    unittest.main()
