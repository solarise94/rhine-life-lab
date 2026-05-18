from __future__ import annotations

from app.workers.command_worker import CommandTemplateWorkerAdapter


class ShellWorkerAdapter(CommandTemplateWorkerAdapter):
    name = "shell"
    command_template = "{python} -m app.workers.demo_executor --task-packet {task_packet_path} --run-dir {run_dir} --project-root {project_root}"
