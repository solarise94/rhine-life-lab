from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Lock
from uuid import uuid4

from app.models.chat import ChatRequest, ChatResponse


ChatJobStatus = str


@dataclass
class ChatJob:
    job_id: str
    project_id: str
    request: ChatRequest
    status: ChatJobStatus = "queued"
    response: ChatResponse | None = None
    error: str | None = None
    future: Future | None = field(default=None, repr=False)


class ChatJobService:
    def __init__(self, max_workers: int = 4) -> None:
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="manager-chat")
        self.jobs: dict[str, ChatJob] = {}
        self.lock = Lock()

    def submit(self, project_id: str, request: ChatRequest, handler) -> ChatJob:
        job = ChatJob(job_id=f"chat_{uuid4().hex}", project_id=project_id, request=request)
        with self.lock:
            self.jobs[job.job_id] = job
        future = self.executor.submit(self._run, job.job_id, handler)
        with self.lock:
            job.future = future
        return job

    def get(self, job_id: str) -> ChatJob | None:
        with self.lock:
            return self.jobs.get(job_id)

    def _run(self, job_id: str, handler) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job.status = "running"
        try:
            response = handler(job.project_id, job.request)
        except Exception as exc:
            with self.lock:
                job.status = "failed"
                job.error = str(exc)
            return
        with self.lock:
            job.status = "succeeded"
            job.response = response
