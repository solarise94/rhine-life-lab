from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
import logging
from threading import Lock
import traceback
from typing import Any
from uuid import uuid4

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
    def __init__(self, max_workers: int = 2) -> None:
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="runtime-deps")
        self.jobs: dict[str, RuntimeDependencyJob] = {}
        self.lock = Lock()

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
            return
        with self.lock:
            job.status = "succeeded" if result.get("ok") else "failed"
            job.result = result
            job.error = None if result.get("ok") else str(result.get("message") or "Dependency installation failed.")
            job.finished_at = utc_now()
