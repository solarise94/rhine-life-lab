from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
import logging
from threading import Lock
import traceback
from typing import Any, Callable
from uuid import uuid4

from fastapi import HTTPException

from app.services.background_task_service import BackgroundTaskService
from app.services.project_event_service import ProjectEventService
from app.services.project_service import ProjectService
from app.services.runtime_dependency_state_service import runtime_dependency_failure_details
from app.services.utils import atomic_write_json, read_json, utc_now


JobStatus = str
logger = logging.getLogger(__name__)


@dataclass
class RuntimeDependencyJob:
    job_id: str
    project_id: str
    task_id: str
    payload: dict[str, Any]
    status: JobStatus = "queued"
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    future: Future | None = field(default=None, repr=False)


class RuntimeDependencyJobService:
    def __init__(
        self,
        project_service: ProjectService,
        max_workers: int = 2,
        project_event_service: ProjectEventService | None = None,
        background_task_service: BackgroundTaskService | None = None,
        background_terminal_callback: Callable[[str, str | None, str | None], None] | None = None,
    ) -> None:
        self.project_service = project_service
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="runtime-deps")
        self.jobs: dict[str, RuntimeDependencyJob] = {}
        self.lock = Lock()
        self.runtime_locks: dict[tuple[str, str], Lock] = {}
        self.project_event_service = project_event_service
        self.background_task_service = background_task_service or BackgroundTaskService(project_service)
        self.background_terminal_callback = background_terminal_callback

    def submit(self, project_id: str, payload: dict[str, Any], handler) -> RuntimeDependencyJob:
        task = self.background_task_service.create_task(
            project_id,
            task_type="runtime_dependency_install",
            affected={"job_ids": [], "card_ids": [payload.get("source", {}).get("card_id")] if isinstance(payload.get("source"), dict) and payload.get("source", {}).get("card_id") else []},
            adapter={"kind": "dependency_installer"},
        )
        job = RuntimeDependencyJob(
            job_id=f"depjob_{uuid4().hex}",
            project_id=project_id,
            task_id=task.task_id,
            payload=payload,
        )
        self.background_task_service.update_task(
            project_id,
            task.task_id,
            affected={"job_ids": [job.job_id], "card_ids": task.affected.card_ids},
        )
        with self.lock:
            self._load_project_jobs_locked(project_id)
            self.jobs[job.job_id] = job
            self._persist_project_jobs_locked(project_id)
        self._emit_project_event(job)
        future = self.executor.submit(self._run, job.job_id, handler)
        with self.lock:
            job.future = future
        return job

    def get(self, job_id: str) -> RuntimeDependencyJob | None:
        with self.lock:
            job = self.jobs.get(job_id)
        if job is not None:
            return job
        for project in self.project_service.list_projects():
            with self.lock:
                self._load_project_jobs_locked(project.project_id)
                job = self.jobs.get(job_id)
                if job is not None:
                    return job
        return None

    def get_for_project(self, project_id: str, job_id: str) -> RuntimeDependencyJob | None:
        with self.lock:
            self._load_project_jobs_locked(project_id)
            job = self.jobs.get(job_id)
            if job is None or job.project_id != project_id:
                return None
            return job

    def _run(self, job_id: str, handler) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job.status = "running"
            job.started_at = utc_now()
            self.background_task_service.update_task(job.project_id, job.task_id, status="running", started_at=job.started_at)
            self._persist_project_jobs_locked(job.project_id)
            self._emit_project_event(job)
            runtime = str(job.payload.get("runtime") or "").strip()
            runtime_key = (job.project_id, runtime)
            runtime_lock = self.runtime_locks.setdefault(runtime_key, Lock())
        try:
            with runtime_lock:
                result = handler(job.project_id, job.payload)
        except Exception as exc:
            logger.exception("Runtime dependency job failed: %s", job_id)
            with self.lock:
                job.status = "failed"
                job.error = "".join(traceback.format_exception(exc))
                job.finished_at = utc_now()
                self.background_task_service.update_task(
                    job.project_id,
                    job.task_id,
                    status="failed",
                    finished_at=job.finished_at,
                    error=job.error,
                )
                self._persist_project_jobs_locked(job.project_id)
                self._emit_project_event(job)
            self._notify_background_terminal(job.project_id, job_id=job.job_id)
            return
        with self.lock:
            job.status = "succeeded" if result.get("ok") else "failed"
            job.result = result
            job.error = None if result.get("ok") else str(result.get("message") or "Dependency installation failed.")
            job.finished_at = utc_now()
            self.background_task_service.update_task(
                job.project_id,
                job.task_id,
                status=job.status,
                finished_at=job.finished_at,
                result=result,
                error=job.error,
            )
            self._persist_project_jobs_locked(job.project_id)
            self._emit_project_event(job)
        self._notify_background_terminal(job.project_id, job_id=job.job_id)

    def _emit_project_event(self, job: RuntimeDependencyJob) -> None:
        if self.project_event_service is None:
            return
        source_payload = job.payload.get("source") if isinstance(job.payload, dict) else {}
        source = source_payload if isinstance(source_payload, dict) else {}
        payload: dict[str, Any] = {
            "task_id": job.task_id,
            "job_status": job.status,
            "runtime": job.payload.get("runtime"),
            "packages": job.payload.get("packages"),
            "manager": job.payload.get("manager"),
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "ok": bool(job.result.get("ok")) if isinstance(job.result, dict) else None,
        }
        # Enrich terminal events with normalized failure details
        if job.status in {"failed", "succeeded"}:
            try:
                details = runtime_dependency_failure_details(job)
                # Merge key failure fields into the event payload
                for key in (
                    "error_code",
                    "message",
                    "requested_package",
                    "attempted_candidates",
                    "fallback_available",
                    "retry_hint",
                    "dedupe_key",
                    "stdout_tail",
                    "stderr_tail",
                    "truncated",
                    "ecosystem",
                    "resolved_runtime",
                    "card_id",
                    "run_id",
                    "session_id",
                ):
                    if key in details:
                        payload[key] = details[key]
            except Exception:
                logger.exception(
                    "Failed to enrich runtime dependency event: project_id=%s job_id=%s",
                    job.project_id,
                    job.job_id,
                )
        try:
            self.project_event_service.emit(
                job.project_id,
                reason="runtime_dependency_job_changed",
                card_id=source.get("card_id"),
                run_id=source.get("run_id"),
                job_id=job.job_id,
                task_id=job.task_id,
                status=job.status,
                payload=payload,
            )
        except Exception:
            logger.exception("Failed to emit runtime dependency project event: project_id=%s job_id=%s", job.project_id, job.job_id)

    def _jobs_path(self, project_id: str):
        return self.project_service.project_path(project_id) / "chat" / "runtime_dependency_jobs.json"

    def _load_project_jobs_locked(self, project_id: str) -> None:
        now = utc_now()
        items = read_json(self._jobs_path(project_id), [])
        if not isinstance(items, list):
            return
        changed = False
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                job_id = str(item["job_id"])
                existing = self.jobs.get(job_id)
                if existing is not None and existing.project_id == project_id:
                    continue
                job = RuntimeDependencyJob(
                    job_id=job_id,
                    project_id=str(item.get("project_id") or project_id),
                    payload=dict(item.get("payload") or {}),
                    status=str(item.get("status") or "failed"),
                    task_id=str(item.get("task_id") or f"bgtask_{job_id}"),
                    result=dict(item["result"]) if isinstance(item.get("result"), dict) else None,
                    error=str(item["error"]) if item.get("error") is not None else None,
                    created_at=str(item.get("created_at") or now),
                    started_at=str(item["started_at"]) if item.get("started_at") is not None else None,
                    finished_at=str(item["finished_at"]) if item.get("finished_at") is not None else None,
                )
            except (KeyError, TypeError, ValueError):
                continue
            if job.status in {"queued", "running", "waiting", "launching"}:
                job.status = "failed"
                job.error = "Runtime dependency job was interrupted by backend restart."
                job.finished_at = job.finished_at or now
                changed = True
                self.background_task_service.update_task(
                    job.project_id,
                    job.task_id,
                    status="interrupted",
                    finished_at=job.finished_at,
                    error=job.error,
                )
            self.jobs[job.job_id] = job
        if changed:
            self._persist_project_jobs_locked(project_id)

    def _persist_project_jobs_locked(self, project_id: str) -> None:
        project_jobs = [
            job
            for job in self.jobs.values()
            if job.project_id == project_id
        ]
        project_jobs.sort(key=lambda item: item.created_at)
        atomic_write_json(
            self._jobs_path(project_id),
            [
                {
                    "job_id": job.job_id,
                    "project_id": job.project_id,
                    "task_id": job.task_id,
                    "payload": job.payload,
                    "status": job.status,
                    "result": job.result,
                    "error": job.error,
                    "created_at": job.created_at,
                    "started_at": job.started_at,
                    "finished_at": job.finished_at,
                }
                for job in project_jobs
            ],
        )

    def _notify_background_terminal(self, project_id: str, *, job_id: str | None = None) -> None:
        if self.background_terminal_callback is None:
            return
        try:
            self.background_terminal_callback(project_id, None, job_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                logger.debug(
                    "Skipping background terminal notification for missing project=%s job=%s",
                    project_id,
                    job_id,
                )
                return
            logger.exception(
                "Failed to notify background terminal state for project=%s job=%s",
                project_id,
                job_id,
            )
        except Exception:
            logger.exception(
                "Failed to notify background terminal state for project=%s job=%s",
                project_id,
                job_id,
            )

    def mark_job_resolved(
        self,
        project_id: str,
        job_id: str,
        session_id: str,
        resolution_message: str,
    ) -> dict[str, Any]:
        """Mark a runtime dependency job as manually resolved.

        Adds resolution metadata to the persisted job record and emits a project event.
        Does not create a new job; updates the existing one in-place.
        """
        with self.lock:
            self._load_project_jobs_locked(project_id)
            job = self.jobs.get(job_id)
            if job is None or job.project_id != project_id:
                raise HTTPException(status_code=404, detail="Runtime dependency job not found")
            now = utc_now()
            job.payload["resolution_status"] = "manually_resolved"
            job.payload["resolved_at"] = now
            job.payload["resolved_by_session_id"] = session_id
            job.payload["resolution_message"] = resolution_message
            self._persist_project_jobs_locked(project_id)
            self._emit_project_event(job)
        return {
            "job_id": job.job_id,
            "status": job.status,
            "resolution_status": "manually_resolved",
            "resolved_at": now,
            "resolved_by_session_id": session_id,
            "resolution_message": resolution_message,
        }
