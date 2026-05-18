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


class WorkerAdapter:
    name: str = "worker"

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
