from __future__ import annotations

from app.workers.shell_worker import ShellWorkerAdapter


class FakeWorkerAdapter(ShellWorkerAdapter):
    name = "fake_worker"
