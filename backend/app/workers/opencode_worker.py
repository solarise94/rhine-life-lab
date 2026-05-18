from __future__ import annotations

from app.workers.command_worker import CommandTemplateWorkerAdapter


class OpenCodeWorkerAdapter(CommandTemplateWorkerAdapter):
    name = "opencode"

    def resolve_command_template(self, settings: object) -> str | None:
        return getattr(settings, "opencode_command", None)
