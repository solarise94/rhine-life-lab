from __future__ import annotations

from app.workers.agent_cli_worker import AgentCliWorkerAdapter


class CodexWorkerAdapter(AgentCliWorkerAdapter):
    name = "codex"
    provider = "codex"
    launch_template_setting_name = "codex_command"
    declares_network_access = True
    recommended_launch_examples = [
        "codex exec --full-auto {executor_prompt_path}",
        "bash /absolute/path/to/codex-launch.sh {executor_prompt_path}",
    ]
    notes = [
        "Codex runs through the shared Blueprint agent_cli_executor wrapper.",
        "Only cli_native auth mode is supported; project_api mode is blocked until a dedicated OpenAI-compatible renderer is built.",
        "Prefers JSON/JSONL output mode for structured event capture when available.",
    ]
