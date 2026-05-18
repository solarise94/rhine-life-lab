from __future__ import annotations

from pathlib import Path

from app.services.project_service import ProjectService
from app.services.utils import atomic_write_json, read_json, utc_now


class RuntimeApprovalService:
    def __init__(self, project_service: ProjectService) -> None:
        self.project_service = project_service

    def review_permission_request(self, project_id: str, run_id: str, request_payload: dict, readonly_paths: list[str] | None = None) -> dict:
        readonly_paths = readonly_paths or []
        target = request_payload.get("target", "")
        action = request_payload.get("action", "")
        risk_level, decision, user_required, reason = self._classify(run_id, target, action, readonly_paths)
        payload = {
            "request_id": request_payload.get("request_id", f"perm_{run_id}_{len(self.load_decisions(project_id, run_id)) + 1:03d}"),
            "target": target,
            "action": action,
            "risk_level": risk_level,
            "decision": decision,
            "decided_by": "manager_ai" if decision != "needs_user_confirmation" else "pending_user",
            "user_required": user_required,
            "reason": reason,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        decisions = [item for item in self.load_decisions(project_id, run_id) if item["request_id"] != payload["request_id"]]
        decisions.append(payload)
        self._save_decisions(project_id, run_id, decisions)
        return payload

    def load_decisions(self, project_id: str, run_id: str) -> list[dict]:
        return list(read_json(self._path(project_id, run_id), []))

    def decide_request(self, project_id: str, run_id: str, request_id: str, approve: bool) -> dict:
        decisions = self.load_decisions(project_id, run_id)
        decision = next(item for item in decisions if item["request_id"] == request_id)
        decision["decision"] = "user_approved" if approve else "user_rejected"
        decision["decided_by"] = "user"
        decision["user_required"] = False
        decision["updated_at"] = utc_now()
        self._save_decisions(project_id, run_id, decisions)
        return decision

    def unresolved_user_requests(self, project_id: str, run_id: str) -> list[dict]:
        return [item for item in self.load_decisions(project_id, run_id) if item["decision"] == "needs_user_confirmation"]

    def _path(self, project_id: str, run_id: str) -> Path:
        return self.project_service.project_path(project_id) / "runs" / run_id / "runtime_approvals.json"

    def _save_decisions(self, project_id: str, run_id: str, decisions: list[dict]) -> None:
        atomic_write_json(self._path(project_id, run_id), decisions)

    @staticmethod
    def _classify(run_id: str, target: str, action: str, readonly_paths: list[str]) -> tuple[str, str, bool, str]:
        safe_prefixes = (f"runs/{run_id}/", f"results/", "scripts/generated/")
        dangerous_prefixes = (".git/", "graph/", "artifact_store/")

        if any(target.startswith(prefix) for prefix in dangerous_prefixes):
            return "dangerous", "rejected", True, "Requested path targets protected project internals."
        if target in readonly_paths or any(target.startswith(f"{item}/") for item in readonly_paths):
            return "high", "needs_user_confirmation", True, "Requested permission would modify a readonly input path."
        if target.startswith(f"runs/{run_id}/") or target.startswith("results/"):
            return "low", "auto_approved", False, "Requested path stays under the current run workspace."
        if target.startswith("data/") or action == "network":
            return "medium", "needs_user_confirmation", True, "Requested permission touches project data or network access."
        if any(target.startswith(prefix) for prefix in safe_prefixes):
            return "low", "auto_approved", False, "Requested path is within an allowed worker output scope."
        return "high", "needs_user_confirmation", True, "Requested permission touches a path outside declared worker scopes."
