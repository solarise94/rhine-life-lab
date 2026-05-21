from __future__ import annotations

from app.workers.claude_code_worker import ClaudeCodeWorkerAdapter
from app.workers.codex_worker import CodexWorkerAdapter
from app.workers.opencode_worker import OpenCodeWorkerAdapter
from app.workers.pi_worker import PiWorkerAdapter


def build_worker_registry() -> dict[str, object]:
    adapters = [
        PiWorkerAdapter(),
        OpenCodeWorkerAdapter(),
        ClaudeCodeWorkerAdapter(),
        CodexWorkerAdapter(),
    ]
    return {adapter.name: adapter for adapter in adapters}
