"""Tests for parallel run concurrency: has_running derivation, capacity-aware batch, fuel_revision bumps."""

import unittest
from pathlib import Path
from threading import Semaphore
from unittest.mock import MagicMock, patch

from app.models.background import (
    BackgroundWorkboardState,
    WorkboardFuelSnapshot,
    WorkboardItemRecord,
)
from app.models.manager_auto import ManagerAutoState
from app.services.background_workboard_service import BackgroundWorkboardService
from app.services.manager_auto_service import ManagerAutoService
from app.services.utils import atomic_write_json, utc_now


class HasRunningDerivationTest(unittest.TestCase):
    """has_running must be derived from workboard active_run_count, not scalar active_run_id."""

    def _make_auto_service(self):
        ps = MagicMock()
        ps.lock_for.return_value = MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        pes = MagicMock()
        bws = MagicMock()
        svc = ManagerAutoService(ps, pes, bws)
        return svc

    def test_derive_running_when_active_run_count_positive(self):
        """Even with active_run_id=None, state is 'running' if workboard has active tasks."""
        svc = self._make_auto_service()
        state = ManagerAutoState(enabled=True, state="pending_wake", active_run_id=None)
        fuel = WorkboardFuelSnapshot(todo_count=1, active_run_count=2)
        result = svc._derive_state(state, fuel)
        self.assertEqual(result, "running")

    def test_derive_pending_wake_when_no_active_runs(self):
        """With active_run_count=0 and fuel present, state is 'pending_wake'."""
        svc = self._make_auto_service()
        state = ManagerAutoState(enabled=True, state="running", active_run_id=None)
        fuel = WorkboardFuelSnapshot(todo_count=2, active_run_count=0)
        result = svc._derive_state(state, fuel)
        self.assertEqual(result, "pending_wake")

    def test_first_terminal_does_not_trigger_complete_with_siblings(self):
        """3 parallel runs: first terminal must NOT derive 'complete' if 2 still active."""
        svc = self._make_auto_service()
        state = ManagerAutoState(enabled=True, state="running", active_run_id=None)
        fuel = WorkboardFuelSnapshot(todo_count=0, active_run_count=2)
        result = svc._derive_state(state, fuel)
        self.assertEqual(result, "idle")

    def test_all_terminal_derives_complete(self):
        """All runs finished, no fuel → 'complete'."""
        svc = self._make_auto_service()
        state = ManagerAutoState(enabled=True, state="running", active_run_id=None)
        fuel = WorkboardFuelSnapshot(todo_count=0, active_run_count=0)
        result = svc._derive_state(state, fuel)
        self.assertEqual(result, "complete")


class TerminalCallbackGuardTest(unittest.TestCase):
    """Terminal callback should only clear active_run_id if it matches the stored one."""

    def _make_auto_service(self, state_dict: dict):
        ps = MagicMock()
        lock = MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        ps.lock_for.return_value = lock
        mock_store = MagicMock()
        mock_graph = MagicMock(metadata={"manager_auto": state_dict})
        mock_store.load_graph.return_value = mock_graph
        ps.graph_store.return_value = mock_store

        pes = MagicMock()
        bws = MagicMock()
        bws.get_wake_fuel.return_value = WorkboardFuelSnapshot(active_run_count=1)

        svc = ManagerAutoService(ps, pes, bws)
        return svc

    def test_mismatched_run_id_does_not_clear(self):
        """Terminal event with a different run_id should not clear active_run_id."""
        state_dict = ManagerAutoState(
            enabled=True, state="running",
            active_run_id="run-A", owner_session_id="sess-1",
        ).model_dump()
        svc = self._make_auto_service(state_dict)

        svc.set_runtime_state = MagicMock(return_value=ManagerAutoState(**state_dict))
        svc.evaluate_workboard_and_maybe_signal = MagicMock(
            return_value=(ManagerAutoState(**state_dict), None)
        )

        svc.notify_background_task_terminal("proj", run_id="run-B")
        svc.set_runtime_state.assert_not_called()

    def test_matched_run_id_clears(self):
        """Terminal event with matching run_id should trigger clear."""
        state_dict = ManagerAutoState(
            enabled=True, state="running",
            active_run_id="run-A", owner_session_id="sess-1",
        ).model_dump()
        svc = self._make_auto_service(state_dict)

        svc.set_runtime_state = MagicMock(return_value=ManagerAutoState(**state_dict))
        svc.evaluate_workboard_and_maybe_signal = MagicMock(
            return_value=(ManagerAutoState(**state_dict), None)
        )

        svc.notify_background_task_terminal("proj", run_id="run-A")
        svc.set_runtime_state.assert_called_once_with(
            "proj", clear_active_run=True, clear_active_job=False,
        )


class CapacityAwareBatchTest(unittest.TestCase):
    """submit_claimed_workboard_items should defer items beyond capacity."""

    def setUp(self):
        import tempfile
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="bp_cap_"))
        self._project_id = "test-cap"
        state_dir = self._tmp_dir / self._project_id / "chat"
        state_dir.mkdir(parents=True)

        self.ps = MagicMock()
        self.ps.project_path.return_value = self._tmp_dir / self._project_id
        mock_store = MagicMock()
        mock_store.load_cards.return_value = []
        mock_store.load_graph.return_value = MagicMock(runs=[], metadata={})
        self.ps.graph_store.return_value = mock_store

        self.bts = MagicMock()
        self.bts.list_tasks.return_value = []

        self.wb = BackgroundWorkboardService(self.ps, self.bts)
        self.wb._path = lambda pid: state_dir / "background_workboard_state.json"
        self._fuel_bumps = 0
        self.wb.set_fuel_change_callback(lambda pid: self._count_fuel_bump())

    def _count_fuel_bump(self):
        self._fuel_bumps += 1

    def tearDown(self):
        import shutil
        if self._tmp_dir and self._tmp_dir.exists():
            shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _seed_items(self, count: int):
        """Create claimed todo items with matching derived ready_to_start sources."""
        state = BackgroundWorkboardState()
        item_ids = []
        for i in range(count):
            item_id = f"todo:{i}"
            source_id = f"ready_to_start:card-{i}"
            item_ids.append(item_id)
            state.items[item_id] = WorkboardItemRecord(
                item_id=item_id,
                lane="todo",
                kind="start_ready_card",
                card_id=f"card-{i}",
                status="claimed",
                claimed_by_session_id="sess-1",
                claimed_at=utc_now(),
                source_item_id=source_id,
                payload={"card_id": f"card-{i}"},
                fuel_kind="todo",
            )
        atomic_write_json(self.wb._path(self._project_id), state.model_dump())
        return item_ids

    def _mock_derived_items(self, count: int):
        """Create derived items dict that provides ready_to_start sources."""
        derived = {}
        for i in range(count):
            source_id = f"ready_to_start:card-{i}"
            derived[source_id] = WorkboardItemRecord(
                item_id=source_id,
                lane="ready_to_start",
                kind="ready_card",
                card_id=f"card-{i}",
                status="pending",
            )
        return derived

    def test_defers_overflow_items(self):
        """With max_starts=2, submitting 4 items should start 2 and defer 2."""
        item_ids = self._seed_items(4)
        start_count = 0

        def mock_start(pid, card_id):
            nonlocal start_count
            start_count += 1
            return {"run_id": f"run-{start_count}", "task_id": f"task-{start_count}", "status": "running"}

        derived = self._mock_derived_items(4)
        with patch.object(self.wb, '_derived_items', return_value=derived):
            with patch.object(self.wb, 'flow_service') as mock_flow:
                mock_flow.get_work_order.return_value = {"work_items": []}
                result = self.wb.submit_claimed_workboard_items(
                    self._project_id,
                    item_ids,
                    session_id="sess-1",
                    start_callback=mock_start,
                    max_starts=2,
                )

        self.assertEqual(len(result["deferred"]), 2)
        self.assertEqual(start_count, 2)
        self.assertEqual(len(result["started"]), 2)

    def test_no_defer_when_within_capacity(self):
        """When items <= max_starts, nothing is deferred."""
        item_ids = self._seed_items(2)

        derived = self._mock_derived_items(2)
        with patch.object(self.wb, '_derived_items', return_value=derived):
            with patch.object(self.wb, 'flow_service') as mock_flow:
                mock_flow.get_work_order.return_value = {"work_items": []}
                result = self.wb.submit_claimed_workboard_items(
                    self._project_id,
                    item_ids,
                    session_id="sess-1",
                    start_callback=lambda pid, cid: {"run_id": "r1", "task_id": "t1", "status": "running"},
                    max_starts=3,
                )

        self.assertEqual(len(result["deferred"]), 0)
        self.assertEqual(len(result["started"]), 2)

    def test_fuel_revision_bumped_on_defer(self):
        """Deferring items must trigger fuel_change_callback."""
        item_ids = self._seed_items(3)
        self._fuel_bumps = 0

        derived = self._mock_derived_items(3)
        with patch.object(self.wb, '_derived_items', return_value=derived):
            with patch.object(self.wb, 'flow_service') as mock_flow:
                mock_flow.get_work_order.return_value = {"work_items": []}
                self.wb.submit_claimed_workboard_items(
                    self._project_id,
                    item_ids,
                    session_id="sess-1",
                    start_callback=lambda pid, cid: {"run_id": "r1", "task_id": "t1", "status": "running"},
                    max_starts=1,
                )

        # At least one bump from the defer path
        self.assertGreater(self._fuel_bumps, 0)


class GetAvailableSlotsTest(unittest.TestCase):
    """WorkerService.get_available_run_slots returns semaphore value."""

    def test_returns_correct_value(self):
        from app.services.worker_service import WorkerService

        ps = MagicMock()
        ps.settings = MagicMock()
        ps.settings.executor_max_concurrent_runs = 3
        ps.settings.executor_sandboxed = True
        ps.settings.executor_post_run_audit = False

        with patch.object(WorkerService, '_reconcile_active_runs'):
            ws = WorkerService(ps, MagicMock(), MagicMock())
        slots = ws.get_available_run_slots("proj-1")
        self.assertEqual(slots, 3)

        sem = ws._execution_semaphore_for("proj-1")
        sem.acquire(blocking=False)
        self.assertEqual(ws.get_available_run_slots("proj-1"), 2)

        sem.acquire(blocking=False)
        sem.acquire(blocking=False)
        self.assertEqual(ws.get_available_run_slots("proj-1"), 0)


if __name__ == "__main__":
    unittest.main()
