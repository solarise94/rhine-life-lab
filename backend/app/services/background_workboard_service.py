from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha1
import json
from pathlib import Path
from threading import RLock
from typing import Any

from fastapi import HTTPException

from app.models.background import BackgroundWorkboardState, BackgroundWorkboardView, WorkboardItemRecord
from app.models.runs import ExecutorFailureReport
from app.services.background_task_service import BackgroundTaskService
from app.services.flow_service import FlowService
from app.services.project_service import ProjectService
from app.services.runtime_dependency_state_service import ACTIVE_RUNTIME_DEPENDENCY_JOB_STATUSES
from app.services.runtime_dependency_state_service import dependency_blockers_by_card
from app.services.utils import atomic_write_json, read_json, utc_now


_ACTIVE_TASK_STATUSES = {"queued", "launching", "running", "waiting"}
_SIGNALABLE_LANES = {"todo", "needs_manager", "completed", "ready_to_start"}


class BackgroundWorkboardService:
    def __init__(self, project_service: ProjectService, background_task_service: BackgroundTaskService) -> None:
        self.project_service = project_service
        self.background_task_service = background_task_service
        self.flow_service = FlowService(project_service)
        self._locks: dict[str, RLock] = {}
        self._locks_guard = RLock()

    def get_workboard(self, project_id: str, *, session_id: str | None = None) -> BackgroundWorkboardView:
        with self._lock_for(project_id):
            state = self._load_state(project_id)
            changed = self._release_expired_claims(state)
            if changed:
                self._save_state(project_id, state)
            derived = self._derived_items(project_id, state=state)
            merged = self._merge_items(derived, state.items)
            view = self._build_view(project_id, merged, session_id=session_id)
            state.last_revision = view.revision
            self._save_state(project_id, state)
            return view

    def promote_workboard_item_to_todo(self, project_id: str, item_id: str, session_id: str) -> WorkboardItemRecord:
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required.")
        with self._lock_for(project_id):
            state = self._load_state(project_id)
            source = self._find_view_item(project_id, item_id, session_id=session_id, state=state)
            if source is None:
                raise HTTPException(status_code=404, detail=f"Workboard item not found: {item_id}")
            if source["lane"] != "ready_to_start":
                raise HTTPException(status_code=409, detail=f"Only ready_to_start items may be promoted in P0. Got {source['lane']}.")
            todo_id = f"todo:{session_id}:{item_id}"
            existing = state.items.get(todo_id)
            if existing and existing.status != "done":
                return existing
            record = WorkboardItemRecord(
                item_id=todo_id,
                lane="todo",
                kind="start_ready_card",
                title=source.get("title"),
                card_id=source.get("card_id"),
                task_id=source.get("task_id"),
                source_item_id=item_id,
                source_lane="ready_to_start",
                status="pending",
                action_type="start_card_run",
                payload={"card_id": source.get("card_id")},
                updated_at=utc_now(),
            )
            state.items[todo_id] = record
            self._save_state(project_id, state)
            return record

    def claim_workboard_item(self, project_id: str, item_id: str, session_id: str, *, lease_seconds: int = 300) -> WorkboardItemRecord:
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required.")
        with self._lock_for(project_id):
            state = self._load_state(project_id)
            item = state.items.get(item_id)
            if item is None:
                item_dict = self._find_view_item(project_id, item_id, session_id=session_id, state=state)
                if item_dict is None:
                    raise HTTPException(status_code=404, detail=f"Workboard item not found: {item_id}")
                item = WorkboardItemRecord.model_validate({**item_dict, "updated_at": utc_now()})
            if item.status not in {"pending", "failed"}:
                raise HTTPException(status_code=409, detail=f"Workboard item {item_id} cannot be claimed from status {item.status}.")
            claimed_at = utc_now()
            claim_expires_at = _utc_after_seconds(lease_seconds)
            claimed = item.model_copy(
                update={
                    "status": "claimed",
                    "claimed_by_session_id": session_id,
                    "claimed_at": claimed_at,
                    "claim_expires_at": claim_expires_at,
                    "updated_at": claimed_at,
                }
            )
            state.items[item_id] = claimed
            self._save_state(project_id, state)
            return claimed

    def complete_workboard_item(self, project_id: str, item_id: str, session_id: str, *, summary: str | None = None, message: str | None = None) -> WorkboardItemRecord:
        return self._update_item_status(project_id, item_id, session_id, status="done", summary=summary, message=message)

    def defer_workboard_item(self, project_id: str, item_id: str, session_id: str, *, message: str | None = None) -> WorkboardItemRecord:
        return self._update_item_status(project_id, item_id, session_id, status="deferred", message=message)

    def block_workboard_item_for_user(self, project_id: str, item_id: str, session_id: str, *, message: str | None = None) -> WorkboardItemRecord:
        return self._update_item_status(project_id, item_id, session_id, status="blocked_for_user", message=message)

    def reopen_workboard_item(self, project_id: str, item_id: str) -> WorkboardItemRecord:
        with self._lock_for(project_id):
            state = self._load_state(project_id)
            item = state.items.get(item_id)
            if item is None:
                raise HTTPException(status_code=404, detail=f"Workboard item not found: {item_id}")
            reopened = item.model_copy(
                update={
                    "status": "pending",
                    "claimed_by_session_id": None,
                    "claimed_at": None,
                    "claim_expires_at": None,
                    "message": None,
                    "updated_at": utc_now(),
                }
            )
            state.items[item_id] = reopened
            self._save_state(project_id, state)
            return reopened

    def submit_claimed_workboard_items(
        self,
        project_id: str,
        item_ids: list[str],
        *,
        session_id: str,
        start_callback,
    ) -> dict[str, Any]:
        started: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        pending_starts: list[tuple[str, str]] = []
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required.")
        runtime_dependency_blockers = dependency_blockers_by_card(self.project_service.project_path(project_id))
        current_work_order = self.flow_service.get_work_order(
            project_id,
            runtime_dependency_blockers=runtime_dependency_blockers,
        )
        work_items_by_card_id = {
            str(item.get("card_id") or ""): item
            for item in current_work_order["work_items"]
            if isinstance(item, dict) and item.get("card_id")
        }
        with self._lock_for(project_id):
            state = self._load_state(project_id)
            changed = self._release_expired_claims(state)
            derived_items = self._derived_items(project_id, state=state)
            merged_items = self._merge_items(derived_items, state.items)
            view = self._build_view(project_id, merged_items, session_id=session_id)
            if changed:
                self._save_state(project_id, state)
            view_index = self._view_index(view)
            for item_id in item_ids:
                item = state.items.get(item_id)
                if item is None:
                    blocked.append({"item_id": item_id, "reason": "missing_todo_item"})
                    continue
                if item.lane != "todo" or item.kind != "start_ready_card":
                    blocked.append({"item_id": item_id, "reason": "unsupported_todo_kind"})
                    continue
                if item.claimed_by_session_id != session_id or item.status != "claimed":
                    blocked.append({"item_id": item_id, "reason": "not_claimed_by_session"})
                    continue
                source_id = item.source_item_id or ""
                source = view_index.get(source_id)
                if source is None:
                    derived_source = derived_items.get(source_id)
                    source = derived_source.model_dump() if derived_source is not None else None
                card_id = str(item.payload.get("card_id") or item.card_id or "")
                if source is None or source.get("lane") != "ready_to_start":
                    runtime_dependency_blocker = None
                    if card_id:
                        work_item = work_items_by_card_id.get(card_id)
                        if isinstance(work_item, dict):
                            runtime_dependency_blocker = work_item.get("runtime_dependency_blocker")
                    if (
                        isinstance(runtime_dependency_blocker, dict)
                        and str(runtime_dependency_blocker.get("status") or "") in ACTIVE_RUNTIME_DEPENDENCY_JOB_STATUSES
                    ):
                        blocked.append(
                            {
                                "item_id": item_id,
                                "card_id": card_id,
                                "reason": f"Card {card_id} is waiting for runtime dependency repair to finish.",
                                "error_code": "runtime_dependency_repair_in_progress",
                                "job_id": runtime_dependency_blocker.get("job_id"),
                                "retry_after_signal": "runtime_dependency_install_terminal",
                            }
                        )
                    else:
                        blocked.append({"item_id": item_id, "reason": "source_not_ready"})
                    item = item.model_copy(update={"status": "failed", "updated_at": utc_now(), "message": "Source card is no longer ready to start."})
                    state.items[item_id] = item
                    continue
                if not card_id:
                    blocked.append({"item_id": item_id, "reason": "missing_card_id"})
                    continue
                state.items[item_id] = item.model_copy(
                    update={
                        "status": "processing",
                        "claim_expires_at": None,
                        "message": None,
                        "updated_at": utc_now(),
                    }
                )
                pending_starts.append((item_id, card_id))
            self._save_state(project_id, state)

        for item_id, card_id in pending_starts:
            try:
                response = start_callback(project_id, card_id)
            except HTTPException as exc:
                if isinstance(exc.detail, dict):
                    blocked.append(
                        {
                            "item_id": item_id,
                            "card_id": card_id,
                            "reason": exc.detail.get("message") or str(exc.detail),
                            "error_code": exc.detail.get("error_code"),
                            "job_id": exc.detail.get("job_id"),
                            "retry_after_signal": exc.detail.get("retry_after_signal"),
                        }
                    )
                else:
                    blocked.append({"item_id": item_id, "card_id": card_id, "reason": str(exc.detail)})
                with self._lock_for(project_id):
                    state = self._load_state(project_id)
                    item = state.items.get(item_id)
                    if item is not None:
                        state.items[item_id] = item.model_copy(
                            update={
                                "status": "failed",
                                "updated_at": utc_now(),
                                "message": str(exc.detail),
                            }
                        )
                        self._save_state(project_id, state)
                continue
            started.append(
                {
                    "item_id": item_id,
                    "card_id": card_id,
                    "run_id": response.get("run_id"),
                    "task_id": response.get("task_id"),
                    "status": response.get("status"),
                    "pending_approvals": response.get("pending_approvals") or [],
                    "rejected_approvals": response.get("rejected_approvals") or [],
                }
            )
            with self._lock_for(project_id):
                state = self._load_state(project_id)
                item = state.items.get(item_id)
                if item is not None:
                    state.items[item_id] = item.model_copy(
                        update={
                            "status": "done",
                            "summary": f"Started background run {response.get('run_id')}.",
                            "message": None,
                            "updated_at": utc_now(),
                        }
                    )
                    self._save_state(project_id, state)
        return {
            "ok": bool(started),
            "background": bool(started),
            "async_boundary": bool(started),
            "do_not_poll": bool(started),
            "wait_for_wake": bool(started),
            "started": started,
            "blocked": blocked,
        }

    def signal_snapshot(self, project_id: str, *, session_id: str | None = None) -> dict[str, Any]:
        view = self.get_workboard(project_id, session_id=session_id)
        return self._semantic_wake_snapshot_from_view(view)

    @staticmethod
    def _semantic_wake_snapshot_from_view(view: BackgroundWorkboardView) -> dict[str, Any]:
        counts = view.counts

        # Filter ready projections: only truly fresh pending frontier
        running_card_ids = {
            str(item.get("card_id") or "")
            for item in view.running
            if item.get("card_id")
        }
        filtered_ready = [
            item for item in view.ready_to_start
            if item.get("status") == "pending"
            and str(item.get("card_id") or "") not in running_card_ids
        ]

        todo_actionable = [item for item in view.todo if item.get("status") != "processing"]
        needs_manager_items = view.needs_manager
        completed_actionable = [item for item in view.completed if BackgroundWorkboardService._completed_item_is_actionable(item)]

        # Build fingerprint from normalized action units
        # 1. startable frontier: ready cards that are truly fresh
        ready_units: list[str] = []
        for item in filtered_ready:
            card_id = str(item.get("card_id") or "")
            if card_id:
                ready_units.append(f"ready:{card_id}")
            else:
                ready_units.append(f"ready:{item.get('item_id')}")

        # 2. manager attention: semantic keys, not item_ids.
        #    Exclude items whose coalescing_key has already been handled.
        manager_units: set[str] = set()
        for item in needs_manager_items:
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            if payload.get("coalescing_handled"):
                continue
            kind = str(item.get("kind") or "")
            card_id = str(item.get("card_id") or "")
            run_id = str(item.get("run_id") or "")
            if kind == "runtime_dependency_install_failed":
                ckey = payload.get("coalescing_key") or f"dep:{run_id or card_id or 'unknown'}"
                manager_units.add(f"manager:{ckey}")
            elif run_id:
                manager_units.add(f"manager:run:{run_id}:{kind}")
            elif card_id:
                manager_units.add(f"manager:{kind}:{card_id}")
            else:
                manager_units.add(f"manager:{kind}:{item.get('item_id')}")

        # 3. todo items do NOT enter fingerprint (they are intermediate state, not fresh work)
        # 4. completed items do NOT enter fingerprint (receipts, not wake fuel)
        fingerprint_items = sorted(ready_units) + sorted(manager_units)
        fingerprint = sha1(json.dumps(fingerprint_items, ensure_ascii=False).encode("utf-8")).hexdigest() if fingerprint_items else ""

        has_manager_actionable = bool(manager_units)
        has_startable_frontier = bool(ready_units)
        has_only_blocked_for_user = bool(counts.get("blocked_for_user", 0) > 0 and not has_manager_actionable and not has_startable_frontier)
        has_only_running = bool(counts.get("running", 0) > 0 and not has_manager_actionable and not has_startable_frontier and not has_only_blocked_for_user)
        has_only_housekeeping = bool(completed_actionable and not has_manager_actionable and not has_startable_frontier and not has_only_blocked_for_user and not has_only_running)

        running = int(counts.get("running", 0))
        blocked = int(counts.get("blocked_for_user", 0))
        return {
            "revision": view.revision,
            "counts": counts,
            "has_actionable": has_manager_actionable or has_startable_frontier,
            "has_running": running > 0,
            "has_blocked_for_user": blocked > 0,
            "actionability": {
                "has_manager_actionable": has_manager_actionable,
                "has_startable_frontier": has_startable_frontier,
                "has_only_blocked_for_user": has_only_blocked_for_user,
                "has_only_running": has_only_running,
                "has_only_housekeeping": has_only_housekeeping,
            },
            "fingerprint": f"sha1:{fingerprint}" if fingerprint else "",
            "fingerprint_items": fingerprint_items,
        }

    def _update_item_status(
        self,
        project_id: str,
        item_id: str,
        session_id: str,
        *,
        status: str,
        summary: str | None = None,
        message: str | None = None,
    ) -> WorkboardItemRecord:
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required.")
        with self._lock_for(project_id):
            state = self._load_state(project_id)
            item = state.items.get(item_id)
            if item is None:
                item_dict = self._find_view_item(project_id, item_id, session_id=session_id, state=state)
                if item_dict is None:
                    raise HTTPException(status_code=404, detail=f"Workboard item not found: {item_id}")
                item = WorkboardItemRecord.model_validate({**item_dict, "updated_at": utc_now()})
            if item.lane == "todo":
                if item.status not in {"claimed", "processing"} or item.claimed_by_session_id != session_id:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Workboard item {item_id} must be claimed by the current session before it can move to {status}.",
                    )
            updated = item.model_copy(
                update={
                    "status": status,
                    "summary": summary if summary is not None else item.summary,
                    "message": message,
                    "claimed_by_session_id": session_id if status in {"claimed", "processing"} else item.claimed_by_session_id,
                    "updated_at": utc_now(),
                }
            )
            if status in {"done", "deferred", "blocked_for_user"}:
                updated.claimed_by_session_id = None
                updated.claimed_at = None
                updated.claim_expires_at = None
                # Mark coalescing as handled when user/supervisor resolves the blocker
                payload = updated.payload if isinstance(updated.payload, dict) else {}
                coalescing_key = payload.get("coalescing_key")
                if coalescing_key and not payload.get("coalescing_handled"):
                    updated.payload = {**payload, "coalescing_handled": True}
                    state.handled_coalescing_keys[str(coalescing_key)] = utc_now()
                    # Batch-mark all other persisted items with the same coalescing_key
                    for other_id, other in list(state.items.items()):
                        other_payload = other.payload if isinstance(other.payload, dict) else {}
                        if other_payload.get("coalescing_key") == coalescing_key and not other_payload.get("coalescing_handled"):
                            state.items[other_id] = other.model_copy(
                                update={
                                    "payload": {**other_payload, "coalescing_handled": True},
                                    "updated_at": utc_now(),
                                }
                            )
            state.items[item_id] = updated
            self._save_state(project_id, state)
            return updated

    def _find_view_item(
        self,
        project_id: str,
        item_id: str,
        *,
        session_id: str | None,
        state: BackgroundWorkboardState,
    ) -> dict[str, Any] | None:
        view = self._build_view(project_id, self._merge_items(self._derived_items(project_id, state=state), state.items), session_id=session_id)
        return self._view_index(view).get(item_id)

    def _derived_items(self, project_id: str, state: BackgroundWorkboardState | None = None) -> dict[str, WorkboardItemRecord]:
        graph_store = self.project_service.graph_store(project_id)
        graph = graph_store.load_graph()
        cards = graph_store.load_cards()
        card_by_id = {card.card_id: card for card in cards}
        task_by_run: dict[str, str] = {}
        task_by_job: dict[str, str] = {}
        derived: dict[str, WorkboardItemRecord] = {}

        for task in self.background_task_service.list_tasks(project_id):
            if task.affected.run_ids:
                for run_id in task.affected.run_ids:
                    task_by_run[run_id] = task.task_id
            if task.affected.job_ids:
                for job_id in task.affected.job_ids:
                    task_by_job[job_id] = task.task_id
            if task.status in _ACTIVE_TASK_STATUSES:
                item_id = f"running_task:{task.task_id}"
                derived[item_id] = WorkboardItemRecord(
                    item_id=item_id,
                    lane="running",
                    kind=task.task_type,
                    task_id=task.task_id,
                    card_id=task.affected.card_ids[0] if task.affected.card_ids else None,
                    run_id=task.affected.run_ids[0] if task.affected.run_ids else None,
                    job_id=task.affected.job_ids[0] if task.affected.job_ids else None,
                    status="processing",
                    summary=task.result.get("message") if isinstance(task.result, dict) else None,
                    message=task.error,
                    payload={
                        "status": task.status,
                        "task_type": task.task_type,
                        "affected": task.affected.model_dump(),
                    },
                    updated_at=task.finished_at or task.started_at or task.created_at,
                )
            elif task.status == "interrupted":
                item_id = f"workboard_item:task:{task.task_id}:interrupted"
                derived[item_id] = WorkboardItemRecord(
                    item_id=item_id,
                    lane="needs_manager",
                    kind="interrupted_background_task",
                    task_id=task.task_id,
                    card_id=task.affected.card_ids[0] if task.affected.card_ids else None,
                    run_id=task.affected.run_ids[0] if task.affected.run_ids else None,
                    job_id=task.affected.job_ids[0] if task.affected.job_ids else None,
                    status="pending",
                    message=task.error or "Background task was interrupted.",
                    recommended_action="inspect_background_task",
                    updated_at=task.finished_at or task.created_at,
                )

        for run in graph.runs:
            task_id = task_by_run.get(run.run_id)
            card = card_by_id.get(run.card_id)
            if run.status == "reviewed":
                item_id = f"workboard_item:{run.run_id}:card_run_reviewed"
                derived[item_id] = WorkboardItemRecord(
                    item_id=item_id,
                    lane="completed",
                    kind="card_run_reviewed",
                    title=card.title if card is not None else run.title,
                    card_id=run.card_id,
                    run_id=run.run_id,
                    task_id=task_id,
                    status="pending",
                    summary=run.summary,
                    updated_at=run.finished_at or run.started_at,
                )
                continue
            if run.status == "failed" or run.needs_manager_attention or (card is not None and card.status == "needs_review"):
                issue = self._classify_run_issue(project_id, run.run_id)
                item_id = f"workboard_item:{run.run_id}:{issue['kind']}"
                derived[item_id] = WorkboardItemRecord(
                    item_id=item_id,
                    lane="needs_manager",
                    kind=issue["kind"],
                    title=card.title if card is not None else run.title,
                    card_id=run.card_id,
                    run_id=run.run_id,
                    task_id=task_id,
                    status="pending",
                    recommended_action=issue.get("recommended_action"),
                    summary=run.summary,
                    message=issue.get("message") or run.summary,
                    payload=issue.get("payload") or {},
                    updated_at=run.finished_at or run.started_at,
                )

        dep_jobs_path = self.project_service.project_path(project_id) / "chat" / "runtime_dependency_jobs.json"
        dep_jobs = read_json(dep_jobs_path, [])
        if isinstance(dep_jobs, list):
            for item in dep_jobs:
                if not isinstance(item, dict):
                    continue
                job_id = str(item.get("job_id") or "")
                if not job_id:
                    continue
                task_id = task_by_job.get(job_id)
                status = str(item.get("status") or "")
                result = item.get("result") if isinstance(item.get("result"), dict) else {}
                source = item.get("payload") if isinstance(item.get("payload"), dict) else {}
                source_meta = source.get("source") if isinstance(source.get("source"), dict) else {}
                if status == "succeeded":
                    derived[f"workboard_item:{job_id}:runtime_dependency_install_succeeded"] = WorkboardItemRecord(
                        item_id=f"workboard_item:{job_id}:runtime_dependency_install_succeeded",
                        lane="completed",
                        kind="runtime_dependency_install_succeeded",
                        card_id=str(source_meta.get("card_id") or "") or None,
                        run_id=str(source_meta.get("run_id") or "") or None,
                        job_id=job_id,
                        task_id=task_id,
                        status="pending",
                        summary=str(result.get("message") or "Dependency installation completed."),
                        payload={**result, "actionable_wake": False},
                        updated_at=str(item.get("finished_at") or item.get("created_at") or utc_now()),
                    )
                elif status == "failed":
                    coalescing_key = self._coalescing_key_for_dependency_item(result, source)
                    handled = (state is not None and coalescing_key in state.handled_coalescing_keys)
                    derived[f"workboard_item:{job_id}:runtime_dependency_install_failed"] = WorkboardItemRecord(
                        item_id=f"workboard_item:{job_id}:runtime_dependency_install_failed",
                        lane="needs_manager",
                        kind="runtime_dependency_install_failed",
                        card_id=str(source_meta.get("card_id") or "") or None,
                        run_id=str(source_meta.get("run_id") or "") or None,
                        job_id=job_id,
                        task_id=task_id,
                        status="pending",
                        recommended_action="inspect_runtime_dependency_install",
                        summary=str(result.get("message") or item.get("error") or "Dependency installation failed."),
                        payload={**result, "coalescing_key": coalescing_key, "coalescing_handled": handled},
                        updated_at=str(item.get("finished_at") or item.get("created_at") or utc_now()),
                    )
                elif status in ACTIVE_RUNTIME_DEPENDENCY_JOB_STATUSES:
                    derived[f"workboard_item:{job_id}:runtime_dependency_install_running"] = WorkboardItemRecord(
                        item_id=f"workboard_item:{job_id}:runtime_dependency_install_running",
                        lane="deferred",
                        kind="runtime_dependency_install_running",
                        card_id=str(source_meta.get("card_id") or "") or None,
                        run_id=str(source_meta.get("run_id") or "") or None,
                        job_id=job_id,
                        task_id=task_id,
                        status="pending",
                        summary="Waiting for runtime dependency installation to finish before starting the affected card.",
                        message=str(result.get("message") or item.get("error") or ""),
                        payload={
                            "status": status,
                            "runtime": source.get("runtime"),
                            "packages": source.get("packages"),
                            "session_id": source_meta.get("session_id"),
                        },
                        updated_at=str(item.get("started_at") or item.get("created_at") or utc_now()),
                    )

        runtime_dependency_blockers = dependency_blockers_by_card(self.project_service.project_path(project_id))
        work_order = self.flow_service.get_work_order(
            project_id,
            runtime_dependency_blockers=runtime_dependency_blockers,
        )
        for item in work_order["work_items"]:
            if not item.get("can_start"):
                continue
            card_id = str(item.get("card_id") or "")
            ready_id = f"ready_card:{card_id}"
            derived[ready_id] = WorkboardItemRecord(
                item_id=ready_id,
                lane="ready_to_start",
                kind="ready_card",
                title=str(item.get("title") or card_id),
                card_id=card_id,
                status="pending",
                payload={
                    "step": item.get("step"),
                    "parallel_group": self._parallel_group_for_card(work_order.get("parallel_batches"), card_id),
                    "safe_to_batch_start": True,
                    "block_reasons": item.get("block_reasons") or [],
                },
                updated_at=utc_now(),
            )
        return derived

    def _classify_run_issue(self, project_id: str, run_id: str) -> dict[str, Any]:
        run_dir = self.project_service.project_path(project_id) / "runs" / run_id
        failure_path = run_dir / "executor_failure.json"
        if failure_path.exists():
            try:
                failure = ExecutorFailureReport.model_validate(json.loads(failure_path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, ValueError):
                failure = None
            if failure is not None:
                mapping = {
                    "runtime_dependency_missing": ("runtime_dependency_missing", "install_runtime_dependencies"),
                    "input_missing": ("input_blocked", "ask_user_or_inspect_inputs"),
                    "input_invalid": ("input_blocked", "ask_user_or_inspect_inputs"),
                    "permission_denied": ("permission_blocked", "request_permission_or_reconfigure"),
                    "tool_unavailable": ("tool_unavailable", "configure_runtime_or_tool"),
                    "execution_error": ("execution_error", "inspect_run_failure"),
                    "contract_violation": ("contract_violation", "repair_executor_contract_or_rerun"),
                    "unknown": ("generic_run_failed", "inspect_run_failure"),
                }
                kind, action = mapping.get(failure.reason_code, ("generic_run_failed", "inspect_run_failure"))
                return {
                    "kind": kind,
                    "recommended_action": action,
                    "message": failure.summary,
                    "payload": failure.model_dump(exclude_none=True),
                }
        events = self.project_service.graph_store(project_id).load_run_events(run_id)
        for event in reversed(events):
            if event.event_type == "runtime_dependency_missing":
                payload = event.payload if isinstance(event.payload, dict) else {}
                return {
                    "kind": "runtime_dependency_missing",
                    "recommended_action": "install_runtime_dependencies",
                    "message": event.message,
                    "payload": payload,
                }
            if event.event_type == "executor_issue":
                return {
                    "kind": "executor_validation_failed",
                    "recommended_action": "inspect_run_failure",
                    "message": event.message,
                    "payload": event.payload if isinstance(event.payload, dict) else {},
                }
            if event.event_type == "reviewer_review_incomplete":
                return {
                    "kind": "needs_review",
                    "recommended_action": "review_card_run",
                    "message": event.message,
                    "payload": event.payload if isinstance(event.payload, dict) else {},
                }
        return {
            "kind": "generic_run_failed",
            "recommended_action": "inspect_run_failure",
            "message": None,
            "payload": {},
        }

    def _merge_items(self, derived: dict[str, WorkboardItemRecord], persisted: dict[str, WorkboardItemRecord]) -> dict[str, WorkboardItemRecord]:
        merged = {item_id: item.model_copy(deep=True) for item_id, item in derived.items()}
        active_todo_sources = {
            record.source_item_id
            for record in persisted.values()
            if record.lane == "todo" and record.status in {"pending", "claimed", "processing", "failed"} and record.source_item_id
        }
        for source_id in active_todo_sources:
            merged.pop(source_id, None)
        for item_id, record in persisted.items():
            if item_id in merged:
                base = merged[item_id]
                merged[item_id] = base.model_copy(
                    update={
                        "status": record.status,
                        "claimed_by_session_id": record.claimed_by_session_id,
                        "claimed_at": record.claimed_at,
                        "claim_expires_at": record.claim_expires_at,
                        "summary": record.summary or base.summary,
                        "message": record.message or base.message,
                        "updated_at": record.updated_at or base.updated_at,
                    }
                )
                if record.status == "blocked_for_user":
                    merged[item_id].lane = "blocked_for_user"
                elif record.status == "deferred":
                    merged[item_id].lane = "deferred"
                elif record.status == "done":
                    merged.pop(item_id, None)
            else:
                if record.status == "done":
                    continue
                if record.lane == "todo" or record.status in {"blocked_for_user", "deferred"}:
                    lane = record.lane
                    if record.status == "blocked_for_user":
                        lane = "blocked_for_user"
                    elif record.status == "deferred":
                        lane = "deferred"
                    merged[item_id] = record.model_copy(update={"lane": lane})
        return merged

    def _build_view(self, project_id: str, items: dict[str, WorkboardItemRecord], *, session_id: str | None) -> BackgroundWorkboardView:
        lanes: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in items.values():
            payload = item.model_dump()
            lanes[item.lane].append(payload)
        for lane_items in lanes.values():
            lane_items.sort(key=lambda item: (str(item.get("card_id") or ""), str(item.get("run_id") or ""), str(item.get("item_id") or "")))
        counts = {lane: len(lanes.get(lane, [])) for lane in ["running", "todo", "needs_manager", "completed", "ready_to_start", "blocked_for_user", "deferred"]}
        revision = self._revision_for_items(project_id, items)
        return BackgroundWorkboardView(
            project_id=project_id,
            revision=revision,
            counts=counts,
            running=lanes.get("running", []),
            todo=lanes.get("todo", []),
            needs_manager=lanes.get("needs_manager", []),
            completed=lanes.get("completed", []),
            ready_to_start=lanes.get("ready_to_start", []),
            blocked_for_user=lanes.get("blocked_for_user", []),
            deferred=lanes.get("deferred", []),
        )

    @staticmethod
    def _revision_for_items(project_id: str, items: dict[str, WorkboardItemRecord]) -> int:
        relevant = [
            {
                "item_id": item.item_id,
                "lane": item.lane,
                "status": item.status,
                "task_id": item.task_id,
                "card_id": item.card_id,
                "run_id": item.run_id,
                "job_id": item.job_id,
                "source_item_id": item.source_item_id,
            }
            for item in sorted(items.values(), key=lambda value: value.item_id)
        ]
        serialized = json.dumps({"project_id": project_id, "items": relevant}, sort_keys=True, separators=(",", ":"))
        digest = sha1(serialized.encode("utf-8")).hexdigest()[:12]
        return int(digest, 16)

    @staticmethod
    def _coalescing_key_for_dependency_item(result: dict[str, Any], source: dict[str, Any]) -> str:
        runtime = str(source.get("runtime") or result.get("runtime") or "unknown")
        ecosystem = str(source.get("ecosystem") or result.get("ecosystem") or "unknown")
        packages = source.get("packages")
        if isinstance(packages, list):
            pkg_str = ",".join(sorted(str(p) for p in packages))
        else:
            pkg_str = str(packages or "")
        error_code = str(result.get("error_code") or result.get("reason_code") or "unknown")
        requested_package = str(result.get("requested_package") or "")
        return f"dep:{ecosystem}:{runtime}:{pkg_str}:{error_code}:{requested_package}"

    @staticmethod
    def _parallel_group_for_card(parallel_batches: Any, card_id: str) -> str | None:
        if not isinstance(parallel_batches, list):
            return None
        for batch in parallel_batches:
            if not isinstance(batch, dict):
                continue
            card_ids = batch.get("card_ids")
            if isinstance(card_ids, list) and card_id in card_ids:
                return str(batch.get("batch_id") or batch.get("step") or "")
        return None

    @staticmethod
    def _view_index(view: BackgroundWorkboardView) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for lane_name in ["running", "todo", "needs_manager", "completed", "ready_to_start", "blocked_for_user", "deferred"]:
            for item in getattr(view, lane_name):
                if isinstance(item, dict):
                    index[str(item.get("item_id") or "")] = item
        return index

    @staticmethod
    def _completed_item_is_actionable(item: dict[str, Any]) -> bool:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        return bool(payload.get("actionable_wake"))

    def _path(self, project_id: str) -> Path:
        return self.project_service.project_path(project_id) / "chat" / "background_workboard_state.json"

    def _load_state(self, project_id: str) -> BackgroundWorkboardState:
        payload = read_json(self._path(project_id), {})
        if not isinstance(payload, dict):
            return BackgroundWorkboardState()
        try:
            return BackgroundWorkboardState.model_validate(payload)
        except Exception:
            return BackgroundWorkboardState()

    def _save_state(self, project_id: str, state: BackgroundWorkboardState) -> None:
        atomic_write_json(self._path(project_id), state.model_dump())

    @staticmethod
    def _release_expired_claims(state: BackgroundWorkboardState) -> bool:
        now = _utc_now_dt()
        changed = False
        for item_id, item in list(state.items.items()):
            if item.status not in {"claimed", "processing"} or not item.claim_expires_at:
                continue
            expires_at = _parse_utc(item.claim_expires_at)
            if expires_at is None or expires_at > now:
                continue
            state.items[item_id] = item.model_copy(
                update={
                    "status": "pending",
                    "claimed_by_session_id": None,
                    "claimed_at": None,
                    "claim_expires_at": None,
                    "updated_at": utc_now(),
                }
            )
            changed = True
        return changed

    def _lock_for(self, project_id: str) -> RLock:
        with self._locks_guard:
            lock = self._locks.get(project_id)
            if lock is None:
                lock = RLock()
                self._locks[project_id] = lock
            return lock


def _utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _utc_after_seconds(seconds: int) -> str:
    return (_utc_now_dt() + timedelta(seconds=max(1, seconds))).isoformat().replace("+00:00", "Z")
