from __future__ import annotations

from app.workers.command_worker import CommandTemplateWorkerAdapter


class CodexWorkerAdapter(CommandTemplateWorkerAdapter):
    name = "codex"
    declares_network_access = True

    def resolve_command_template(self, settings: object) -> str | None:
        return getattr(settings, "codex_command", None)

    def capability_metadata(self, settings: object) -> dict[str, object]:
        metadata = super().capability_metadata(settings)
        metadata["launch_template_setting"] = "codex_command"
        metadata["notes"] = [
            "Codex still launches as a direct backend command template rather than through the shared agent CLI wrapper.",
            "Use a local wrapper script if you need prompt-file translation or extra bootstrap behavior.",
        ]
        metadata["recommended_launch_examples"] = [
            "codex <non-interactive-args> {executor_prompt_path}",
            "bash /absolute/path/to/codex-launch.sh {executor_prompt_path}",
        ]
        return metadata
