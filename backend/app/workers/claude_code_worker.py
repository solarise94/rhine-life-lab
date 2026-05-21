from __future__ import annotations

from app.workers.agent_cli_worker import AgentCliWorkerAdapter


class ClaudeCodeWorkerAdapter(AgentCliWorkerAdapter):
    name = "claude_code"
    provider = "claude_code"
    launch_template_setting_name = "claude_code_command"
    declares_network_access = True
    recommended_launch_examples = [
        "claude-code <non-interactive-args> {executor_prompt_path}",
        "bash /absolute/path/to/claude-code-launch.sh {executor_prompt_path}",
    ]
    notes = [
        "Keep claude-code in a print/non-interactive mode so the wrapper can forward stdout deterministically.",
        "If your CLI only supports a chat subcommand, hide that complexity inside a local launch script.",
    ]
