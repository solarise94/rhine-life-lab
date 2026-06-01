from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.utils import read_json


ACTIVE_RUNTIME_DEPENDENCY_JOB_STATUSES = {"queued", "launching", "running", "waiting"}
BLOCKING_RUNTIME_DEPENDENCY_JOB_STATUSES = ACTIVE_RUNTIME_DEPENDENCY_JOB_STATUSES | {"failed"}


def load_runtime_dependency_jobs(project_root: Path) -> list[dict[str, Any]]:
    items = read_json(project_root / "chat" / "runtime_dependency_jobs.json", [])
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def dependency_blockers_by_card(project_root: Path) -> dict[str, dict[str, Any]]:
    latest_by_card: dict[str, tuple[tuple[str, str], dict[str, Any]]] = {}
    for item in load_runtime_dependency_jobs(project_root):
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        card_id = str(source.get("card_id") or "").strip()
        if not card_id:
            continue
        status = str(item.get("status") or "").strip()
        created_at = str(item.get("created_at") or item.get("started_at") or item.get("finished_at") or "")
        job_id = str(item.get("job_id") or "")
        current_key = (created_at, job_id)
        existing = latest_by_card.get(card_id)
        if existing is None or current_key >= existing[0]:
            latest_by_card[card_id] = (current_key, item)
    blockers: dict[str, dict[str, Any]] = {}
    for card_id, (_key, item) in latest_by_card.items():
        status = str(item.get("status") or "").strip()
        if status not in BLOCKING_RUNTIME_DEPENDENCY_JOB_STATUSES:
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        blockers[card_id] = {
            "job_id": str(item.get("job_id") or ""),
            "task_id": str(item.get("task_id") or ""),
            "status": status,
            "runtime": str(payload.get("runtime") or ""),
            "packages": list(payload.get("packages") or []),
            "run_id": str(source.get("run_id") or ""),
            "session_id": str(source.get("session_id") or ""),
            "result": item.get("result") if isinstance(item.get("result"), dict) else {},
            "error": str(item.get("error") or ""),
        }
    return blockers


def dependency_blocker_for_card(project_root: Path, card_id: str) -> dict[str, Any] | None:
    if not card_id:
        return None
    return dependency_blockers_by_card(project_root).get(card_id)
