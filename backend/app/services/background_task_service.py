from __future__ import annotations

from pathlib import Path
from threading import Lock
from uuid import uuid4

from app.models.background import BackgroundTaskAdapter, BackgroundTaskAffected, BackgroundTaskRecord
from app.services.project_service import ProjectService
from app.services.utils import atomic_write_json, read_json, utc_now


class BackgroundTaskService:
    def __init__(self, project_service: ProjectService) -> None:
        self.project_service = project_service
        self._locks: dict[str, Lock] = {}
        self._locks_guard = Lock()

    def create_task(
        self,
        project_id: str,
        *,
        task_type: str,
        affected: dict | BackgroundTaskAffected | None = None,
        adapter: dict | BackgroundTaskAdapter | None = None,
        task_id: str | None = None,
        status: str = "queued",
    ) -> BackgroundTaskRecord:
        record = BackgroundTaskRecord(
            task_id=task_id or f"bgtask_{uuid4().hex}",
            task_type=task_type,
            project_id=project_id,
            status=status,
            created_at=utc_now(),
            affected=affected if isinstance(affected, BackgroundTaskAffected) else BackgroundTaskAffected.model_validate(affected or {}),
            adapter=adapter if isinstance(adapter, BackgroundTaskAdapter) else BackgroundTaskAdapter.model_validate(adapter or {"kind": "unknown"}),
        )
        lock = self._lock_for(project_id)
        with lock:
            tasks = self._load(project_id)
            tasks = [item for item in tasks if item.task_id != record.task_id]
            tasks.append(record)
            self._save(project_id, tasks)
        return record

    def update_task(self, project_id: str, task_id: str, **updates: object) -> BackgroundTaskRecord | None:
        lock = self._lock_for(project_id)
        with lock:
            tasks = self._load(project_id)
            updated: BackgroundTaskRecord | None = None
            for index, task in enumerate(tasks):
                if task.task_id != task_id:
                    continue
                payload = dict(updates)
                if "result" in payload and not isinstance(payload["result"], dict):
                    payload["result"] = {}
                if "affected" in payload and not isinstance(payload["affected"], BackgroundTaskAffected):
                    payload["affected"] = BackgroundTaskAffected.model_validate(payload["affected"] or {})
                if "adapter" in payload and not isinstance(payload["adapter"], BackgroundTaskAdapter):
                    payload["adapter"] = BackgroundTaskAdapter.model_validate(payload["adapter"] or {"kind": "unknown"})
                updated = task.model_copy(update=payload)
                tasks[index] = updated
                break
            if updated is None:
                return None
            self._save(project_id, tasks)
            return updated

    def get_task(self, project_id: str, task_id: str) -> BackgroundTaskRecord | None:
        for task in self.list_tasks(project_id):
            if task.task_id == task_id:
                return task
        return None

    def list_tasks(self, project_id: str) -> list[BackgroundTaskRecord]:
        lock = self._lock_for(project_id)
        with lock:
            return self._load(project_id)

    def find_by_run_id(self, project_id: str, run_id: str) -> BackgroundTaskRecord | None:
        for task in self.list_tasks(project_id):
            if run_id in task.affected.run_ids:
                return task
        return None

    def find_by_job_id(self, project_id: str, job_id: str) -> BackgroundTaskRecord | None:
        for task in self.list_tasks(project_id):
            if job_id in task.affected.job_ids:
                return task
        return None

    def _path(self, project_id: str) -> Path:
        return self.project_service.project_path(project_id) / "chat" / "background_tasks.json"

    def _load(self, project_id: str) -> list[BackgroundTaskRecord]:
        items = read_json(self._path(project_id), [])
        if not isinstance(items, list):
            return []
        tasks: list[BackgroundTaskRecord] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                tasks.append(BackgroundTaskRecord.model_validate(item))
            except Exception:
                continue
        tasks.sort(key=lambda item: item.created_at)
        return tasks

    def _save(self, project_id: str, tasks: list[BackgroundTaskRecord]) -> None:
        atomic_write_json(self._path(project_id), [task.model_dump() for task in tasks])

    def _lock_for(self, project_id: str) -> Lock:
        with self._locks_guard:
            lock = self._locks.get(project_id)
            if lock is None:
                lock = Lock()
                self._locks[project_id] = lock
            return lock
