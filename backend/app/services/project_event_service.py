from __future__ import annotations

import logging
from collections.abc import Iterator
from queue import Empty, Full, Queue
import threading
from typing import Any

from app.services.project_service import ProjectService
from app.services.utils import utc_now


logger = logging.getLogger(__name__)


class ProjectEventService:
    def __init__(self, project_service: ProjectService) -> None:
        self.project_service = project_service
        self._subscriber_lock = threading.Lock()
        self._subscribers: dict[str, list[Queue[dict[str, Any]]]] = {}

    def subscribe_events(self, project_id: str) -> Iterator[dict[str, Any]]:
        queue: Queue[dict[str, Any]] = Queue(maxsize=512)
        with self._subscriber_lock:
            self._subscribers.setdefault(project_id, []).append(queue)
        yield self._baseline_event(project_id)
        try:
            while True:
                try:
                    yield queue.get(timeout=15)
                except Empty:
                    yield {"type": "heartbeat", "created_at": utc_now()}
        finally:
            with self._subscriber_lock:
                subscribers = self._subscribers.get(project_id, [])
                if queue in subscribers:
                    subscribers.remove(queue)
                if not subscribers:
                    self._subscribers.pop(project_id, None)

    def emit(
        self,
        project_id: str,
        *,
        reason: str,
        reasons: list[str] | None = None,
        card_id: str | None = None,
        run_id: str | None = None,
        job_id: str | None = None,
        status: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # `reasons` is reserved for future coalesced UI/cache events. Current
        # callers usually emit one committed mutation at a time.
        event_payload = payload or {}
        run_status = self._event_status(event_payload, "run_status", fallback=status)
        card_status = self._event_status(event_payload, "card_status")
        job_status = self._event_status(event_payload, "job_status")
        revision = self._increment_revision(project_id)
        event = {
            "type": "project_state_changed",
            "project_id": project_id,
            "graph_revision": revision,
            "seq": revision,
            "reason": reason,
            "reasons": reasons or [reason],
            "card_id": card_id,
            "run_id": run_id,
            "job_id": job_id,
            "status": status,
            "run_status": run_status,
            "card_status": card_status,
            "job_status": job_status,
            "payload": event_payload,
            "created_at": utc_now(),
        }
        self._publish(project_id, event)
        return event

    @staticmethod
    def _event_status(payload: dict[str, Any], key: str, *, fallback: str | None = None) -> str | None:
        value = payload.get(key)
        return value if isinstance(value, str) else fallback

    def _baseline_event(self, project_id: str) -> dict[str, Any]:
        revision = self._current_revision(project_id)
        return {
            "type": "project_state_baseline",
            "project_id": project_id,
            "graph_revision": revision,
            "seq": revision,
            "requires_refetch": True,
            "created_at": utc_now(),
        }

    def _current_revision(self, project_id: str) -> int:
        store = self.project_service.graph_store(project_id)
        metadata = store.load_metadata()
        return int(metadata.get("project_event_revision") or 0)

    def _increment_revision(self, project_id: str) -> int:
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            metadata = store.load_metadata()
            revision = int(metadata.get("project_event_revision") or 0) + 1
            metadata["project_event_revision"] = revision
            store.save_metadata(metadata)
            return revision

    def _publish(self, project_id: str, event: dict[str, Any]) -> None:
        with self._subscriber_lock:
            subscribers = list(self._subscribers.get(project_id, []))
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except Full:
                logger.warning(
                    "Dropping project event because subscriber queue is full: project_id=%s event_type=%s reason=%s",
                    project_id,
                    event.get("type"),
                    event.get("reason"),
                )
