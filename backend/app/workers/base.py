from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.models.runs import TaskPacket


@dataclass(frozen=True)
class PermissionRequest:
    request_id: str
    target: str
    action: str
    reason: str


@dataclass(frozen=True)
class WorkerLaunchSpec:
    command: list[str]
    cwd: Path
    environment: dict[str, str]
    permission_requests: list[PermissionRequest]
    sandboxed: bool = False


class WorkerAdapter:
    name: str = "worker"
    requires_configuration: bool = False
    declares_network_access: bool = False

    def build_launch_spec(
        self,
        *,
        packet: TaskPacket,
        packet_path: Path,
        run_dir: Path,
        project_root: Path,
        settings: Any,
    ) -> WorkerLaunchSpec:
        raise NotImplementedError

    def is_configured(self, settings: Any) -> bool:
        return True

    def capability_metadata(self, settings: Any) -> dict[str, Any]:
        return {
            "worker_type": self.name,
            "configured": self.is_configured(settings),
            "requires_configuration": self.requires_configuration,
            "declares_network_access": self.declares_network_access,
            "execution_mode": "native",
            "launch_template_setting": None,
            "wrapper_module": None,
            "recommended_launch_examples": [],
            "notes": [],
        }

    def uses_sandbox(self, settings: Any) -> bool:
        return False
