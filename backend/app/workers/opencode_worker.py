from __future__ import annotations

from app.workers.agent_cli_worker import AgentCliWorkerAdapter


class OpenCodeWorkerAdapter(AgentCliWorkerAdapter):
    name = "opencode"
    provider = "opencode"
    launch_template_setting_name = "opencode_command"
    declares_network_access = True
    recommended_launch_examples = [
        "opencode <non-interactive-args> {executor_prompt_path}",
        "bash /absolute/path/to/opencode-launch.sh {executor_prompt_path}",
    ]
    notes = [
        "Prefer a local shell wrapper if your installed opencode CLI syntax differs across versions.",
        "Keep the provider invocation non-interactive so stdout can be streamed back as run events.",
    ]
