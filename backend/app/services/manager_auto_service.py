from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from threading import Thread
from uuid import uuid4

from fastapi import HTTPException

from app.models.background import WorkboardFuelSnapshot
from app.models.chat import ChatRequest
from app.models.manager_auto import ManagerAutoDirective, ManagerAutoState
from app.services.background_workboard_service import BackgroundWorkboardService
from app.services.project_event_service import ProjectEventService
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.services.chat_session_service import ChatSessionService
    from app.services.chat_stream_relay import ChatStreamRelay
from app.services.project_service import ProjectService
from app.services.utils import utc_now


logger = logging.getLogger(__name__)


@dataclass
class ManagerAutoView:
    state: ManagerAutoState
    is_owner: bool
    btw_mode: bool


class ManagerAutoService:
    def __init__(
        self,
        project_service: ProjectService,
        project_event_service: ProjectEventService | None = None,
        background_workboard_service: BackgroundWorkboardService | None = None,
    ) -> None:
        self.project_service = project_service
        self.project_event_service = project_event_service
        self.background_workboard_service = background_workboard_service
        # Doc 42: Wake dispatch is done inline (no persistent wake queue).
        # The chat_stream_relay is injected lazily to avoid circular deps.
        self._chat_stream_relay: ChatStreamRelay | None = None

    def set_chat_stream_relay(self, relay: ChatStreamRelay) -> None:
        self._chat_stream_relay = relay

    # ── state access ──────────────────────────────────────────────

    def get_state(self, project_id: str) -> ManagerAutoState:
        graph = self.project_service.graph_store(project_id).load_graph()
        payload = graph.metadata.get("manager_auto") or {}
        state = ManagerAutoState.model_validate(payload)
        return state

    def get_view(self, project_id: str, session_id: str | None) -> ManagerAutoView:
        state = self.get_state(project_id)
        is_owner = bool(state.enabled and session_id and state.owner_session_id == session_id)
        btw_mode = bool(state.enabled and session_id and state.owner_session_id and state.owner_session_id != session_id)
        return ManagerAutoView(state=state, is_owner=is_owner, btw_mode=btw_mode)

    # ── enable / stop ─────────────────────────────────────────────

    def enable_auto_flow(
        self,
        project_id: str,
        session_id: str,
        chat_session_service: ChatSessionService,
        *,
        directive_text: str | None = None,
        message_id: str | None = None,
    ) -> tuple[ManagerAutoState, ManagerAutoDirective | None]:
        """Doc 42: Single-episode entry point. No more mode or trigger_wake."""
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required to enable auto mode.")
        try:
            chat_session_service.get_session(project_id, session_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        directive_text = str(directive_text or "").strip()
        current_state = self.get_state(project_id)
        if (
            directive_text
            and current_state.enabled
            and current_state.owner_session_id == session_id
            and (
                any(item.status == "pending" for item in current_state.pending_directives)
                or current_state.state not in {"complete", "finished"}
            )
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "A scoped workboard continuation is already active for this session. "
                    "Stop it first or append steering through the directives path."
                ),
            )

        state = self.enable(
            project_id,
            session_id,
            scope_objective=directive_text or None,
        )
        directive = None
        if directive_text:
            directive = self.add_directive(
                project_id,
                session_id,
                text=directive_text,
                message_id=message_id,
            )
        # Evaluate the workboard immediately — returns (state, wake_payload | None)
        state, wake_payload = self.evaluate_workboard_and_maybe_signal(project_id, session_id)
        if wake_payload is not None:
            self._dispatch_wake(project_id, session_id, wake_payload)
        return state, directive

    def enable(self, project_id: str, session_id: str, *, scope_objective: str | None = None) -> ManagerAutoState:
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required to enable auto mode.")
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            graph = store.load_graph()
            state = ManagerAutoState.model_validate(graph.metadata.get("manager_auto") or {})
            if state.enabled and state.owner_session_id and state.owner_session_id != session_id:
                raise HTTPException(status_code=409, detail="Auto mode is already owned by another session.")
            now = utc_now()
            next_state = state.model_copy(deep=True)
            next_state.enabled = True
            next_state.scope_objective = scope_objective
            next_state.owner_session_id = session_id
            next_state.state = "idle"
            is_new_auto_scope = (
                not state.enabled
                or state.owner_session_id != session_id
                or state.scope_objective != scope_objective
            )
            if is_new_auto_scope:
                next_state.chain_count = 0
                next_state.auto_scope_id = f"scope_{uuid4().hex[:12]}"
                # Doc 42: reset latch fields on new scope
                next_state.fuel_revision = 0
                next_state.last_notified_revision = 0
                next_state.wake_in_flight = False
                next_state.completion_notified = False
                next_state.wake_window = []
                next_state.finished_at = None
                next_state.stop_reason = None
                next_state.stop_message = None
            next_state.started_at = next_state.started_at or now
            next_state.stopped_at = None
            next_state.active_run_id = None
            next_state.active_job_id = None
            next_state.max_chain_count = 50
            graph.metadata["manager_auto"] = next_state.model_dump()
            store.save_graph(graph)
        self._emit_auto_event(project_id, next_state)
        return next_state

    def stop(self, project_id: str, session_id: str, *, reason: str, message: str) -> ManagerAutoState:
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required to stop auto mode.")
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            graph = store.load_graph()
            state = ManagerAutoState.model_validate(graph.metadata.get("manager_auto") or {})
            if state.enabled and state.owner_session_id and state.owner_session_id != session_id:
                raise HTTPException(status_code=409, detail="Only the auto owner session may stop auto mode.")
            next_state = state.model_copy(deep=True)
            next_state.enabled = False
            # Doc 42: All stops go to finished
            next_state.state = "finished"
            next_state.active_run_id = None
            next_state.active_job_id = None
            next_state.finished_at = utc_now()
            next_state.stopped_at = next_state.finished_at
            next_state.stop_reason = reason
            next_state.stop_message = message
            # Doc 42: Map legacy stop reasons
            if reason == "auto_chain_budget_exceeded":
                next_state.stop_reason = "chain_limit"
            elif reason == "auto_once_complete":
                next_state.stop_reason = "user_stop"

            # Directive Cleanup: supersede all pending directives
            now = utc_now()
            for directive in next_state.pending_directives:
                if directive.status == "pending":
                    directive.status = "superseded"
                    directive.resolved_at = now
                    directive.resolution_note = "Auto session stopped."

            graph.metadata["manager_auto"] = next_state.model_dump()
            store.save_graph(graph)
            self._emit_auto_event(project_id, next_state)
            return next_state

    def finish_auto_episode(self, project_id: str, session_id: str, *, complete_message: str = "") -> dict:
        """Doc 42 Section 6: Finish tool — valid only when state == complete."""
        if not session_id:
            return {"ok": False, "error_code": "auto_not_complete", "current_state": "unknown"}
        lock = self.project_service.lock_for(project_id)
        with lock:
            state = self.get_state(project_id)
            if not state.enabled or state.owner_session_id != session_id:
                return {"ok": False, "error_code": "auto_not_owner", "current_state": state.state}
            if state.state != "complete":
                return {"ok": False, "error_code": "auto_not_complete", "current_state": state.state}
            store = self.project_service.graph_store(project_id)
            graph = store.load_graph()
            state = ManagerAutoState.model_validate(graph.metadata.get("manager_auto") or {})
            now = utc_now()
            state.state = "finished"
            state.finished_at = now
            state.stopped_at = now
            state.stop_reason = "finished"
            state.stop_message = complete_message or "Auto episode finished by Manager."
            state.enabled = False
            graph.metadata["manager_auto"] = state.model_dump()
            store.save_graph(graph)
            self._emit_auto_event(project_id, state)
            return {
                "ok": True,
                "state": "finished",
                "complete_message": complete_message,
                "stopped_at": now,
            }

    # ── runtime state mutations ───────────────────────────────────

    def set_runtime_state(
        self,
        project_id: str,
        *,
        state_value: str | None = None,
        active_run_id: str | None = None,
        active_job_id: str | None = None,
        clear_active_run: bool = False,
        clear_active_job: bool = False,
        last_wake_id: str | None = None,
        increment_chain: bool = False,
        wake_in_flight: bool | None = None,
    ) -> ManagerAutoState:
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            graph = store.load_graph()
            state = ManagerAutoState.model_validate(graph.metadata.get("manager_auto") or {})
            next_state = state.model_copy(deep=True)
            if state_value is not None:
                next_state.state = state_value  # type: ignore[assignment]
            if clear_active_run:
                next_state.active_run_id = None
            if active_run_id is not None:
                next_state.active_run_id = active_run_id
            if clear_active_job:
                next_state.active_job_id = None
            if active_job_id is not None:
                next_state.active_job_id = active_job_id
            if last_wake_id is not None:
                next_state.last_wake_id = last_wake_id
            if increment_chain:
                next_state.chain_count += 1
            if wake_in_flight is not None:
                next_state.wake_in_flight = wake_in_flight
            graph.metadata["manager_auto"] = next_state.model_dump()
            store.save_graph(graph)
            self._emit_auto_event(project_id, next_state)
            return next_state

    def increment_fuel_revision(self, project_id: str) -> ManagerAutoState:
        """Doc 42: Increment fuel_revision and clear completion_notified."""
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            graph = store.load_graph()
            state = ManagerAutoState.model_validate(graph.metadata.get("manager_auto") or {})
            state.fuel_revision += 1
            state.completion_notified = False
            graph.metadata["manager_auto"] = state.model_dump()
            store.save_graph(graph)
            self._emit_auto_event(project_id, state)
            return state

    # ── directives ────────────────────────────────────────────────

    def add_directive(self, project_id: str, session_id: str, *, text: str, message_id: str | None = None) -> ManagerAutoDirective:
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required to add an auto directive.")
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            graph = store.load_graph()
            state = ManagerAutoState.model_validate(graph.metadata.get("manager_auto") or {})
            if not state.enabled or state.owner_session_id != session_id:
                raise HTTPException(status_code=409, detail="Only the auto owner session may add directives.")
            directive = ManagerAutoDirective(
                id=f"directive_{uuid4().hex[:12]}",
                message_id=message_id,
                text=text.strip(),
                created_at=utc_now(),
            )
            state.pending_directives.append(directive)
            # Reset chain count on new user directive
            state.chain_count = 0
            graph.metadata["manager_auto"] = state.model_dump()
            store.save_graph(graph)
            self._emit_auto_event(project_id, state)
            return directive

    def pending_directives(self, project_id: str) -> list[ManagerAutoDirective]:
        state = self.get_state(project_id)
        return [item for item in state.pending_directives if item.status == "pending"]

    def resolve_directives(self, project_id: str, directive_ids: list[str], *, status: str, note: str | None = None) -> list[ManagerAutoDirective]:
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            graph = store.load_graph()
            state = ManagerAutoState.model_validate(graph.metadata.get("manager_auto") or {})
            now = utc_now()
            resolved: list[ManagerAutoDirective] = []
            for item in state.pending_directives:
                if item.id in directive_ids and item.status == "pending":
                    item.status = status  # type: ignore[assignment]
                    item.resolved_at = now
                    item.resolution_note = note
                    resolved.append(item.model_copy(deep=True))
            graph.metadata["manager_auto"] = state.model_dump()
            store.save_graph(graph)
            self._emit_auto_event(project_id, state)
            return resolved

    # ── turn settlement ───────────────────────────────────────────

    def notify_turn_settled(self, project_id: str, session_id: str | None, *, async_boundary: bool = False) -> ManagerAutoState:
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required to settle an auto turn.")
        view = self.get_view(project_id, session_id)
        if not view.is_owner:
            raise HTTPException(status_code=409, detail="Only the auto owner session may settle an auto turn.")
        # Doc 42: Clear wake_in_flight on turn settlement
        self.set_runtime_state(project_id, wake_in_flight=False, increment_chain=True)
        state, wake_payload = self.evaluate_workboard_and_maybe_signal(project_id, session_id, from_turn_settlement=async_boundary)
        if wake_payload is not None:
            self._dispatch_wake(project_id, session_id, wake_payload)
        return state

    def notify_background_task_terminal(
        self,
        project_id: str,
        *,
        run_id: str | None = None,
        job_id: str | None = None,
    ) -> ManagerAutoState:
        state = self.get_state(project_id)
        owner_session_id = state.owner_session_id
        if not state.enabled or not owner_session_id:
            return state
        clear_active_run = run_id is not None and state.active_run_id == run_id
        clear_active_job = job_id is not None and state.active_job_id == job_id
        if clear_active_run or clear_active_job:
            state = self.set_runtime_state(
                project_id,
                clear_active_run=clear_active_run,
                clear_active_job=clear_active_job,
            )
        state, wake_payload = self.evaluate_workboard_and_maybe_signal(project_id, owner_session_id, from_turn_settlement=True)
        if wake_payload is not None:
            self._dispatch_wake(project_id, owner_session_id, wake_payload)
        return state

    # ── state derivation (Doc 42 Section 4) ───────────────────────

    def _derive_state(self, state: ManagerAutoState, fuel: WorkboardFuelSnapshot) -> str:
        """Derive episode state from has_running + has_wake_fuel."""
        if state.state == "finished":
            return "finished"

        has_running = fuel.active_run_count > 0 or bool(state.active_job_id)
        has_wake_fuel = (fuel.todo_count + fuel.complete_signal_count + fuel.block_signal_count) > 0

        # If we were in complete and fuel appeared, exit complete back to pending_wake
        if state.state == "complete" and has_wake_fuel:
            return "pending_wake" if not has_running else "running"

        if has_running:
            return "running" if has_wake_fuel else "idle"
        if has_wake_fuel:
            return "pending_wake"
        # Not running, no fuel, not finished → complete (evaluation phase)
        if state.state not in ("finished",):
            return "complete"
        return state.state

    # ── evaluate + wake emission (Doc 42 Section 5, 7) ────────────

    def evaluate_workboard_and_maybe_signal(
        self,
        project_id: str,
        session_id: str,
        *,
        from_turn_settlement: bool = False,
    ) -> tuple[ManagerAutoState, dict | None]:
        """Doc 42: Evaluate workboard fuel and emit transient wake payload if conditions met.

        Returns (state, wake_payload | None). The caller is responsible for dispatching
        the wake payload via _dispatch_wake.
        """
        fuel = WorkboardFuelSnapshot()
        if self.background_workboard_service is not None:
            fuel = self.background_workboard_service.get_wake_fuel(project_id)

        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            graph = store.load_graph()
            state = ManagerAutoState.model_validate(graph.metadata.get("manager_auto") or {})

            if not state.enabled or state.owner_session_id != session_id:
                return state, None

            # ── risk controls first ──
            # Prune wake window
            state.wake_window = [ts for ts in state.wake_window if self._within_last_minute(ts)]

            # Chain limit guard
            if state.chain_count > state.max_chain_count:
                next_state = self.stop(
                    project_id,
                    session_id,
                    reason="chain_limit",
                    message=f"自动运行已暂停：连续唤醒次数达到安全上限 {state.max_chain_count} 次。如需继续，请重新发送 /auto <目标>。",
                )
                return next_state, None

            # ── derive new state ──
            old_state = state.state
            new_state_value = self._derive_state(state, fuel)
            state.state = new_state_value  # type: ignore[assignment]

            # ── emit wake events based on state transitions ──
            wake_payload = None

            # workboard_evaluate: entering pending_wake
            if (
                state.state == "pending_wake"
                and not state.wake_in_flight
                and state.fuel_revision > state.last_notified_revision
            ):
                # Wake storm guard
                state.wake_window.append(utc_now())
                if len(state.wake_window) > 5:
                    next_state = self.stop(
                        project_id,
                        session_id,
                        reason="wake_storm",
                        message="自动运行已暂停：1分钟内唤醒超过5次，已触发风暴保护。",
                    )
                    graph.metadata["manager_auto"] = next_state.model_dump()
                    store.save_graph(graph)
                    self._emit_auto_event(project_id, next_state)
                    return next_state, None

                # Mark all fuel as seen at this revision before dispatching (N2)
                if self.background_workboard_service is not None:
                    self.background_workboard_service.mark_fuel_seen(project_id, state.fuel_revision)

                wake_payload = {
                    "kind": "workboard_evaluate",
                    "fuel_counts": {
                        "todo": fuel.todo_count,
                        "complete_signal": fuel.complete_signal_count,
                        "block_signal": fuel.block_signal_count,
                    },
                    "top_item_ids": fuel.top_item_ids,
                    "top_card_ids": fuel.top_card_ids,
                    "fuel_revision": state.fuel_revision,
                }
                state.last_notified_revision = state.fuel_revision
                state.wake_in_flight = True

            # complete_evaluate: entering complete
            # Doc 42: same wake_in_flight mutex and wake_storm guard as workboard_evaluate
            elif state.state == "complete" and not state.completion_notified and not state.wake_in_flight:
                state.wake_window.append(utc_now())
                if len(state.wake_window) > 5:
                    next_state = self.stop(
                        project_id,
                        session_id,
                        reason="wake_storm",
                        message="自动运行已暂停：1分钟内唤醒超过5次，已触发风暴保护。",
                    )
                    graph.metadata["manager_auto"] = next_state.model_dump()
                    store.save_graph(graph)
                    self._emit_auto_event(project_id, next_state)
                    return next_state, None

                wake_payload = {
                    "kind": "complete_evaluate",
                    "prompt": "当前任务似乎已完成，请回顾任务总结情况。如果确认完成，请调用 finish_auto_episode；如果仍需继续，请调整 card 或加入新的 todo。",
                    "fuel_revision": state.fuel_revision,
                }
                state.completion_notified = True
                state.wake_in_flight = True

            # If we stayed in pending_wake and wake_in_flight is already True,
            # don't re-emit. The latch prevents duplicate wakes.
            # If we entered complete but completion_notified is already True,
            # don't re-emit either.

            # Clear completion_notified if fuel was consumed during the turn
            # (handled by increment_fuel_revision which clears completion_notified)

            graph.metadata["manager_auto"] = state.model_dump()
            store.save_graph(graph)

        self._emit_auto_event(project_id, state)
        return state, wake_payload

    # ── wake dispatch ─────────────────────────────────────────────

    def _dispatch_wake(self, project_id: str, session_id: str, wake_payload: dict) -> None:
        """Doc 42: Dispatch a transient wake payload to the Manager agent.

        Runs in a background thread so callers (including background task callbacks)
        don't block waiting for the Manager's response.
        """
        relay = self._chat_stream_relay
        if relay is None:
            logger.warning("Cannot dispatch wake — chat_stream_relay not set on ManagerAutoService")
            return

        def _run():
            try:
                # Build wake prompt
                kind = wake_payload.get("kind", "workboard_evaluate")
                if kind == "workboard_evaluate":
                    fuel = wake_payload.get("fuel_counts", {})
                    message_lines = [
                        "Auto mode: workboard has wake fuel.",
                        f"fuel_counts: todo={fuel.get('todo', 0)} complete_signal={fuel.get('complete_signal', 0)} block_signal={fuel.get('block_signal', 0)}",
                        f"fuel_revision: {wake_payload.get('fuel_revision', 0)}",
                    ]
                    message = "\n".join(message_lines)
                elif kind == "complete_evaluate":
                    message = wake_payload.get("prompt", "Episode complete — please review and finish or continue.")
                else:
                    message = f"Wake: {kind}"

                # Get pending directives
                pending = self.pending_directives(project_id)
                directive_text = "\n".join(f"- {item.text}" for item in pending if item.text)
                if directive_text:
                    message += f"\npending_directives:\n{directive_text}"

                message += "\nCall get_background_workboard first. This turn may end with at most one async-boundary-yielding action (one submit_claimed_workboard_items, or one start_card_run / rerun_card, or one install_runtime_dependencies). You may combine independent non-yielding tool calls and may start one dependency install plus one run-yielding action in the same turn when they are truly independent. When planning new cards from a frontier wake, align new cards to the parallel_group of existing ready_to_start items that share their input layer."
                message += "\nIf you start background work, stop after reporting ids and let async-boundary yield the turn."
                message += "\nIf a tool returns pending_approvals, rejected_approvals, manual_preparation_required, partial_resolution, fallback_available, unsupported_source_spec, or runtime_missing, do not retry or ask the user — skip/defer/block the workboard item with the reason and move on."

                request = ChatRequest(
                    message=message,
                    session_id=session_id,
                    thinking_effort="medium",
                    messages=[],
                )
                wake_id = f"wake_{uuid4().hex[:12]}"
                relay.run_to_session(
                    project_id,
                    session_id,
                    request,
                    message_id=f"wake_response_{wake_id}",
                    initial_thinking="Manager 正在处理 AUTO 唤醒…",
                )

                # Resolve pending directives after the turn
                if pending:
                    self.resolve_directives(
                        project_id,
                        [item.id for item in pending],
                        status="consumed",
                        note=f"Handled with wake {wake_id}",
                    )

                # Doc 42: Manager-agent calls /turn-settled after each auto turn.
                # Do NOT call notify_turn_settled here — the agent is the
                # authoritative source of turn completion. Calling it here would
                # double-increment chain_count and risk re-dispatching a wake.

            except Exception:
                logger.exception("Wake dispatch failed for project=%s kind=%s", project_id, kind)
                try:
                    self.stop(project_id, session_id, reason="error", message="Wake dispatch failed.")
                except Exception:
                    logger.exception("Failed to stop auto after dispatch failure project=%s", project_id)

        Thread(target=_run, name=f"wake-dispatch-{project_id}", daemon=True).start()

    # ── helpers ───────────────────────────────────────────────────

    def _emit_auto_event(self, project_id: str, state: ManagerAutoState) -> None:
        if self.project_event_service is None:
            return
        try:
            self.project_event_service.emit(
                project_id,
                reason="manager_auto_changed",
                run_id=state.active_run_id,
                job_id=state.active_job_id,
                status=state.state,
                payload={"enabled": state.enabled, "owner_session_id": state.owner_session_id},
            )
        except Exception:
            logger.exception("Failed to emit manager auto project event: project_id=%s state=%s", project_id, state.state)

    def can_mutate(self, project_id: str, session_id: str | None) -> tuple[bool, ManagerAutoState]:
        state = self.get_state(project_id)
        if not state.enabled:
            return True, state
        if session_id and state.owner_session_id == session_id:
            return True, state
        return False, state

    def assert_mutation_allowed(self, project_id: str, session_id: str | None, tool_name: str) -> None:
        allowed, state = self.can_mutate(project_id, session_id)
        if allowed:
            return
        owner = state.owner_session_id or "another session"
        raise HTTPException(
            status_code=409,
            detail=(
                f"Auto mode is active for this project. Tool {tool_name} is locked to owner session {owner}. "
                "Switch to the owner session or stop auto mode first."
            ),
        )

    @staticmethod
    def _within_last_minute(ts_str: str) -> bool:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            return False
        return (datetime.now(timezone.utc) - ts).total_seconds() < 60
