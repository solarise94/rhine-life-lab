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
from app.services.utils import utc_now


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
    def __init__(self, max_workers: int = 2, manager_wake_service: ManagerWakeService | None = None) -> None:
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="runtime-deps")
        self.jobs: dict[str, RuntimeDependencyJob] = {}
        self.lock = Lock()
        self.manager_wake_service = manager_wake_service

    def submit(self, project_id: str, payload: dict[str, Any], handler) -> RuntimeDependencyJob:
        job = RuntimeDependencyJob(
            job_id=f"depjob_{uuid4().hex}",
            project_id=project_id,
            payload=payload,
        )
        with self.lock:
            self.jobs[job.job_id] = job
        future = self.executor.submit(self._run, job.job_id, handler)
        with self.lock:
            job.future = future
        return job

    def get(self, job_id: str) -> RuntimeDependencyJob | None:
        with self.lock:
            return self.jobs.get(job_id)

    def _run(self, job_id: str, handler) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job.status = "running"
            job.started_at = utc_now()
        try:
            result = handler(job.project_id, job.payload)
        except Exception as exc:
            logger.exception("Runtime dependency job failed: %s", job_id)
            with self.lock:
                job.status = "failed"
                job.error = "".join(traceback.format_exception(exc))
                job.finished_at = utc_now()
                self._emit_wake_event(job, ok=False, message=job.error or "Dependency installation failed.")
            return
        with self.lock:
            job.status = "succeeded" if result.get("ok") else "failed"
            job.result = result
            job.error = None if result.get("ok") else str(result.get("message") or "Dependency installation failed.")
            job.finished_at = utc_now()
            self._emit_wake_event(job, ok=bool(result.get("ok")), message=job.error or str(result.get("message") or "Dependency installation completed."))

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
