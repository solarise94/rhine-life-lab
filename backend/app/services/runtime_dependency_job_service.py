from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
import logging
from threading import Lock
import traceback
from typing import Any
from uuid import uuid4

from app.models.manager_auto import ManagerWakeEvent, ManagerWakeSource
from app.services.manager_wake_service import ManagerWakeService
from app.services.project_event_service import ProjectEventService
from app.services.project_service import ProjectService
from app.services.utils import atomic_write_json, read_json, utc_now


JobStatus = str
logger = logging.getLogger(__name__)


@dataclass
class RuntimeDependencyJob:
    job_id: str
    project_id: str
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
        manager_wake_service: ManagerWakeService | None = None,
        project_event_service: ProjectEventService | None = None,
    ) -> None:
        self.project_service = project_service
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="runtime-deps")
        self.jobs: dict[str, RuntimeDependencyJob] = {}
        self.lock = Lock()
        self.runtime_locks: dict[tuple[str, str], Lock] = {}
        self.manager_wake_service = manager_wake_service
        self.project_event_service = project_event_service

    def submit(self, project_id: str, payload: dict[str, Any], handler) -> RuntimeDependencyJob:
        job = RuntimeDependencyJob(
            job_id=f"depjob_{uuid4().hex}",
            project_id=project_id,
            payload=payload,
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
                self._persist_project_jobs_locked(job.project_id)
                self._emit_project_event(job)
                self._emit_wake_event(job, ok=False, message=job.error or "Dependency installation failed.")
            return
        with self.lock:
            job.status = "succeeded" if result.get("ok") else "failed"
            job.result = result
            job.error = None if result.get("ok") else str(result.get("message") or "Dependency installation failed.")
            job.finished_at = utc_now()
            self._persist_project_jobs_locked(job.project_id)
            self._emit_project_event(job)
            self._emit_wake_event(job, ok=bool(result.get("ok")), message=job.error or str(result.get("message") or "Dependency installation completed."))

    def _emit_project_event(self, job: RuntimeDependencyJob) -> None:
        if self.project_event_service is None:
            return
        source_payload = job.payload.get("source") if isinstance(job.payload, dict) else {}
        source = source_payload if isinstance(source_payload, dict) else {}
        try:
            self.project_event_service.emit(
                job.project_id,
                reason="runtime_dependency_job_changed",
                card_id=source.get("card_id"),
                run_id=source.get("run_id"),
                job_id=job.job_id,
                status=job.status,
                payload={
                    "job_status": job.status,
                    "runtime": job.payload.get("runtime"),
                    "packages": job.payload.get("packages"),
                    "manager": job.payload.get("manager"),
                    "started_at": job.started_at,
                    "finished_at": job.finished_at,
                    "ok": bool(job.result.get("ok")) if isinstance(job.result, dict) else None,
                },
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
                    result=dict(item["result"]) if isinstance(item.get("result"), dict) else None,
                    error=str(item["error"]) if item.get("error") is not None else None,
                    created_at=str(item.get("created_at") or now),
                    started_at=str(item["started_at"]) if item.get("started_at") is not None else None,
                    finished_at=str(item["finished_at"]) if item.get("finished_at") is not None else None,
                )
            except (KeyError, TypeError, ValueError):
                continue
            if job.status in {"queued", "running"}:
                job.status = "failed"
                job.error = "Runtime dependency job was interrupted by backend restart."
                job.finished_at = job.finished_at or now
                changed = True
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

    def _emit_wake_event(self, job: RuntimeDependencyJob, *, ok: bool, message: str) -> None:
        if self.manager_wake_service is None:
            return
        source_payload = job.payload.get("source") if isinstance(job.payload, dict) else {}
        source = source_payload if isinstance(source_payload, dict) else {}
        kind = "runtime_dependency_install_succeeded" if ok else "runtime_dependency_install_failed"
        event = ManagerWakeEvent(
            wake_id=f"wake_{uuid4().hex[:12]}",
            project_id=job.project_id,
            kind=kind,
            source_type="dependency_job",
            source_id=job.job_id,
            card_id=source.get("card_id"),
            run_id=source.get("run_id"),
            job_id=job.job_id,
            severity="info" if ok else "warning",
            message=message,
            payload_summary={
                "runtime": job.payload.get("runtime") if isinstance(job.payload, dict) else None,
                "packages": job.payload.get("packages") if isinstance(job.payload, dict) else None,
                "job_status": job.status,
            },
            source=ManagerWakeSource(
                card_id=source.get("card_id"),
                run_id=source.get("run_id"),
                wake_id=source.get("wake_id"),
                reason=source.get("reason"),
                job_id=job.job_id,
            ),
            idempotency_key=f"depjob:{job.job_id}:{'succeeded' if ok else 'failed'}",
            created_at=utc_now(),
        )
        self.manager_wake_service.enqueue(event)
