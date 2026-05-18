from __future__ import annotations

from app.workers.claude_code_worker import ClaudeCodeWorkerAdapter
from app.workers.codex_worker import CodexWorkerAdapter
from app.workers.fake_worker import FakeWorkerAdapter
from app.workers.opencode_worker import OpenCodeWorkerAdapter
from app.workers.pi_worker import PiWorkerAdapter
from app.workers.shell_worker import ShellWorkerAdapter


def build_worker_registry() -> dict[str, object]:
    adapters = [
        OpenCodeWorkerAdapter(),
        PiWorkerAdapter(),
        ClaudeCodeWorkerAdapter(),
        CodexWorkerAdapter(),
        ShellWorkerAdapter(),
        FakeWorkerAdapter(),
    ]
    return {adapter.name: adapter for adapter in adapters}
