"""Doc 42: Auto-episode integration tests covering signal persistence, fuel revision idempotency, and consumption lifecycle."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.models.background import BackgroundWorkboardState, WorkboardItemRecord
from app.models.cards import Card
from app.services.background_workboard_service import BackgroundWorkboardService
from app.services.utils import atomic_write_json, utc_now


class AutoEpisodeFlowTest(unittest.TestCase):
    """Integration tests for the full auto-episode workboard lifecycle."""

    def setUp(self):
        self._tmp_dir = None

    def tearDown(self):
        import shutil
        if self._tmp_dir and Path(self._tmp_dir).exists():
            shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _make_wb(self, project_id: str = "test-episode"):
        """Create BackgroundWorkboardService with fully mocked deps."""
        import tempfile

        self._tmp_dir = Path(tempfile.mkdtemp(prefix="bp_episode_"))
        state_dir = self._tmp_dir / project_id / "chat"
        state_dir.mkdir(parents=True)

        ps = MagicMock()
        ps.project_path.return_value = self._tmp_dir / project_id

        mock_store = MagicMock()
        mock_store.load_cards.return_value = []
        mock_store.load_graph.return_value = MagicMock(runs=[], metadata={})
        ps.graph_store.return_value = mock_store

        bts = MagicMock()
        bts.list_tasks.return_value = []

        wb = BackgroundWorkboardService(ps, bts)
        wb._path = lambda pid: state_dir.parent / "chat" / "background_workboard_state.json"
        return wb

    def _seed_state(self, wb, project_id: str, item: WorkboardItemRecord):
        state = BackgroundWorkboardState()
        state.items[item.item_id] = item
        atomic_write_json(wb._path(project_id), state.model_dump())

    def _set_cards(self, wb, cards: list[Card]):
        """Update the mocked graph_store to return specific cards."""
        wb.project_service.graph_store.return_value.load_cards.return_value = cards

    def test_get_workboard_idempotent_no_bump(self):
        """Multiple get_workboard calls do not recreate signals or re-bump fuel."""
        wb = self._make_wb()
        card = Card(
            card_id="c-idem", title="Idem Card", status="failed",
            card_type="module", summary="idem summary", step=1,
        )
        self._set_cards(wb, [card])

        notify_calls = []
        wb.set_fuel_change_callback(lambda pid: notify_calls.append(pid))

        # First call creates signal and notifies
        wb.get_workboard("test-episode")
        self.assertEqual(len(notify_calls), 1)

        # Second and third calls must NOT notify because signal already persisted
        wb.get_workboard("test-episode")
        wb.get_workboard("test-episode")
        self.assertEqual(len(notify_calls), 1, "get_workboard must be idempotent after signal persisted")

        state = wb._load_state("test-episode")
        self.assertEqual(len(state.items), 1)
        self.assertIn("fuel_signal:c-idem:failed", state.items)

    def test_todo_auto_exits_when_card_leaves_planned(self):
        """Todo fuel is consumed when referenced card leaves planned."""
        wb = self._make_wb()
        now = utc_now()
        todo = WorkboardItemRecord(
            item_id="todo:sess:ready_card:c1", lane="todo",
            kind="start_ready_card", card_id="c1",
            source_item_id="ready_card:c1", source_lane="ready_to_start",
            status="pending", fuel_kind="todo", fuel_added_at=now,
            updated_at=now,
        )
        self._seed_state(wb, "test-episode", todo)

        # Card is now running (no longer planned)
        card = Card(
            card_id="c1", title="Running Card", status="running",
            card_type="module", summary="running summary", step=1,
        )
        self._set_cards(wb, [card])

        notify_calls = []
        wb.set_fuel_change_callback(lambda pid: notify_calls.append(pid))

        wb.get_workboard("test-episode")
        self.assertEqual(len(notify_calls), 1, "auto-exit todo should notify fuel change")

        state = wb._load_state("test-episode")
        self.assertIsNotNone(state.items[todo.item_id].fuel_consumed_at)

    def test_failed_card_creates_block_signal_evaluate_does_not_consume(self):
        """Failed card creates block_signal; mark_fuel_seen sets revision but leaves unconsumed."""
        wb = self._make_wb()
        card = Card(
            card_id="c-eval", title="Eval Card", status="failed",
            card_type="module", summary="eval summary", step=1,
        )
        self._set_cards(wb, [card])

        wb.get_workboard("test-episode")
        state = wb._load_state("test-episode")
        sig = state.items.get("fuel_signal:c-eval:failed")
        self.assertIsNotNone(sig)
        self.assertIsNone(sig.fuel_seen_at_revision)

        # Evaluate turn begins: mark fuel seen
        wb.mark_fuel_seen("test-episode", fuel_revision=42)
        state2 = wb._load_state("test-episode")
        sig2 = state2.items["fuel_signal:c-eval:failed"]
        self.assertEqual(sig2.fuel_seen_at_revision, 42)
        self.assertIsNone(sig2.fuel_consumed_at)

        # Signal still appears in wake fuel snapshot
        fuel = wb.get_wake_fuel("test-episode")
        self.assertEqual(fuel.block_signal_count, 1)

    def test_manager_done_deferred_blocked_for_user_consumes_signal(self):
        """Explicit done/deferred/blocked_for_user on signal sets fuel_consumed_at."""
        wb = self._make_wb()
        now = utc_now()
        sig = WorkboardItemRecord(
            item_id="fuel_signal:c-consume:failed", lane="needs_manager",
            kind="card_run_failed", card_id="c-consume",
            fuel_kind="block_signal", fuel_added_at=now,
            triggered_by_status="failed", updated_at=now,
        )
        self._seed_state(wb, "test-episode", sig)

        notify_calls = []
        wb.set_fuel_change_callback(lambda pid: notify_calls.append(pid))

        for status in ("done", "deferred", "blocked_for_user"):
            with self.subTest(status=status):
                # Re-seed fresh signal for each status
                fresh = sig.model_copy(update={"fuel_consumed_at": None})
                state = BackgroundWorkboardState()
                state.items[fresh.item_id] = fresh
                atomic_write_json(wb._path("test-episode"), state.model_dump())

                wb._update_item_status("test-episode", fresh.item_id, "sess", status=status)
                state_after = wb._load_state("test-episode")
                self.assertIsNotNone(state_after.items[fresh.item_id].fuel_consumed_at)

        # One notification per sub-test (3 total)
        self.assertEqual(len(notify_calls), 3)

    def test_rerun_failed_does_not_recreate_signal(self):
        """Card re-entering failed after invalidation does NOT create a second signal."""
        wb = self._make_wb()
        card = Card(
            card_id="c-dedup2", title="Dedup Card", status="failed",
            card_type="module", summary="dedup summary", step=1,
        )
        self._set_cards(wb, [card])

        # First reconcile creates signal
        state = BackgroundWorkboardState()
        wb._save_state("test-episode", state)
        changed1 = wb._reconcile_signal_fuel("test-episode", state)
        self.assertTrue(changed1)
        self.assertIn("fuel_signal:c-dedup2:failed", state.items)

        # Card moves to running (signal invalidated)
        card_running = Card(
            card_id="c-dedup2", title="Dedup Card", status="running",
            card_type="module", summary="dedup summary", step=1,
        )
        self._set_cards(wb, [card_running])
        changed2 = wb._reconcile_signal_fuel("test-episode", state)
        self.assertTrue(changed2)
        invalidated = state.items["fuel_signal:c-dedup2:failed"]
        self.assertIsNotNone(invalidated.fuel_consumed_at)

        # Card fails again — no new signal because dedup marker exists
        self._set_cards(wb, [card])
        changed3 = wb._reconcile_signal_fuel("test-episode", state)
        self.assertFalse(changed3)
        self.assertEqual(len(state.items), 1)

    def test_full_auto_episode_lifecycle(self):
        """ManagerAutoService: pending_wake → running → complete → finished."""
        from app.services.manager_auto_service import ManagerAutoService
        from app.services.project_event_service import ProjectEventService

        wb = self._make_wb()
        ps = wb.project_service
        pes = MagicMock(spec=ProjectEventService)
        relay = MagicMock()

        auto_svc = ManagerAutoService(ps, pes, wb)
        auto_svc.set_chat_stream_relay(relay)
        # Wire fuel change callback so fuel_revision bumps on mutations
        wb.set_fuel_change_callback(lambda pid: auto_svc.increment_fuel_revision(pid))

        session_id = "sess-auto"

        # 1. Enable auto (creates scope, resets state)
        state = auto_svc.enable("test-episode", session_id, scope_objective="run test episode")
        self.assertEqual(state.state, "idle")
        self.assertTrue(state.enabled)
        self.assertEqual(state.owner_session_id, session_id)

        # 2. Inject todo fuel
        card = Card(
            card_id="c-auto", title="Auto Card", status="planned",
            card_type="module", summary="auto summary", step=1,
        )
        self._set_cards(wb, [card])
        wb.promote_workboard_item_to_todo("test-episode", "c-auto", session_id)

        # Evaluate: should emit workboard_evaluate wake
        state, payload = auto_svc.evaluate_workboard_and_maybe_signal(
            "test-episode", session_id
        )
        self.assertEqual(state.state, "pending_wake")
        self.assertIsNotNone(payload)
        self.assertEqual(payload["kind"], "workboard_evaluate")
        self.assertTrue(state.wake_in_flight)
        self.assertEqual(state.last_notified_revision, state.fuel_revision)

        # 3. Simulate turn settlement
        settled = auto_svc.notify_turn_settled(
            "test-episode", session_id, async_boundary=False
        )
        # Fuel still present → state stays pending_wake; wake is not re-emitted
        # because fuel_revision == last_notified_revision
        self.assertEqual(settled.state, "pending_wake")
        state = auto_svc.get_state("test-episode")
        self.assertFalse(state.wake_in_flight)

        # 4. Claim and consume todo (simulate manager starting the card)
        wb.claim_workboard_item("test-episode", f"todo:{session_id}:c-auto", session_id)
        wb.skip_workboard_item("test-episode", f"todo:{session_id}:c-auto", session_id)

        # Evaluate with no fuel: should enter complete and emit complete_evaluate
        state, payload = auto_svc.evaluate_workboard_and_maybe_signal(
            "test-episode", session_id
        )
        self.assertEqual(state.state, "complete")
        self.assertIsNotNone(payload)
        self.assertEqual(payload["kind"], "complete_evaluate")
        self.assertIn("上传数据探索目标", payload["prompt"])
        self.assertIn("不要因为没有 ready_to_start 就直接结束", payload["prompt"])
        self.assertTrue(state.completion_notified)

        # 5. Finish episode
        result = auto_svc.finish_auto_episode("test-episode", session_id)
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "finished")
        finished = auto_svc.get_state("test-episode")
        self.assertEqual(finished.state, "finished")
        self.assertFalse(finished.enabled)


    def test_consumed_or_invalidated_signal_hidden_from_workboard_view(self):
        """Consumed/invalidated signals stay in state.items but leave the view."""
        wb = self._make_wb()

        # --- Consumption path ---
        now = utc_now()
        sig = WorkboardItemRecord(
            item_id="fuel_signal:c-hide:failed", lane="needs_manager",
            kind="card_run_failed", card_id="c-hide",
            fuel_kind="block_signal", fuel_added_at=now,
            triggered_by_status="failed", updated_at=now,
        )
        self._seed_state(wb, "test-episode", sig)
        wb._update_item_status("test-episode", sig.item_id, "sess", status="done")

        state = wb._load_state("test-episode")
        self.assertIn(sig.item_id, state.items)
        self.assertIsNotNone(state.items[sig.item_id].fuel_consumed_at)

        view = wb.get_workboard("test-episode")
        needs_manager_ids = {str(i.get("item_id")) for i in view.needs_manager}
        self.assertNotIn(sig.item_id, needs_manager_ids)

        fuel = wb.get_wake_fuel("test-episode")
        self.assertEqual(fuel.block_signal_count, 0)

        # --- Invalidation path ---
        card = Card(
            card_id="c-inv", title="Inv Card", status="failed",
            card_type="module", summary="inv summary", step=1,
        )
        self._set_cards(wb, [card])
        # Use get_workboard to create and persist the signal
        wb.get_workboard("test-episode")
        state2 = wb._load_state("test-episode")
        self.assertIn("fuel_signal:c-inv:failed", state2.items)
        self.assertIsNone(state2.items["fuel_signal:c-inv:failed"].fuel_consumed_at)

        # Card moves to running — signal invalidated on next get_workboard
        card_running = Card(
            card_id="c-inv", title="Inv Card", status="running",
            card_type="module", summary="inv summary", step=1,
        )
        self._set_cards(wb, [card_running])
        wb.get_workboard("test-episode")
        state3 = wb._load_state("test-episode")
        self.assertIsNotNone(state3.items["fuel_signal:c-inv:failed"].fuel_consumed_at)

        view2 = wb.get_workboard("test-episode")
        needs_manager_ids2 = {str(i.get("item_id")) for i in view2.needs_manager}
        self.assertNotIn("fuel_signal:c-inv:failed", needs_manager_ids2)

        fuel2 = wb.get_wake_fuel("test-episode")
        self.assertEqual(fuel2.block_signal_count, 0)

    def test_accepted_rejected_have_independent_dedup_markers(self):
        """accepted and rejected each have their own dedup marker; cycling does not re-emit."""
        wb = self._make_wb()

        card_accepted = Card(
            card_id="c-cycle", title="Cycle Card", status="accepted",
            card_type="module", summary="cycle summary", step=1,
        )
        self._set_cards(wb, [card_accepted])
        state = BackgroundWorkboardState()
        wb._save_state("test-episode", state)

        # 1. accepted → signal created
        changed = wb._reconcile_signal_fuel("test-episode", state)
        self.assertTrue(changed)
        self.assertIn("fuel_signal:c-cycle:accepted", state.items)

        # 2. running → accepted signal invalidated
        card_running = Card(
            card_id="c-cycle", title="Cycle Card", status="running",
            card_type="module", summary="cycle summary", step=1,
        )
        self._set_cards(wb, [card_running])
        changed = wb._reconcile_signal_fuel("test-episode", state)
        self.assertTrue(changed)
        self.assertIsNotNone(state.items["fuel_signal:c-cycle:accepted"].fuel_consumed_at)

        # 3. rejected → new rejected signal created
        card_rejected = Card(
            card_id="c-cycle", title="Cycle Card", status="rejected",
            card_type="module", summary="cycle summary", step=1,
        )
        self._set_cards(wb, [card_rejected])
        changed = wb._reconcile_signal_fuel("test-episode", state)
        self.assertTrue(changed)
        self.assertIn("fuel_signal:c-cycle:rejected", state.items)

        # 4. running → rejected signal invalidated
        self._set_cards(wb, [card_running])
        changed = wb._reconcile_signal_fuel("test-episode", state)
        self.assertTrue(changed)
        self.assertIsNotNone(state.items["fuel_signal:c-cycle:rejected"].fuel_consumed_at)

        # 5. accepted again → NO new signal because dedup marker still exists
        self._set_cards(wb, [card_accepted])
        changed = wb._reconcile_signal_fuel("test-episode", state)
        self.assertFalse(changed)

        # Both markers persist in state.items
        self.assertEqual(len(state.items), 2)
        self.assertIn("fuel_signal:c-cycle:accepted", state.items)
        self.assertIn("fuel_signal:c-cycle:rejected", state.items)


if __name__ == "__main__":
    unittest.main()
