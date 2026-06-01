from __future__ import annotations

from dataclasses import dataclass
import logging
from uuid import uuid4

from fastapi import HTTPException

from app.models.cards import Card
from app.models.manager_auto import ManagerAutoChainLimitBasis, ManagerAutoDirective, ManagerAutoState
from app.models.manager_auto import ManagerWakeEvent
from app.services.background_workboard_service import BackgroundWorkboardService
from app.services.manager_wake_service import ManagerWakeService
from app.services.project_event_service import ProjectEventService
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.services.chat_session_service import ChatSessionService
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
        manager_wake_service: ManagerWakeService | None = None,
    ) -> None:
        self.project_service = project_service
        self.project_event_service = project_event_service
        self.background_workboard_service = background_workboard_service
        self.manager_wake_service = manager_wake_service

    def get_state(self, project_id: str) -> ManagerAutoState:
        graph = self.project_service.graph_store(project_id).load_graph()
        payload = graph.metadata.get("manager_auto") or {}
        state = ManagerAutoState.model_validate(payload)
        limit_basis = self._chain_limit_basis(self.project_service.graph_store(project_id).load_cards())
        state.chain_limit_basis = limit_basis
        state.max_chain_count = max(state.max_chain_count, self._max_chain_count(limit_basis.executable_card_count))
        return state

    def get_view(self, project_id: str, session_id: str | None) -> ManagerAutoView:
        state = self.get_state(project_id)
        is_owner = bool(state.enabled and session_id and state.owner_session_id == session_id)
        btw_mode = bool(state.enabled and session_id and state.owner_session_id and state.owner_session_id != session_id)
        return ManagerAutoView(state=state, is_owner=is_owner, btw_mode=btw_mode)

    def enable_auto_flow(
        self,
        project_id: str,
        session_id: str,
        chat_session_service: ChatSessionService,
        manager_wake_service: ManagerWakeService,
        *,
        mode: str = "continuous",
        directive_text: str | None = None,
        message_id: str | None = None,
        trigger_wake: bool = True,
    ) -> tuple[ManagerAutoState, ManagerAutoDirective | None, ManagerWakeEvent | None]:
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
                or current_state.state not in {"completed", "cancelled", "stopped"}
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
            mode=mode,
            scope_objective=directive_text or None,
        )
        directive = None
        wake_event = None
        if directive_text:
            directive = self.add_directive(
                project_id,
                session_id,
                text=directive_text,
                message_id=message_id,
            )
            if trigger_wake:
                if self.should_trigger_directive_wake(project_id):
                    wake_event = ManagerWakeEvent(
                        wake_id=f"wake_{directive.id}",
                        project_id=project_id,
                        kind="directive_received",
                        source_type="directive",
                        source_id=directive.id,
                        severity="info",
                        message=f"收到新的 auto 指令：{directive.text}",
                        payload_summary={"directive_id": directive.id},
                        idempotency_key=f"directive:{directive.id}",
                        created_at=utc_now(),
                    )
                    manager_wake_service.enqueue(wake_event)
            state = self.get_state(project_id)
        return state, directive, wake_event

    def enable(self, project_id: str, session_id: str, *, mode: str = "continuous", scope_objective: str | None = None) -> ManagerAutoState:
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
            limit_basis = self._chain_limit_basis(store.load_cards())
            next_state = state.model_copy(deep=True)
            next_state.enabled = True
            next_state.wake_allowed = True
            next_state.scope_objective = scope_objective
            next_state.mode = "once" if mode == "once" else "continuous"
            next_state.owner_session_id = session_id
            next_state.state = "idle"
            next_state.view_workboard = True
            next_state.consume_workboard = True
            is_new_auto_scope = (
                not state.enabled
                or state.owner_session_id != session_id
                or state.scope_objective != scope_objective
            )
            if is_new_auto_scope:
                next_state.last_signaled_board_revision = None
                next_state.last_signaled_workboard_fingerprint = None
                next_state.last_signaled_workboard_fingerprint_at = None
                next_state.chain_count = 0
                next_state.auto_scope_id = f"scope_{uuid4().hex[:12]}"
            else:
                next_state.chain_count = state.chain_count
            next_state.started_at = next_state.started_at or now
            next_state.stopped_at = None
            next_state.stop_reason = None
            next_state.stop_message = None
            next_state.active_run_id = None
            next_state.active_job_id = None
            next_state.chain_limit_basis = limit_basis
            next_state.max_chain_count = self._max_chain_count(limit_basis.executable_card_count)
            graph.metadata["manager_auto"] = next_state.model_dump()
            store.save_graph(graph)
        if self.background_workboard_service is not None:
            return self.evaluate_workboard_and_maybe_signal(project_id, session_id)
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
            next_state.wake_allowed = False
            next_state.state = "cancelled" if reason == "user_stop" else "stopped"
            next_state.active_run_id = None
            next_state.active_job_id = None
            next_state.stopped_at = utc_now()
            next_state.stop_reason = reason
            next_state.stop_message = message
            
            # Directive Cleanup: 将所有 pending directives 标为 superseded
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
            graph.metadata["manager_auto"] = next_state.model_dump()
            store.save_graph(graph)
            self._emit_auto_event(project_id, next_state)
            return next_state

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
            # Reset chain count on new user directive because fresh intent was supplied
            state.chain_count = 0
            graph.metadata["manager_auto"] = state.model_dump()
            store.save_graph(graph)
            self._emit_auto_event(project_id, state)
            return directive

    def should_trigger_directive_wake(self, project_id: str) -> bool:
        state = self.get_state(project_id)
        if not state.enabled:
            return False
        return state.state not in {"running", "thinking"} and not state.active_run_id and not state.active_job_id

    def notify_turn_settled(self, project_id: str, session_id: str | None, *, async_boundary: bool = False) -> ManagerAutoState:
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required to settle an auto turn.")
        view = self.get_view(project_id, session_id)
        if not view.is_owner:
            raise HTTPException(status_code=409, detail="Only the auto owner session may settle an auto turn.")
        return self.evaluate_workboard_and_maybe_signal(project_id, session_id, from_turn_settlement=async_boundary)

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
        clear_active_run = run_id is not None
        clear_active_job = job_id is not None
        if clear_active_run or clear_active_job:
            state = self.set_runtime_state(
                project_id,
                clear_active_run=clear_active_run,
                clear_active_job=clear_active_job,
            )
        return self.evaluate_workboard_and_maybe_signal(project_id, owner_session_id)

    def pending_directives(self, project_id: str) -> list[ManagerAutoDirective]:
        state = self.get_state(project_id)
        return [item.model_copy(deep=True) for item in state.pending_directives if item.status == "pending"]

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
    def _chain_limit_basis(cards: list[Card]) -> ManagerAutoChainLimitBasis:
        executable_card_count = sum(1 for card in cards if card.status not in {"cancelled", "rejected"})
        return ManagerAutoChainLimitBasis(executable_card_count=executable_card_count)

    @staticmethod
    def _max_chain_count(executable_card_count: int) -> int:
        return max(10, min(80, executable_card_count * 3))

    def evaluate_workboard_and_maybe_signal(
        self,
        project_id: str,
        session_id: str,
        *,
        from_turn_settlement: bool = False,
    ) -> ManagerAutoState:
        if self.background_workboard_service is None:
            return self.get_state(project_id)
        snapshot = self.background_workboard_service.signal_snapshot(project_id, session_id=session_id)
        signal: ManagerWakeEvent | None = None
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            graph = store.load_graph()
            state = ManagerAutoState.model_validate(graph.metadata.get("manager_auto") or {})
            if not state.wake_allowed or state.owner_session_id != session_id:
                return state
            if state.state in {"running", "thinking"} and not from_turn_settlement:
                return state

            # Chain budget guard: stop before any further enqueue
            if state.chain_count >= state.max_chain_count:
                next_state = self.stop(
                    project_id,
                    session_id,
                    reason="auto_chain_budget_exceeded",
                    message=f"自动运行已暂停：连续唤醒次数达到安全上限 {state.max_chain_count} 次。如需继续，请重新发送 /auto <目标>。",
                )
                return next_state

            if snapshot["has_actionable"]:
                next_state_value = "active"
            elif snapshot["has_running"]:
                next_state_value = "idle"
            elif snapshot["has_blocked_for_user"]:
                next_state_value = "blocked"
            else:
                next_state_value = "completed"
            state.state = next_state_value  # type: ignore[assignment]

            fingerprint = snapshot.get("fingerprint", "")
            actionability = snapshot.get("actionability", {})

            should_enqueue = (
                snapshot["has_actionable"]
                and self.manager_wake_service is not None
                and state.consume_workboard
                and next_state_value not in {"running", "thinking"}
            )

            if should_enqueue and fingerprint and fingerprint == state.last_signaled_workboard_fingerprint:
                should_enqueue = False

            if should_enqueue and from_turn_settlement:
                # Settlement requeue guard: require new frontier or newly unhandled manager blocker
                has_new_frontier = actionability.get("has_startable_frontier", False)
                has_new_manager_blocker = actionability.get("has_manager_actionable", False)
                if not has_new_frontier and not has_new_manager_blocker:
                    should_enqueue = False

            # Defensive: empty fingerprint means no semantic action units; do not enqueue
            if should_enqueue and not fingerprint:
                should_enqueue = False

            if should_enqueue:
                scope_id = state.auto_scope_id or "legacy"
                signal = ManagerWakeEvent(
                    wake_id=f"wake_workboard_{uuid4().hex[:12]}",
                    project_id=project_id,
                    kind="workboard_actionable",
                    source_type="workboard",
                    source_id=f"workboard:{snapshot['revision']}",
                    severity="info",
                    message="Background workboard has actionable items.",
                    payload_summary={
                        "counts": snapshot["counts"],
                        "revision": snapshot["revision"],
                        "fingerprint": fingerprint,
                        "actionability": actionability,
                        "from_turn_settlement": from_turn_settlement,
                    },
                    idempotency_key=f"workboard:{project_id}:{scope_id}:{fingerprint}",
                    created_at=utc_now(),
                )
                # fingerprint / last_wake_id are written after successful enqueue so that
                # enqueue failures do not suppress subsequent retries.
            graph.metadata["manager_auto"] = state.model_dump()
            store.save_graph(graph)
        if signal is not None:
            enqueued = self.manager_wake_service.enqueue(signal)
            # Record signaled state only after successful enqueue
            with lock:
                store = self.project_service.graph_store(project_id)
                graph = store.load_graph()
                state = ManagerAutoState.model_validate(graph.metadata.get("manager_auto") or {})
                state.last_wake_id = enqueued.wake_id
                state.last_signaled_workboard_fingerprint = fingerprint
                state.last_signaled_workboard_fingerprint_at = utc_now()
                state.last_signaled_board_revision = snapshot["revision"]
                graph.metadata["manager_auto"] = state.model_dump()
                store.save_graph(graph)
        self._emit_auto_event(project_id, state)
        return state
