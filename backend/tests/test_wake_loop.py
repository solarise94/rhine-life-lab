import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.core.config import get_settings
from app.models.background import BackgroundWorkboardState, BackgroundWorkboardView, WorkboardItemRecord
from app.models.manager_auto import ManagerAutoState
from app.services.background_workboard_service import BackgroundWorkboardService
from app.services.manager_auto_service import ManagerAutoService
from app.services.manager_wake_service import ManagerWakeService
from app.services.project_service import ProjectService


class WakeLoopRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="blueprint-re-wake-loop-test-")
        self.settings = get_settings()
        self._original_data_root = self.settings.data_root
        self.settings.data_root = Path(self.tmpdir)
        self.project_service = ProjectService()
        self.project_service.create_project(
            project_id="test-project",
            name="Test Project",
            current_goal="E2E test wake loop",
            seed_demo=False,
        )

    def tearDown(self) -> None:
        self.settings.data_root = self._original_data_root
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_completed_item_default_not_actionable(self):
        """Completed items should NOT be actionable by default (Fix 4)."""
        # A card_run_reviewed item without explicit actionable_wake=true is NOT actionable
        item = {
            "item_id": "wi:run1:reviewed",
            "lane": "completed",
            "kind": "card_run_reviewed",
            "payload": {},
        }
        self.assertFalse(BackgroundWorkboardService._completed_item_is_actionable(item))

        # runtime_dependency_install_succeeded without actionable_wake is NOT actionable
        item_dep = {
            "item_id": "wi:dep1:succeeded",
            "lane": "completed",
            "kind": "runtime_dependency_install_succeeded",
            "payload": {},
        }
        self.assertFalse(BackgroundWorkboardService._completed_item_is_actionable(item_dep))

        # Only explicit opt-in makes it actionable
        item_opt_in = {
            "item_id": "wi:run2:reviewed",
            "lane": "completed",
            "kind": "card_run_reviewed",
            "payload": {"actionable_wake": True},
        }
        self.assertTrue(BackgroundWorkboardService._completed_item_is_actionable(item_opt_in))

    def test_signal_snapshot_returns_fingerprint_and_actionability(self):
        """signal_snapshot should return semantic fingerprint and actionability breakdown (Fix 1, 2)."""
        view = BackgroundWorkboardView(
            project_id="test-project",
            revision=1,
            counts={"ready_to_start": 1, "needs_manager": 1},
            running=[],
            todo=[],
            needs_manager=[
                {
                    "item_id": "needs_mgr",
                    "lane": "needs_manager",
                    "kind": "runtime_dependency_missing",
                    "card_id": "card_c",
                    "run_id": "run_1",
                    "status": "pending",
                    "payload": {"error_code": "package_not_found"},
                }
            ],
            completed=[],
            ready_to_start=[
                {
                    "item_id": "ready1",
                    "lane": "ready_to_start",
                    "kind": "ready_card",
                    "card_id": "card_a",
                    "status": "pending",
                }
            ],
            blocked_for_user=[],
            deferred=[],
        )
        snapshot = BackgroundWorkboardService._semantic_wake_snapshot_from_view(view)
        self.assertIn("fingerprint", snapshot)
        self.assertIn("fingerprint_items", snapshot)
        self.assertIn("actionability", snapshot)
        actionability = snapshot["actionability"]
        self.assertTrue(actionability["has_startable_frontier"])
        self.assertTrue(actionability["has_manager_actionable"])
        # ready item enters fingerprint; needs_manager enters as semantic key
        self.assertIn("ready:card_a", snapshot["fingerprint_items"])
        self.assertTrue(any(it.startswith("manager:run:run_1:") for it in snapshot["fingerprint_items"]))
        # todo does NOT enter fingerprint (intermediate state, not fresh work)
        self.assertFalse(any(it.startswith("todo:") for it in snapshot["fingerprint_items"]))

    def test_signal_snapshot_ready_projection_filter(self):
        """ready_to_start items for cards already running should be filtered (Fix 3)."""
        view = BackgroundWorkboardView(
            project_id="test-project",
            revision=1,
            counts={"ready_to_start": 1, "running": 1},
            running=[{"item_id": "running_task", "lane": "running", "kind": "background_task", "card_id": "card_running", "status": "processing"}],
            todo=[],
            needs_manager=[],
            completed=[],
            ready_to_start=[{"item_id": "ready_running", "lane": "ready_to_start", "kind": "ready_card", "card_id": "card_running", "status": "pending"}],
            blocked_for_user=[],
            deferred=[],
        )
        snapshot = BackgroundWorkboardService._semantic_wake_snapshot_from_view(view)
        # The ready item for a card that already has a running item should be excluded
        ready_items = [it for it in snapshot.get("fingerprint_items", []) if it.startswith("ready:")]
        self.assertEqual(len(ready_items), 0, "Ready projection for running card should be filtered")
        self.assertFalse(snapshot["actionability"]["has_startable_frontier"])

    def test_chain_budget_stops_enqueue(self):
        """evaluate_workboard_and_maybe_signal should stop auto when chain_count >= max_chain_count (Fix 7)."""
        wake_service = ManagerWakeService(self.project_service)
        service = ManagerAutoService(
            self.project_service,
            manager_wake_service=wake_service,
        )
        # Must inject background_workboard_service so evaluate runs past the None guard
        mock_bws = MagicMock()
        mock_bws.signal_snapshot.return_value = {
            "revision": 1,
            "counts": {},
            "has_actionable": False,
            "has_running": False,
            "has_blocked_for_user": False,
            "actionability": {},
            "fingerprint": "",
            "fingerprint_items": [],
        }
        service.background_workboard_service = mock_bws
        # Enable auto with a very low max_chain_count by pre-seeding state
        store = self.project_service.graph_store("test-project")
        graph = store.load_graph()
        state = ManagerAutoState(
            enabled=True,
            wake_allowed=True,
            owner_session_id="session_1",
            consume_workboard=True,
            chain_count=10,
            max_chain_count=10,
        )
        graph.metadata["manager_auto"] = state.model_dump()
        store.save_graph(graph)

        result = service.evaluate_workboard_and_maybe_signal("test-project", "session_1")
        self.assertFalse(result.enabled)
        self.assertEqual(result.stop_reason, "auto_chain_budget_exceeded")

    def test_fingerprint_dedupe_same_fingerprint_no_wake(self):
        """Same fingerprint should not enqueue another wake (Fix 1)."""
        wake_service = ManagerWakeService(self.project_service)
        service = ManagerAutoService(
            self.project_service,
            manager_wake_service=wake_service,
        )
        store = self.project_service.graph_store("test-project")
        graph = store.load_graph()
        state = ManagerAutoState(
            enabled=True,
            wake_allowed=True,
            owner_session_id="session_1",
            consume_workboard=True,
            chain_count=0,
            max_chain_count=10,
            last_signaled_workboard_fingerprint="sha1:abc123",
        )
        graph.metadata["manager_auto"] = state.model_dump()
        store.save_graph(graph)

        # Mock snapshot to return the same fingerprint
        mock_bws = MagicMock()
        mock_bws.signal_snapshot.return_value = {
            "revision": 999,
            "counts": {"ready_to_start": 1},
            "has_actionable": True,
            "has_running": False,
            "has_blocked_for_user": False,
            "actionability": {"has_manager_actionable": False, "has_startable_frontier": True},
            "fingerprint": "sha1:abc123",
            "fingerprint_items": ["ready:card_a"],
        }
        service.background_workboard_service = mock_bws

        result = service.evaluate_workboard_and_maybe_signal("test-project", "session_1")
        self.assertTrue(result.enabled)
        # Should NOT have updated last_wake_id because no wake was enqueued
        self.assertIsNone(result.last_wake_id)

    def test_settlement_requeue_guard_no_new_frontier(self):
        """from_turn_settlement with no new frontier should NOT enqueue (Fix 6)."""
        wake_service = ManagerWakeService(self.project_service)
        service = ManagerAutoService(
            self.project_service,
            manager_wake_service=wake_service,
        )
        store = self.project_service.graph_store("test-project")
        graph = store.load_graph()
        state = ManagerAutoState(
            enabled=True,
            wake_allowed=True,
            owner_session_id="session_1",
            consume_workboard=True,
            chain_count=0,
            max_chain_count=10,
        )
        graph.metadata["manager_auto"] = state.model_dump()
        store.save_graph(graph)

        mock_bws = MagicMock()
        mock_bws.signal_snapshot.return_value = {
            "revision": 100,
            "counts": {"running": 1},
            "has_actionable": False,
            "has_running": True,
            "has_blocked_for_user": False,
            "actionability": {
                "has_manager_actionable": False,
                "has_startable_frontier": False,
                "has_only_running": True,
            },
            "fingerprint": "",
            "fingerprint_items": [],
        }
        service.background_workboard_service = mock_bws

        result = service.evaluate_workboard_and_maybe_signal(
            "test-project", "session_1", from_turn_settlement=True
        )
        self.assertTrue(result.enabled)
        self.assertIsNone(result.last_wake_id)

    def test_chain_count_reset_on_re_enable(self):
        """Re-enabling auto after stop should reset chain_count (Fix 7)."""
        service = ManagerAutoService(self.project_service)
        store = self.project_service.graph_store("test-project")
        graph = store.load_graph()
        # Pre-seed a stopped state with high chain_count
        state = ManagerAutoState(
            enabled=False,
            wake_allowed=False,
            owner_session_id="session_1",
            chain_count=5,
            max_chain_count=10,
        )
        graph.metadata["manager_auto"] = state.model_dump()
        store.save_graph(graph)

        result = service.enable("test-project", "session_1", mode="continuous")
        self.assertEqual(result.chain_count, 0)

    def test_fingerprint_reset_on_re_enable(self):
        """Re-enabling auto should clear last_signaled_workboard_fingerprint so same frontier can re-wake."""
        service = ManagerAutoService(self.project_service)
        store = self.project_service.graph_store("test-project")
        graph = store.load_graph()
        state = ManagerAutoState(
            enabled=False,
            wake_allowed=False,
            owner_session_id="session_1",
            chain_count=3,
            max_chain_count=10,
            last_signaled_workboard_fingerprint="sha1:old",
            last_signaled_workboard_fingerprint_at="2026-01-01T00:00:00Z",
        )
        graph.metadata["manager_auto"] = state.model_dump()
        store.save_graph(graph)

        result = service.enable("test-project", "session_1", mode="continuous")
        self.assertIsNone(result.last_signaled_workboard_fingerprint)
        self.assertIsNone(result.last_signaled_workboard_fingerprint_at)

    def test_coalescing_handled_filtered_from_fingerprint(self):
        """needs_manager items with coalescing_handled=true must not enter fingerprint (Fix 5)."""
        view = BackgroundWorkboardView(
            project_id="test-project",
            revision=1,
            counts={"needs_manager": 2},
            running=[],
            todo=[],
            needs_manager=[
                {
                    "item_id": "dep_fail_1",
                    "lane": "needs_manager",
                    "kind": "runtime_dependency_install_failed",
                    "card_id": "card_a",
                    "status": "pending",
                    "payload": {"coalescing_key": "dep:python:numpy:package_not_found", "coalescing_handled": True},
                },
                {
                    "item_id": "dep_fail_2",
                    "lane": "needs_manager",
                    "kind": "runtime_dependency_install_failed",
                    "card_id": "card_b",
                    "status": "pending",
                    "payload": {"coalescing_key": "dep:python:numpy:package_not_found", "coalescing_handled": False},
                },
            ],
            completed=[],
            ready_to_start=[],
            blocked_for_user=[],
            deferred=[],
        )
        snapshot = BackgroundWorkboardService._semantic_wake_snapshot_from_view(view)
        # Only the unhandled coalescing item produces an action unit
        manager_items = [it for it in snapshot["fingerprint_items"] if it.startswith("manager:")]
        self.assertEqual(len(manager_items), 1)
        self.assertEqual(manager_items[0], "manager:dep:python:numpy:package_not_found")
        self.assertTrue(snapshot["actionability"]["has_manager_actionable"])

    def test_same_coalescing_key_one_action_unit(self):
        """Multiple dependency failures with same coalescing key produce one action unit (Fix 5)."""
        view = BackgroundWorkboardView(
            project_id="test-project",
            revision=1,
            counts={"needs_manager": 2},
            running=[],
            todo=[],
            needs_manager=[
                {
                    "item_id": "dep_fail_a",
                    "lane": "needs_manager",
                    "kind": "runtime_dependency_install_failed",
                    "card_id": "card_a",
                    "status": "pending",
                    "payload": {"coalescing_key": "dep:python:numpy:package_not_found", "coalescing_handled": False},
                },
                {
                    "item_id": "dep_fail_b",
                    "lane": "needs_manager",
                    "kind": "runtime_dependency_install_failed",
                    "card_id": "card_b",
                    "status": "pending",
                    "payload": {"coalescing_key": "dep:python:numpy:package_not_found", "coalescing_handled": False},
                },
            ],
            completed=[],
            ready_to_start=[],
            blocked_for_user=[],
            deferred=[],
        )
        snapshot = BackgroundWorkboardService._semantic_wake_snapshot_from_view(view)
        manager_items = [it for it in snapshot["fingerprint_items"] if it.startswith("manager:")]
        self.assertEqual(len(manager_items), 1, "Same coalescing key should produce exactly one action unit")

    def test_todo_not_in_fingerprint(self):
        """todo items must not enter fingerprint or count as manager actionable (Fix 1/6)."""
        # todo items are still preserved by _merge_items for UI/audit, but should not affect fingerprint
        store = self.project_service.graph_store("test-project")
        state = BackgroundWorkboardState()
        state.items["todo1"] = WorkboardItemRecord(
            item_id="todo1",
            lane="todo",
            kind="claim_workboard_item",
            card_id="card_a",
            status="pending",
            source_item_id="ready1",
        )
        service = BackgroundWorkboardService(self.project_service, MagicMock())
        service._save_state("test-project", state)

        snapshot = service.signal_snapshot("test-project")
        self.assertFalse(snapshot["has_actionable"])
        self.assertFalse(snapshot["actionability"]["has_manager_actionable"])
        self.assertFalse(snapshot["actionability"]["has_startable_frontier"])
        self.assertEqual(len(snapshot["fingerprint_items"]), 0)

    def test_needs_manager_semantic_key_not_item_id(self):
        """needs_manager items use semantic keys, not raw item_id, in fingerprint (Fix 1)."""
        view = BackgroundWorkboardView(
            project_id="test-project",
            revision=1,
            counts={"needs_manager": 1},
            running=[],
            todo=[],
            needs_manager=[
                {
                    "item_id": "run_fail_abc123",
                    "lane": "needs_manager",
                    "kind": "execution_error",
                    "card_id": "card_a",
                    "run_id": "run_42",
                    "status": "pending",
                    "payload": {},
                }
            ],
            completed=[],
            ready_to_start=[],
            blocked_for_user=[],
            deferred=[],
        )
        snapshot = BackgroundWorkboardService._semantic_wake_snapshot_from_view(view)
        manager_items = [it for it in snapshot["fingerprint_items"] if it.startswith("manager:")]
        self.assertEqual(len(manager_items), 1)
        self.assertEqual(manager_items[0], "manager:run:run_42:execution_error")

    def test_per_key_coalescing_handled_in_state(self):
        """Handling one item with a coalescing_key marks the key handled in state and batch-marks siblings."""
        service = BackgroundWorkboardService(self.project_service, MagicMock())
        store = self.project_service.graph_store("test-project")
        state = BackgroundWorkboardState()
        state.items["dep_fail_a"] = WorkboardItemRecord(
            item_id="dep_fail_a",
            lane="needs_manager",
            kind="runtime_dependency_install_failed",
            card_id="card_a",
            status="pending",
            payload={"coalescing_key": "dep:python:numpy:package_not_found", "coalescing_handled": False},
        )
        state.items["dep_fail_b"] = WorkboardItemRecord(
            item_id="dep_fail_b",
            lane="needs_manager",
            kind="runtime_dependency_install_failed",
            card_id="card_b",
            status="pending",
            payload={"coalescing_key": "dep:python:numpy:package_not_found", "coalescing_handled": False},
        )
        service._save_state("test-project", state)

        # Block one item — should batch-mark both and record the key in state
        service.block_workboard_item_for_user("test-project", "dep_fail_a", "session_1", message="needs user")

        updated_state = service._load_state("test-project")
        self.assertIn("dep:python:numpy:package_not_found", updated_state.handled_coalescing_keys)
        self.assertTrue(updated_state.items["dep_fail_a"].payload.get("coalescing_handled"))
        self.assertTrue(updated_state.items["dep_fail_b"].payload.get("coalescing_handled"))

    def test_derived_items_respects_handled_coalescing_keys(self):
        """Newly derived dependency failures with a handled coalescing_key are auto-marked handled."""
        import json as _json
        service = BackgroundWorkboardService(self.project_service, MagicMock())
        # Write a real runtime_dependency_jobs.json with a failed job
        jobs = [
            {
                "job_id": "job_dep_001",
                "status": "failed",
                "created_at": "2026-01-01T00:00:00Z",
                "finished_at": "2026-01-01T00:01:00Z",
                "result": {
                    "error_code": "package_not_found",
                    "message": "Failed to install numpy",
                },
                "payload": {
                    "runtime": "python",
                    "packages": ["numpy"],
                    "source": {"card_id": "card_a", "run_id": "run_1"},
                },
            }
        ]
        dep_jobs_path = self.project_service.project_path("test-project") / "chat" / "runtime_dependency_jobs.json"
        dep_jobs_path.parent.mkdir(parents=True, exist_ok=True)
        dep_jobs_path.write_text(_json.dumps(jobs), encoding="utf-8")

        state = BackgroundWorkboardState()
        # The coalescing_key for this job is dep:python:numpy:package_not_found
        state.handled_coalescing_keys["dep:python:numpy:package_not_found"] = "2026-01-01T00:00:00Z"
        service._save_state("test-project", state)

        derived = service._derived_items("test-project", state=state)
        failed_item = derived.get("workboard_item:job_dep_001:runtime_dependency_install_failed")
        self.assertIsNotNone(failed_item, "Derived dependency failure item should exist")
        self.assertTrue(
            failed_item.payload.get("coalescing_handled"),
            "Derived item with handled coalescing_key should have coalescing_handled=True",
        )
        self.assertEqual(
            failed_item.payload.get("coalescing_key"),
            "dep:python:numpy:package_not_found",
        )


if __name__ == "__main__":
    unittest.main()
