from __future__ import annotations

from app.workers.command_worker import CommandTemplateWorkerAdapter


class CodexWorkerAdapter(CommandTemplateWorkerAdapter):
    name = "codex"

    def resolve_command_template(self, settings: object) -> str | None:
        return getattr(settings, "codex_command", None)
