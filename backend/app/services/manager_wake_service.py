from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from datetime import datetime

from app.models.manager_auto import ManagerWakeEvent
from app.services.project_service import ProjectService
from app.services.utils import utc_now


class ManagerWakeService:
    def __init__(self, project_service: ProjectService) -> None:
        self.project_service = project_service
        self._locks: dict[str, Lock] = {}

    def enqueue(self, event: ManagerWakeEvent) -> ManagerWakeEvent:
        lock = self._lock_for(event.project_id)
        with lock:
            events = self._load(event.project_id)
            if any(item.idempotency_key == event.idempotency_key for item in events):
                return next(item for item in events if item.idempotency_key == event.idempotency_key)
            events.append(event)
            self._save(event.project_id, events)
            return event

    def list_recent(self, project_id: str) -> list[ManagerWakeEvent]:
        return self._load(project_id)

    def claim_next(self, project_id: str, *, processor_id: str, stale_after_seconds: int = 120) -> ManagerWakeEvent | None:
        lock = self._lock_for(project_id)
        with lock:
            events = self._load(project_id)
            now_ts = _parse_utc_timestamp(utc_now())
            for index, event in enumerate(events):
                if event.status == "queued":
                    events[index] = event.model_copy(
                        update={
                            "status": "running",
                            "claimed_at": utc_now(),
                            "processor_id": processor_id,
                            "attempts": event.attempts + 1,
                        }
                    )
                    self._save(project_id, events)
                    return events[index]
                if event.status == "running" and event.claimed_at:
                    claimed_ts = _parse_utc_timestamp(event.claimed_at)
                    if now_ts - claimed_ts >= stale_after_seconds:
                        events[index] = event.model_copy(
                            update={
                                "status": "running",
                                "claimed_at": utc_now(),
                                "processor_id": processor_id,
                                "attempts": event.attempts + 1,
                            }
                        )
                        self._save(project_id, events)
                        return events[index]
            return None

    def mark_done(self, project_id: str, wake_id: str) -> None:
        self._update(project_id, wake_id, status="done", processed_at=utc_now(), error=None)

    def mark_failed(self, project_id: str, wake_id: str, error: str) -> None:
        self._update(project_id, wake_id, status="failed", processed_at=utc_now(), error=error)

    def mark_skipped(self, project_id: str, wake_id: str, reason: str) -> None:
        self._update(project_id, wake_id, status="skipped", processed_at=utc_now(), error=reason)

    def _update(self, project_id: str, wake_id: str, **updates: object) -> None:
        lock = self._lock_for(project_id)
        with lock:
            events = self._load(project_id)
            for index, event in enumerate(events):
                if event.wake_id == wake_id:
                    events[index] = event.model_copy(update=updates)
                    break
            self._save(project_id, events)

    def _path(self, project_id: str) -> Path:
        return self.project_service.project_path(project_id) / "chat" / "manager_wake_events.jsonl"

    def _load(self, project_id: str) -> list[ManagerWakeEvent]:
        path = self._path(project_id)
        if not path.exists():
            return []
        items: list[ManagerWakeEvent] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                items.append(ManagerWakeEvent.model_validate(json.loads(line)))
        return items

    def _save(self, project_id: str, events: list[ManagerWakeEvent]) -> None:
        path = self._path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event.model_dump(), ensure_ascii=False))
                handle.write("\n")
        os.replace(tmp_path, path)

    def _lock_for(self, project_id: str) -> Lock:
        if project_id not in self._locks:
            self._locks[project_id] = Lock()
        return self._locks[project_id]


def _parse_utc_timestamp(value: str) -> int:
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0
