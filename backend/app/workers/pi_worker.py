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
        return spec

    def extra_environment(self, *, packet: TaskPacket, settings: object) -> dict[str, str]:
        environment = super().extra_environment(packet=packet, settings=settings)
        api_key = getattr(settings, "pi_api_key", None) or getattr(settings, "deepseek_api_key", None)
        if not api_key:
            raise RuntimeError(
                "Pi executor cannot start: no API key available. "
                "Configure an Anthropic-compatible provider with an API key and bind it to the pi_executor role."
            )
        environment["BLUEPRINT_DEEPSEEK_API_KEY"] = api_key.get_secret_value()
        environment["BLUEPRINT_DEEPSEEK_API_BASE_URL"] = str(
            getattr(settings, "pi_anthropic_base_url", None) or getattr(settings, "deepseek_api_base_url", "")
        )
        environment["BLUEPRINT_PI_DEEPSEEK_BASE_URL"] = str(getattr(settings, "pi_deepseek_base_url", "https://api.deepseek.com"))
        environment["BLUEPRINT_MANAGER_MODEL"] = str(getattr(settings, "manager_model", "deepseek-v4-pro"))
        environment["BLUEPRINT_EXECUTOR_MODEL"] = str(
            getattr(settings, "pi_executor_model", None)
            or getattr(settings, "executor_model", getattr(settings, "manager_model", "deepseek-v4-flash"))
        )
        environment["BLUEPRINT_REVIEWER_MODEL"] = str(
            getattr(settings, "reviewer_model", getattr(settings, "manager_model", "deepseek-v4-flash"))
        )
        environment["BLUEPRINT_MANAGER_TEMPERATURE"] = str(getattr(settings, "manager_temperature", 0.2))
        environment["BLUEPRINT_MANAGER_MAX_TOKENS"] = str(getattr(settings, "manager_max_tokens", 2400))
        environment["BLUEPRINT_MANAGER_TIMEOUT_SECONDS"] = str(getattr(settings, "manager_timeout_seconds", 600))
        return environment
