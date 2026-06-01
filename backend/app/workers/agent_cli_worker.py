from __future__ import annotations

import json
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
        "The provider launch template should invoke a non-interactive CLI that consumes executor_prompt.md, writes manifest.candidate.json, and calls report_executor_result.py.",
    ]

    def resolve_command_template(self, settings: object) -> str | None:
        if not self.is_configured(settings):
            return None
        return (
            "{python} -m "
            + self.wrapper_module
            + f" --provider {self.provider} --task-packet {{task_packet_path}} --run-dir {{run_dir}} --project-root {{project_root}}"
        )

    def resolve_command_argv_template(self, settings: object) -> list[str] | None:
        """Return the wrapper invocation argv list (not the provider command).

        This ensures the wrapper is launched with proper argv, avoiding shlex.split
        issues with paths containing spaces.
        """
        if not self.is_configured(settings):
            return None
        return [
            "{python}",
            "-m",
            self.wrapper_module,
            "--provider",
            self.provider,
            "--task-packet",
            "{task_packet_path}",
            "--run-dir",
            "{run_dir}",
            "--project-root",
            "{project_root}",
        ]

    def resolve_launch_argv_template(self, settings: object) -> list[str] | None:
        """Return the provider command argv template from *_command_json setting.

        This is passed to the wrapper via BLUEPRINT_AGENT_LAUNCH_ARGV_TEMPLATE env var.
        """
        if not self.launch_template_setting_name:
            return None
        json_setting_name = f"{self.launch_template_setting_name}_json"
        return getattr(settings, json_setting_name, None)

    def is_configured(self, settings: object) -> bool:
        return bool(self.resolve_launch_template(settings) or self.resolve_launch_argv_template(settings))

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
        launch_argv_template = self.resolve_launch_argv_template(settings)
        if not launch_template and not launch_argv_template:
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
        launch_argv_template = self.resolve_launch_argv_template(settings)
        if not launch_template and not launch_argv_template:
            return {}
        worker_timeout = getattr(settings, "worker_timeout_seconds", 1800)
        repair_timeout = getattr(settings, "manifest_repair_timeout_seconds", 180)
        env = {
            "BLUEPRINT_AGENT_PROVIDER": self.provider,
            "BLUEPRINT_WORKER_TIMEOUT_SECONDS": str(worker_timeout),
            "BLUEPRINT_MANIFEST_REPAIR_TIMEOUT_SECONDS": str(repair_timeout),
        }
        # Prefer structured argv template over string template
        if launch_argv_template:
            env["BLUEPRINT_AGENT_LAUNCH_ARGV_TEMPLATE"] = json.dumps(launch_argv_template)
        if launch_template:
            env["BLUEPRINT_AGENT_LAUNCH_TEMPLATE"] = launch_template
        profile_id, auth_mode, api_protocol = self._resolve_profile_hints(packet, settings)
        if profile_id:
            env["BLUEPRINT_EXECUTOR_PROFILE_ID"] = profile_id
        if auth_mode:
            env["BLUEPRINT_AUTH_MODE"] = auth_mode
        if api_protocol:
            env["BLUEPRINT_API_PROTOCOL"] = api_protocol
        return env

    def _resolve_profile_hints(self, packet: TaskPacket, settings: object) -> tuple[str | None, str | None, str | None]:
        """Resolve profile_id, auth_mode, and api_protocol from stored profiles."""
        from app.services.app_config_service import AppConfigService

        config_service = AppConfigService(settings)
        requested_profile_id = None
        requested_profile_from_legacy = False
        if packet.executor_context:
            requested_profile_id = packet.executor_context.executor_profile_id
            if not requested_profile_id and packet.executor_context.executor_profile:
                legacy_profile = packet.executor_context.executor_profile
                if not legacy_profile.endswith("_worker"):
                    requested_profile_id = legacy_profile
                    requested_profile_from_legacy = True
        if not requested_profile_id:
            return None, None, None
        profile = config_service.resolve_executor_profile(self.provider, profile_id=requested_profile_id)
        if profile is None:
            if requested_profile_from_legacy:
                return None, None, None
            raise RuntimeError(f"Executor profile {requested_profile_id} is not configured for worker_type={self.provider}.")
        return (
            profile.get("profile_id"),
            profile.get("auth_mode"),
            profile.get("api_protocol"),
        )
