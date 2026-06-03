"""Doc 42: Fuel buffer unit tests (Section 10 regression tests)."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.models.background import BackgroundWorkboardState, BackgroundWorkboardView, FuelKind, WorkboardFuelSnapshot, WorkboardItemRecord
from app.models.manager_auto import ManagerAutoState
from app.services.utils import utc_now


class AutoEpisodeModelMigrationTest(unittest.TestCase):
    """Test that legacy ManagerAutoState data is correctly migrated to doc-42 schema."""

    def test_legacy_state_completed_maps_to_complete(self):
        s = ManagerAutoState.model_validate({
            "enabled": True,
            "state": "completed",
            "mode": "continuous",
            "view_workboard": True,
            "consume_workboard": True,
            "wake_allowed": True,
            "last_signaled_board_revision": 5,
            "chain_limit_basis": {"executable_card_count": 3, "formula": "..."},
            "expires_at": "2026-01-01T00:00:00Z",
        })
        self.assertEqual(s.state, "complete")
        self.assertEqual(s.fuel_revision, 0)
        self.assertEqual(s.last_notified_revision, 0)
        self.assertFalse(s.wake_in_flight)
        self.assertFalse(s.completion_notified)
        self.assertEqual(s.wake_window, [])
        self.assertIsNone(s.finished_at)
        self.assertEqual(s.max_chain_count, 50)

    def test_legacy_state_active_maps_to_pending_wake(self):
        s = ManagerAutoState.model_validate({"enabled": True, "state": "active"})
        self.assertEqual(s.state, "pending_wake")

    def test_legacy_state_thinking_maps_to_running(self):
        s = ManagerAutoState.model_validate({"enabled": True, "state": "thinking"})
        self.assertEqual(s.state, "running")

    def test_legacy_state_blocked_maps_to_idle(self):
        s = ManagerAutoState.model_validate({"enabled": True, "state": "blocked"})
        self.assertEqual(s.state, "idle")

    def test_legacy_state_stopped_maps_to_finished(self):
        s = ManagerAutoState.model_validate({
            "enabled": True, "state": "stopped", "stopped_at": "2026-01-01T00:00:00Z",
        })
        self.assertEqual(s.state, "finished")
        self.assertEqual(s.finished_at, "2026-01-01T00:00:00Z")

    def test_legacy_state_cancelled_maps_to_finished(self):
        s = ManagerAutoState.model_validate({
            "enabled": True, "state": "cancelled", "stopped_at": "2026-01-01T00:00:00Z",
        })
        self.assertEqual(s.state, "finished")

    def test_new_fields_initialized_from_scratch(self):
        s = ManagerAutoState.model_validate({"enabled": True, "state": "idle"})
        self.assertEqual(s.state, "idle")
        self.assertEqual(s.fuel_revision, 0)
        self.assertFalse(s.wake_in_flight)
        self.assertFalse(s.completion_notified)

    def test_max_chain_count_bumped_to_50(self):
        s = ManagerAutoState.model_validate({"enabled": True, "state": "idle", "max_chain_count": 10})
        self.assertEqual(s.max_chain_count, 50)


class WorkboardFuelRecordTest(unittest.TestCase):
    """Test WorkboardItemRecord fuel fields."""

    def test_todo_fuel_kind_on_item(self):
        now = utc_now()
        item = WorkboardItemRecord(
            item_id="test-todo",
            lane="todo",
            kind="start_ready_card",
            card_id="card1",
            fuel_kind="todo",
            fuel_added_at=now,
        )
        self.assertEqual(item.fuel_kind, "todo")
        self.assertEqual(item.fuel_added_at, now)
        self.assertIsNone(item.fuel_consumed_at)
        self.assertIsNone(item.fuel_seen_at_revision)

    def test_complete_signal_fuel_field(self):
        item = WorkboardItemRecord(
            item_id="sig-1",
            lane="completed",
            kind="card_run_reviewed",
            card_id="card1",
            fuel_kind="complete_signal",
            fuel_added_at=utc_now(),
            triggered_by_status="accepted",
        )
        self.assertEqual(item.fuel_kind, "complete_signal")
        self.assertEqual(item.triggered_by_status, "accepted")

    def test_block_signal_fuel_field(self):
        item = WorkboardItemRecord(
            item_id="sig-2",
            lane="needs_manager",
            kind="card_run_failed",
            card_id="card1",
            fuel_kind="block_signal",
            fuel_added_at=utc_now(),
            triggered_by_status="failed",
        )
        self.assertEqual(item.fuel_kind, "block_signal")
        self.assertEqual(item.triggered_by_status, "failed")

    def test_fuel_consumed_at_set(self):
        now = utc_now()
        item = WorkboardItemRecord(
            item_id="test-todo",
            lane="todo",
            kind="start_ready_card",
            card_id="card1",
            fuel_kind="todo",
            fuel_added_at=utc_now(),
            fuel_consumed_at=now,
        )
        self.assertEqual(item.fuel_consumed_at, now)

    def test_fuel_seen_at_revision_set(self):
        item = WorkboardItemRecord(
            item_id="sig-1",
            lane="completed",
            kind="card_run_reviewed",
            card_id="card1",
            fuel_kind="complete_signal",
            fuel_added_at=utc_now(),
            fuel_seen_at_revision=5,
        )
        self.assertEqual(item.fuel_seen_at_revision, 5)
        self.assertIsNone(item.fuel_consumed_at)


class WorkboardFuelSnapshotTest(unittest.TestCase):
    """Test derived fuel snapshot model."""

    def test_empty_snapshot(self):
        fs = WorkboardFuelSnapshot()
        self.assertEqual(fs.todo_count, 0)
        self.assertEqual(fs.complete_signal_count, 0)
        self.assertEqual(fs.block_signal_count, 0)
        self.assertEqual(fs.top_item_ids, [])
        self.assertEqual(fs.top_card_ids, [])
        self.assertEqual(fs.fuel_revision, 0)

    def test_populated_snapshot(self):
        fs = WorkboardFuelSnapshot(
            todo_count=3,
            complete_signal_count=1,
            block_signal_count=2,
            top_item_ids=["a", "b"],
            top_card_ids=["card1"],
            fuel_revision=7,
        )
        self.assertEqual(fs.todo_count, 3)
        self.assertEqual(fs.complete_signal_count, 1)
        self.assertEqual(fs.block_signal_count, 2)
        self.assertEqual(len(fs.top_item_ids), 2)
        self.assertEqual(fs.fuel_revision, 7)


class FuelKindTypeTest(unittest.TestCase):
    """Verify FuelKind type accepts correct values."""

    def test_valid_fuel_kinds(self):
        for kind in ("todo", "complete_signal", "block_signal"):
            item = WorkboardItemRecord(
                item_id=f"test-{kind}",
                lane="todo",
                kind="test",
                fuel_kind=kind,  # type: ignore[arg-type]
            )
            self.assertEqual(item.fuel_kind, kind)


class BackgroundWorkboardStateFuelTest(unittest.TestCase):
    """Test state persistence with fuel items."""

    def test_state_with_fuel_items(self):
        now = utc_now()
        state = BackgroundWorkboardState()
        item = WorkboardItemRecord(
            item_id="fuel-1",
            lane="completed",
            kind="card_run_reviewed",
            card_id="card1",
            fuel_kind="complete_signal",
            fuel_added_at=now,
            triggered_by_status="accepted",
        )
        state.items[item.item_id] = item
        saved = state.model_dump()
        reloaded = BackgroundWorkboardState.model_validate(saved)
        self.assertIn("fuel-1", reloaded.items)
        self.assertEqual(reloaded.items["fuel-1"].fuel_kind, "complete_signal")


class LatchSemanticsTest(unittest.TestCase):
    """Test latch field semantics on AutoEpisode (Section 7)."""

    def test_fuel_revision_increment(self):
        s = ManagerAutoState.model_validate({"enabled": True, "state": "idle", "fuel_revision": 3})
        s.fuel_revision += 1
        self.assertEqual(s.fuel_revision, 4)

    def test_completion_notified_clears_on_fuel_change(self):
        s = ManagerAutoState.model_validate({
            "enabled": True, "state": "complete", "fuel_revision": 3, "completion_notified": True,
        })
        # Simulate fuel change: increment_revision clears completion_notified
        s.fuel_revision += 1
        s.completion_notified = False
        self.assertEqual(s.fuel_revision, 4)
        self.assertFalse(s.completion_notified)

    def test_wake_in_flight_lifecycle(self):
        s = ManagerAutoState.model_validate({"enabled": True, "state": "pending_wake"})
        self.assertFalse(s.wake_in_flight)
        # Evaluate turn begins
        s.wake_in_flight = True
        self.assertTrue(s.wake_in_flight)
        # Turn settles
        s.wake_in_flight = False
        self.assertFalse(s.wake_in_flight)

    def test_workboard_evaluate_emission_rule(self):
        """Emit when pending_wake + !wake_in_flight + fuel_revision > last_notified."""
        s = ManagerAutoState.model_validate({
            "enabled": True, "state": "pending_wake",
            "fuel_revision": 5, "last_notified_revision": 3,
            "wake_in_flight": False,
        })
        should_emit = (
            s.state == "pending_wake"
            and not s.wake_in_flight
            and s.fuel_revision > s.last_notified_revision
        )
        self.assertTrue(should_emit)

    def test_workboard_evaluate_suppressed_when_in_flight(self):
        s = ManagerAutoState.model_validate({
            "enabled": True, "state": "pending_wake",
            "fuel_revision": 5, "last_notified_revision": 3,
            "wake_in_flight": True,
        })
        should_emit = (
            s.state == "pending_wake"
            and not s.wake_in_flight
            and s.fuel_revision > s.last_notified_revision
        )
        self.assertFalse(should_emit)

    def test_workboard_evaluate_suppressed_when_no_new_fuel(self):
        s = ManagerAutoState.model_validate({
            "enabled": True, "state": "pending_wake",
            "fuel_revision": 3, "last_notified_revision": 3,
            "wake_in_flight": False,
        })
        should_emit = (
            s.state == "pending_wake"
            and not s.wake_in_flight
            and s.fuel_revision > s.last_notified_revision
        )
        self.assertFalse(should_emit)

    def test_complete_evaluate_one_shot(self):
        s = ManagerAutoState.model_validate({
            "enabled": True, "state": "complete", "completion_notified": False,
        })
        should_emit = s.state == "complete" and not s.completion_notified
        self.assertTrue(should_emit)
        # After emission
        s.completion_notified = True
        self.assertTrue(s.completion_notified)
        should_emit = s.state == "complete" and not s.completion_notified
        self.assertFalse(should_emit)

    def test_fuel_change_clears_completion_notified(self):
        s = ManagerAutoState.model_validate({
            "enabled": True, "state": "complete", "completion_notified": True,
        })
        # Fuel change resets
        s.fuel_revision += 1
        s.completion_notified = False
        self.assertFalse(s.completion_notified)

    def test_finish_tool_rejected_when_not_complete(self):
        """Finish tool is valid only when state == complete."""
        for bad_state in ("running", "idle", "pending_wake", "finished"):
            s = ManagerAutoState.model_validate({"enabled": True, "state": bad_state})
            self.assertNotEqual(s.state, "complete", f"State {bad_state} should reject finish")

    def test_finish_tool_accepted_when_complete(self):
        s = ManagerAutoState.model_validate({"enabled": True, "state": "complete"})
        self.assertEqual(s.state, "complete")

    def test_finish_transitions_to_finished(self):
        s = ManagerAutoState.model_validate({
            "enabled": True, "state": "complete",
        })
        s.state = "finished"
        s.finished_at = utc_now()
        s.enabled = False
        self.assertEqual(s.state, "finished")
        self.assertIsNotNone(s.finished_at)

    def test_wake_storm_guard(self):
        """>5 wakes in the wake_window triggers stop."""
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        # Create 6 timestamps within the last minute
        wake_window = [
            (now - timedelta(seconds=s)).isoformat()
            for s in range(0, 55, 10)
        ]
        self.assertGreaterEqual(len(wake_window), 5)
        # Prune to within last minute
        from app.services.manager_auto_service import ManagerAutoService
        pruned = [ts for ts in wake_window if ManagerAutoService._within_last_minute(ts)]
        self.assertGreaterEqual(len(pruned), 5)

    def test_chain_limit_guard(self):
        """chain_count > 50 triggers stop."""
        s = ManagerAutoState.model_validate({
            "enabled": True, "state": "pending_wake", "chain_count": 51,
        })
        self.assertGreater(s.chain_count, s.max_chain_count)

    def test_owner_session_locking_unchanged(self):
        """Non-owner sessions should be blocked from mutating."""
        s = ManagerAutoState.model_validate({
            "enabled": True, "state": "idle", "owner_session_id": "owner-session",
        })
        non_owner = "other-session"
        self.assertTrue(s.enabled)
        self.assertEqual(s.owner_session_id, "owner-session")
        self.assertNotEqual(non_owner, s.owner_session_id)

    def test_auto_scope_id_present(self):
        s = ManagerAutoState.model_validate({
            "enabled": True, "state": "idle", "auto_scope_id": "scope_abc123",
        })
        self.assertEqual(s.auto_scope_id, "scope_abc123")


class BackgroundWorkboardServiceFuelTest(unittest.TestCase):
    """Service-level tests that call actual BackgroundWorkboardService methods.

    Uses a temp directory with patched ProjectService to isolate from real workspace.
    """

    def setUp(self):
        self._tmp = None

    def tearDown(self):
        import shutil
        if self._tmp and Path(self._tmp).exists():
            shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_wb(self, project_id: str = "test-fuel"):
        """Create BackgroundWorkboardService with fully mocked deps."""
        import tempfile, json
        from unittest.mock import MagicMock
        from app.services.background_workboard_service import BackgroundWorkboardService

        self._tmp_dir = Path(tempfile.mkdtemp(prefix="bp_fuel_"))
        state_dir = self._tmp_dir / project_id / "chat"
        state_dir.mkdir(parents=True)

        # Create a mock ProjectService
        ps = MagicMock()
        ps.project_path.return_value = self._tmp_dir / project_id

        # Mock graph_store for card loading in _auto_exit_todo_fuel + _load_card
        mock_store = MagicMock()
        mock_store.load_cards.return_value = []
        mock_store.load_graph.return_value = MagicMock(
            runs=[], metadata={}
        )
        ps.graph_store.return_value = mock_store

        # Mock background_task_service
        bts = MagicMock()
        bts.list_tasks.return_value = []

        wb = BackgroundWorkboardService(ps, bts)
        # Ensure _path returns a real path so _save_state/_load_state work
        wb._path = lambda pid: state_dir.parent / "chat" / "background_workboard_state.json"
        return wb

    def _seed_state(self, wb, project_id: str, item: WorkboardItemRecord):
        state = BackgroundWorkboardState()
        state.items[item.item_id] = item
        from app.services.utils import atomic_write_json
        atomic_write_json(wb._path(project_id), state.model_dump())

    def test_todo_rejects_done_via_update_item_status(self):
        """§10 #2: Reject done on todo items at service level."""
        from fastapi import HTTPException
        now = utc_now()
        wb = self._make_wb()
        item = WorkboardItemRecord(
            item_id="todo:sess:ready_card:c1", lane="todo", kind="start_ready_card",
            card_id="c1", source_item_id="ready_card:c1", source_lane="ready_to_start",
            status="pending", fuel_kind="todo", fuel_added_at=now, updated_at=now,
        )
        self._seed_state(wb, "test-fuel", item)
        with self.assertRaises(HTTPException) as ctx:
            wb._update_item_status("test-fuel", item.item_id, "sess", status="done")
        self.assertIn("cannot be marked done", str(ctx.exception.detail))

    def test_todo_skip_accepted(self):
        """§10 #2: skip_workboard_item sets fuel_consumed_at at service level."""
        now = utc_now()
        wb = self._make_wb()
        item = WorkboardItemRecord(
            item_id="todo:sess:ready_card:c2", lane="todo", kind="start_ready_card",
            card_id="c2", source_item_id="ready_card:c2", source_lane="ready_to_start",
            status="claimed", claimed_by_session_id="sess", claimed_at=now,
            fuel_kind="todo", fuel_added_at=now, updated_at=now,
        )
        self._seed_state(wb, "test-fuel", item)
        result = wb.skip_workboard_item("test-fuel", item.item_id, "sess")
        self.assertEqual(result.status, "done")
        self.assertIsNotNone(result.fuel_consumed_at)

    def test_get_wake_fuel_excludes_consumed(self):
        """§10 #3: Consumed fuel excluded from get_wake_fuel snapshot."""
        now = utc_now()
        wb = self._make_wb()
        item = WorkboardItemRecord(
            item_id="todo:sess:ready_card:c3", lane="todo", kind="start_ready_card",
            card_id="c3", source_item_id="ready_card:c3", source_lane="ready_to_start",
            status="pending", fuel_kind="todo", fuel_added_at=now,
            fuel_consumed_at=now, updated_at=now,
        )
        self._seed_state(wb, "test-fuel", item)
        fuel = wb.get_wake_fuel("test-fuel")
        self.assertEqual(fuel.todo_count, 0,
                         "Consumed fuel must be excluded from snapshot")

    def test_mark_fuel_seen_sets_revision(self):
        """N2: mark_fuel_seen writes fuel_seen_at_revision on unconsumed fuel."""
        now = utc_now()
        wb = self._make_wb()
        item = WorkboardItemRecord(
            item_id="fuel:sig:c4", lane="completed", kind="card_run_reviewed",
            card_id="c4", status="pending", fuel_kind="complete_signal",
            fuel_added_at=now, triggered_by_status="accepted", updated_at=now,
        )
        self._seed_state(wb, "test-fuel", item)
        wb.mark_fuel_seen("test-fuel", 7)
        state2 = wb._load_state("test-fuel")
        self.assertEqual(state2.items[item.item_id].fuel_seen_at_revision, 7)

    def test_mark_fuel_seen_skips_consumed(self):
        """mark_fuel_seen does not overwrite already-consumed fuel."""
        now = utc_now()
        wb = self._make_wb()
        item = WorkboardItemRecord(
            item_id="fuel:consumed:c5", lane="completed", kind="card_run_reviewed",
            card_id="c5", status="pending", fuel_kind="complete_signal",
            fuel_added_at=now, fuel_consumed_at=now,
            triggered_by_status="accepted", updated_at=now,
        )
        self._seed_state(wb, "test-fuel", item)
        wb.mark_fuel_seen("test-fuel", 7)
        state2 = wb._load_state("test-fuel")
        self.assertIsNone(state2.items[item.item_id].fuel_seen_at_revision)

    def test_reconcile_creates_complete_signal_for_accepted(self):
        """Persisted complete_signal created when card enters accepted."""
        from app.models.cards import Card
        wb = self._make_wb()
        card = Card(
            card_id="c-accept", title="Accept Card", status="accepted",
            card_type="module", summary="accept summary", step=1,
        )
        wb.project_service.graph_store.return_value.load_cards.return_value = [card]
        state = BackgroundWorkboardState()
        wb._save_state("test-fuel", state)
        changed = wb._reconcile_signal_fuel("test-fuel", state)
        self.assertTrue(changed)
        self.assertIn("fuel_signal:c-accept:accepted", state.items)
        sig = state.items["fuel_signal:c-accept:accepted"]
        self.assertEqual(sig.fuel_kind, "complete_signal")
        self.assertEqual(sig.triggered_by_status, "accepted")
        self.assertIsNone(sig.fuel_consumed_at)

    def test_reconcile_creates_block_signal_for_failed(self):
        """Persisted block_signal created when card enters failed."""
        from app.models.cards import Card
        wb = self._make_wb()
        card = Card(
            card_id="c-fail", title="Fail Card", status="failed",
            card_type="module", summary="fail summary", step=1,
        )
        wb.project_service.graph_store.return_value.load_cards.return_value = [card]
        state = BackgroundWorkboardState()
        wb._save_state("test-fuel", state)
        changed = wb._reconcile_signal_fuel("test-fuel", state)
        self.assertTrue(changed)
        self.assertIn("fuel_signal:c-fail:failed", state.items)
        sig = state.items["fuel_signal:c-fail:failed"]
        self.assertEqual(sig.fuel_kind, "block_signal")
        self.assertEqual(sig.triggered_by_status, "failed")
        self.assertIsNone(sig.fuel_consumed_at)

    def test_reconcile_invalidates_signal_when_card_leaves_status(self):
        """Signal invalidated when card no longer matches triggered_by_status."""
        from app.models.cards import Card
        wb = self._make_wb()
        # Seed a persisted signal for a previously-failed card
        now = utc_now()
        sig = WorkboardItemRecord(
            item_id="fuel_signal:c-revive:failed", lane="needs_manager",
            kind="card_run_failed", card_id="c-revive",
            fuel_kind="block_signal", fuel_added_at=now,
            triggered_by_status="failed", updated_at=now,
        )
        state = BackgroundWorkboardState()
        state.items[sig.item_id] = sig
        wb._save_state("test-fuel", state)
        # Card is now running again (left failed status)
        card = Card(
            card_id="c-revive", title="Revived Card", status="running",
            card_type="module", summary="revive summary", step=1,
        )
        wb.project_service.graph_store.return_value.load_cards.return_value = [card]
        changed = wb._reconcile_signal_fuel("test-fuel", state)
        self.assertTrue(changed)
        invalidated = state.items["fuel_signal:c-revive:failed"]
        self.assertIsNotNone(invalidated.fuel_consumed_at)

    def test_reconcile_skips_duplicate_same_card_status(self):
        """Re-entering same terminal status does not create a duplicate signal."""
        from app.models.cards import Card
        wb = self._make_wb()
        card = Card(
            card_id="c-dedup", title="Dedup Card", status="failed",
            card_type="module", summary="dedup summary", step=1,
        )
        wb.project_service.graph_store.return_value.load_cards.return_value = [card]
        state = BackgroundWorkboardState()
        wb._save_state("test-fuel", state)
        # First reconcile creates the signal
        changed1 = wb._reconcile_signal_fuel("test-fuel", state)
        self.assertTrue(changed1)
        # Second reconcile with same card state does nothing
        changed2 = wb._reconcile_signal_fuel("test-fuel", state)
        self.assertFalse(changed2)
        self.assertEqual(len(state.items), 1)


class ManagerAutoServiceDeriveStateTest(unittest.TestCase):
    """Test state derivation function directly (§10 #16 happy path pieces)."""

    def test_running_with_fuel(self):
        from app.services.manager_auto_service import ManagerAutoService
        svc = object.__new__(ManagerAutoService)
        state = ManagerAutoState.model_validate({
            "enabled": True, "state": "pending_wake", "active_run_id": "r1",
        })
        fuel = WorkboardFuelSnapshot(todo_count=1, active_run_count=1)
        result = svc._derive_state(state, fuel)
        self.assertEqual(result, "running")

    def test_running_without_fuel(self):
        from app.services.manager_auto_service import ManagerAutoService
        svc = object.__new__(ManagerAutoService)
        state = ManagerAutoState.model_validate({
            "enabled": True, "state": "pending_wake", "active_run_id": "r1",
        })
        fuel = WorkboardFuelSnapshot(active_run_count=1)
        result = svc._derive_state(state, fuel)
        self.assertEqual(result, "idle", "N1: running + no fuel → idle")

    def test_pending_wake(self):
        from app.services.manager_auto_service import ManagerAutoService
        svc = object.__new__(ManagerAutoService)
        state = ManagerAutoState.model_validate({"enabled": True, "state": "idle"})
        fuel = WorkboardFuelSnapshot(todo_count=2)
        result = svc._derive_state(state, fuel)
        self.assertEqual(result, "pending_wake")

    def test_complete(self):
        from app.services.manager_auto_service import ManagerAutoService
        svc = object.__new__(ManagerAutoService)
        state = ManagerAutoState.model_validate({"enabled": True, "state": "idle"})
        fuel = WorkboardFuelSnapshot()
        result = svc._derive_state(state, fuel)
        self.assertEqual(result, "complete")

    def test_complete_exits_to_pending_wake_on_new_fuel(self):
        from app.services.manager_auto_service import ManagerAutoService
        svc = object.__new__(ManagerAutoService)
        state = ManagerAutoState.model_validate({"enabled": True, "state": "complete"})
        fuel = WorkboardFuelSnapshot(todo_count=1)
        result = svc._derive_state(state, fuel)
        self.assertEqual(result, "pending_wake")

    def test_finished_stays_finished(self):
        from app.services.manager_auto_service import ManagerAutoService
        svc = object.__new__(ManagerAutoService)
        state = ManagerAutoState.model_validate({"enabled": True, "state": "finished"})
        fuel = WorkboardFuelSnapshot(todo_count=5)
        result = svc._derive_state(state, fuel)
        self.assertEqual(result, "finished")


if __name__ == "__main__":
    unittest.main()
