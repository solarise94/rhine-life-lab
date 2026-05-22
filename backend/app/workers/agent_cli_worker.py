from __future__ import annotations

from pathlib import Path

from app.models.runs import TaskPacket
from app.workers.command_worker import CommandTemplateWorkerAdapter
from app.workers.base import WorkerLaunchSpec


class AgentCliWorkerAdapter(CommandTemplateWorkerAdapter):
    provider: str = ""
    launch_template_setting_name: str = ""
    wrapper_module: str = "app.workers.agent_cli_executor"
    recommended_launch_examples: list[str] = []
    notes: list[str] = [
        "This adapter runs through the shared Blueprint agent_cli_executor wrapper.",
        "The provider launch template should invoke a non-interactive CLI that consumes executor_prompt.md and writes manifest.json.",
    ]

    def resolve_command_template(self, settings: object) -> str | None:
        if not self.is_configured(settings):
            return None
        return (
            "{python} -m "
            + self.wrapper_module
            + f" --provider {self.provider} --task-packet {{task_packet_path}} --run-dir {{run_dir}} --project-root {{project_root}}"
        )

    def is_configured(self, settings: object) -> bool:
        return bool(self.resolve_launch_template(settings))

    def resolve_launch_template(self, settings: object) -> str | None:
        if not self.launch_template_setting_name:
            return None
        return getattr(settings, self.launch_template_setting_name, None)

    def capability_metadata(self, settings: object) -> dict[str, object]:
        metadata = super().capability_metadata(settings)
        metadata["execution_mode"] = "agent_cli_wrapper"
        metadata["launch_template_setting"] = self.launch_template_setting_name
        metadata["wrapper_module"] = self.wrapper_module
        metadata["provider"] = self.provider
        metadata["recommended_launch_examples"] = list(self.recommended_launch_examples)
        metadata["notes"] = list(self.notes)
        return metadata

    def build_launch_spec(
        self,
        *,
        packet: TaskPacket,
        packet_path: Path,
        run_dir: Path,
        project_root: Path,
        settings: object,
    ) -> WorkerLaunchSpec:
        launch_template = self.resolve_launch_template(settings)
        if not launch_template:
            raise RuntimeError(f"Worker adapter {self.name} is not configured.")
        spec = super().build_launch_spec(
            packet=packet,
            packet_path=packet_path,
            run_dir=run_dir,
            project_root=project_root,
            settings=settings,
        )
        return spec

    def extra_environment(self, *, packet: TaskPacket, settings: object) -> dict[str, str]:
        launch_template = self.resolve_launch_template(settings)
        if not launch_template:
            return {}
        return {
            "BLUEPRINT_AGENT_PROVIDER": self.provider,
            "BLUEPRINT_AGENT_LAUNCH_TEMPLATE": launch_template,
        }
