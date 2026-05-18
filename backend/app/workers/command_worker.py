from __future__ import annotations

from pathlib import Path
import os
import shlex
import sys

from app.models.runs import TaskPacket
from app.workers.base import PermissionRequest, WorkerAdapter, WorkerLaunchSpec


class CommandTemplateWorkerAdapter(WorkerAdapter):
    command_template: str | None = None
    requires_configuration: bool = False

    def build_launch_spec(
        self,
        *,
        packet: TaskPacket,
        packet_path: Path,
        run_dir: Path,
        project_root: Path,
        settings: object,
    ) -> WorkerLaunchSpec:
        template = self.resolve_command_template(settings)
        if not template:
            raise RuntimeError(f"Worker adapter {self.name} is not configured.")
        mapping = {
            "task_packet_path": str(packet_path),
            "run_dir": str(run_dir),
            "project_root": str(project_root),
            "python": sys.executable,
        }
        command = shlex.split(template.format(**mapping))
        backend_root = Path(__file__).resolve().parents[2]
        pythonpath = os.environ.get("PYTHONPATH", "")
        merged_pythonpath = str(backend_root) if not pythonpath else f"{backend_root}{os.pathsep}{pythonpath}"
        return WorkerLaunchSpec(
            command=command,
            cwd=project_root,
            environment={
                **os.environ,
                "BLUEPRINT_PROJECT_ROOT": str(project_root),
                "BLUEPRINT_RUN_DIR": str(run_dir),
                "BLUEPRINT_TASK_PACKET": str(packet_path),
                "PYTHONPATH": merged_pythonpath,
            },
            permission_requests=[
                PermissionRequest(
                    request_id=f"perm_{packet.task_id}_write_results",
                    target=f"results/{packet.card_id}/{packet.task_id}/",
                    action="write",
                    reason="Worker needs to write outputs under the declared result directory.",
                ),
                PermissionRequest(
                    request_id=f"perm_{packet.task_id}_write_run_dir",
                    target=f"runs/{packet.task_id}/",
                    action="write",
                    reason="Worker needs to write transcript, logs, and manifest for the current run.",
                ),
            ],
        )

    def resolve_command_template(self, settings: object) -> str | None:
        if self.command_template:
            return self.command_template
        return None
