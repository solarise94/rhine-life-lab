from __future__ import annotations

from app.workers.command_worker import CommandTemplateWorkerAdapter


class ClaudeCodeWorkerAdapter(CommandTemplateWorkerAdapter):
    name = "claude_code"

    def resolve_command_template(self, settings: object) -> str | None:
        return getattr(settings, "claude_code_command", None)
