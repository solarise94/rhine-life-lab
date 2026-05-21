from __future__ import annotations

from app.models.runs import TaskPacket
from app.workers.agent_cli_worker import AgentCliWorkerAdapter
from app.workers.base import WorkerLaunchSpec


class PiWorkerAdapter(AgentCliWorkerAdapter):
    name = "pi"
    provider = "pi"
    launch_template_setting_name = "pi_command"
    declares_network_access = True
    recommended_launch_examples = [
        "pi --no-session -p @{executor_prompt_path}",
        "bash /absolute/path/to/pi-launch.sh {executor_prompt_path}",
    ]
    notes = [
        "Requires BLUEPRINT_PI_COMMAND to point at a real non-interactive pi CLI or wrapper.",
        "The pi agent must write manifest.json, manager_brief.json, and preserved code artifacts.",
        "DeepSeek is used by the backend validator/reviewer through the Manager AI configuration, not as a pi fallback executor.",
    ]

    def resolve_launch_template(self, settings: object) -> str | None:
        return getattr(settings, self.launch_template_setting_name, None)

    def capability_metadata(self, settings: object) -> dict[str, object]:
        metadata = super().capability_metadata(settings)
        metadata["execution_mode"] = "agent_cli_wrapper"
        metadata["requires_configuration"] = True
        metadata["notes"] = list(self.notes)
        return metadata

    def build_launch_spec(
        self,
        *,
        packet: TaskPacket,
        packet_path,
        run_dir,
        project_root,
        settings: object,
    ) -> WorkerLaunchSpec:
        spec = super().build_launch_spec(
            packet=packet,
            packet_path=packet_path,
            run_dir=run_dir,
            project_root=project_root,
            settings=settings,
        )
        api_key = getattr(settings, "deepseek_api_key", None)
        if api_key:
            spec.environment["BLUEPRINT_DEEPSEEK_API_KEY"] = api_key.get_secret_value()
        spec.environment["BLUEPRINT_DEEPSEEK_API_BASE_URL"] = str(getattr(settings, "deepseek_api_base_url", ""))
        spec.environment["BLUEPRINT_MANAGER_MODEL"] = str(getattr(settings, "manager_model", "deepseek-v4-pro"))
        spec.environment["BLUEPRINT_MANAGER_TEMPERATURE"] = str(getattr(settings, "manager_temperature", 0.2))
        spec.environment["BLUEPRINT_MANAGER_MAX_TOKENS"] = str(getattr(settings, "manager_max_tokens", 2400))
        spec.environment["BLUEPRINT_MANAGER_TIMEOUT_SECONDS"] = str(getattr(settings, "manager_timeout_seconds", 600))
        return spec
