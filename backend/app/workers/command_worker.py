from __future__ import annotations

import json
from pathlib import Path
import os
import shlex
import sys

from app.models.runs import TaskPacket
from app.workers.base import PermissionRequest, WorkerAdapter, WorkerLaunchSpec


class CommandTemplateWorkerAdapter(WorkerAdapter):
    command_template: str | None = None
    requires_configuration: bool = False
    declares_network_access: bool = False

    def build_launch_spec(
        self,
        *,
        packet: TaskPacket,
        packet_path: Path,
        run_dir: Path,
        project_root: Path,
        settings: object,
    ) -> WorkerLaunchSpec:
        template = self.resolve_command_template(settings)
        if not template:
            raise RuntimeError(f"Worker adapter {self.name} is not configured.")
        self._validate_executor_policy(packet)
        contract_paths = self._write_contract_files(packet=packet, run_dir=run_dir)
        r_profile_path = self._write_runtime_r_profile(run_dir)
        mapping = {
            "task_packet_path": str(packet_path),
            "run_dir": str(run_dir),
            "project_root": str(project_root),
            "result_dir": str(project_root / packet.run_context.result_dir),
            "manifest_path": str(run_dir / "manifest.json"),
            "transcript_path": str(run_dir / "transcript.md"),
            "executor_brief_path": str(contract_paths["executor_brief_path"]),
            "executor_prompt_path": str(contract_paths["executor_prompt_path"]),
            "adapter_contract_path": str(contract_paths["adapter_contract_path"]),
            "manager_brief_path": str(run_dir / "manager_brief.json"),
            "worker_type": self.name,
            "python": sys.executable,
        }
        command = shlex.split(template.format(**mapping))
        backend_root = Path(__file__).resolve().parents[2]
        pythonpath = os.environ.get("PYTHONPATH", "")
        merged_pythonpath = str(backend_root) if not pythonpath else f"{backend_root}{os.pathsep}{pythonpath}"
        runtime_env = packet.executor_context.runtime_bindings.env if packet.executor_context else {}
        return WorkerLaunchSpec(
            command=command,
            cwd=project_root,
            environment={
                **os.environ,
                **runtime_env,
                "BLUEPRINT_PROJECT_ROOT": str(project_root),
                "BLUEPRINT_RUN_DIR": str(run_dir),
                "BLUEPRINT_RESULT_DIR": packet.run_context.result_dir,
                "BLUEPRINT_TASK_PACKET": str(packet_path),
                "BLUEPRINT_MANIFEST_PATH": str(run_dir / "manifest.json"),
                "BLUEPRINT_TRANSCRIPT_PATH": str(run_dir / "transcript.md"),
                "BLUEPRINT_EXECUTOR_BRIEF": str(contract_paths["executor_brief_path"]),
                "BLUEPRINT_EXECUTOR_PROMPT": str(contract_paths["executor_prompt_path"]),
                "BLUEPRINT_ADAPTER_CONTRACT": str(contract_paths["adapter_contract_path"]),
                "BLUEPRINT_MANAGER_BRIEF": str(run_dir / "manager_brief.json"),
                "BLUEPRINT_ALLOWED_PATHS": json.dumps(packet.allowed_paths),
                "BLUEPRINT_READONLY_PATHS": json.dumps(packet.readonly_paths),
                "BLUEPRINT_FORBIDDEN_PATHS": json.dumps(packet.forbidden_paths),
                "BLUEPRINT_WORKER_TYPE": self.name,
                "BLUEPRINT_EXECUTOR_PROFILE": packet.executor_context.executor_profile if packet.executor_context else "",
                "BLUEPRINT_EXECUTOR_SKILLS": json.dumps(packet.executor_context.skills if packet.executor_context else []),
                "BLUEPRINT_RUNTIME_WORKING_DIR": packet.executor_context.runtime_bindings.working_dir if packet.executor_context else ".",
                "BLUEPRINT_MANAGER_REPORT_STDOUT_PREFIX": (
                    packet.manager_reporting_contract.stdout_prefix if packet.manager_reporting_contract else "BP_EVENT "
                ),
                "R_PROFILE_USER": str(r_profile_path),
                "R_DEFAULT_DEVICE": "pdf",
                "PYTHONPATH": merged_pythonpath,
            },
            permission_requests=self._build_permission_requests(packet),
        )

    def resolve_command_template(self, settings: object) -> str | None:
        if self.command_template:
            return self.command_template
        return None

    def is_configured(self, settings: object) -> bool:
        return bool(self.resolve_command_template(settings))

    def capability_metadata(self, settings: object) -> dict[str, object]:
        metadata = super().capability_metadata(settings)
        metadata["execution_mode"] = "command_template"
        return metadata

    def _build_permission_requests(self, packet: TaskPacket) -> list[PermissionRequest]:
        requests = [
            PermissionRequest(
                request_id=f"perm_{packet.task_id}_write_results",
                target=f"results/{packet.card_id}/{packet.task_id}/",
                action="write",
                reason="Worker needs to write outputs under the declared result directory.",
            ),
            PermissionRequest(
                request_id=f"perm_{packet.task_id}_write_run_dir",
                target=f"runs/{packet.task_id}/",
                action="write",
                reason="Worker needs to write transcript, logs, and manifest for the current run.",
            ),
            PermissionRequest(
                request_id=f"perm_{packet.task_id}_write_generated_scripts",
                target="scripts/generated/",
                action="write",
                reason="Worker may generate reusable helper scripts under scripts/generated/.",
            ),
        ]
        network_policy = packet.executor_context.tool_policy.network if packet.executor_context else "prompt"
        if self.declares_network_access and network_policy == "prompt":
            requests.append(
                PermissionRequest(
                    request_id=f"perm_{packet.task_id}_network",
                    target="network",
                    action="network",
                    reason="Worker requested conditional network access under the executor tool policy.",
                )
            )
        return requests

    @staticmethod
    def _write_runtime_r_profile(run_dir: Path) -> Path:
        path = run_dir / ".Rprofile"
        path.write_text(
            "pdf(file = file.path(Sys.getenv('BLUEPRINT_RUN_DIR', '.'), 'Rplots.pdf'))\n",
            encoding="utf-8",
        )
        return path

    def _validate_executor_policy(self, packet: TaskPacket) -> None:
        network_policy = packet.executor_context.tool_policy.network if packet.executor_context else "prompt"
        if self.declares_network_access and network_policy == "deny":
            raise RuntimeError(
                f"Worker adapter {self.name} requires model/network access, but executor_context.tool_policy.network=deny."
            )

    def _write_contract_files(self, *, packet: TaskPacket, run_dir: Path) -> dict[str, Path]:
        executor_brief_path = run_dir / "executor_brief.md"
        executor_prompt_path = run_dir / "executor_prompt.md"
        adapter_contract_path = run_dir / "adapter_contract.json"
        executor_brief_path.write_text(self._render_executor_brief(packet), encoding="utf-8")
        executor_prompt_path.write_text(self._render_executor_prompt(packet), encoding="utf-8")
        adapter_contract_path.write_text(
            json.dumps(
                {
                    "worker_type": self.name,
                    "task_packet_path": "task_packet.json",
                    "executor_prompt_path": "executor_prompt.md",
                    "manifest_path": "manifest.json",
                    "manager_brief_path": "manager_brief.json",
                    "executor_validation_path": "executor_validation.json",
                    "stdout_prefix": packet.manager_reporting_contract.stdout_prefix if packet.manager_reporting_contract else "BP_EVENT ",
                    "allowed_paths": packet.allowed_paths,
                    "readonly_paths": packet.readonly_paths,
                    "forbidden_paths": packet.forbidden_paths,
                    "declares_network_access": self.declares_network_access,
                    "template_fields": [
                        "python",
                        "project_root",
                        "run_dir",
                        "result_dir",
                        "task_packet_path",
                        "manifest_path",
                        "transcript_path",
                        "executor_brief_path",
                        "executor_prompt_path",
                        "adapter_contract_path",
                        "manager_brief_path",
                        "worker_type",
                    ],
                    "expected_outputs": [item.model_dump() for item in packet.expected_outputs],
                    "required_manifest_fields": [
                        "run_id",
                        "status",
                        "summary",
                        "inputs_used",
                        "created_assets",
                        "code_artifacts",
                        "commands_executed",
                    ],
                    "code_artifact_scope": f"scripts/generated/{packet.task_id}/",
                },
                ensure_ascii=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return {
            "executor_brief_path": executor_brief_path,
            "executor_prompt_path": executor_prompt_path,
            "adapter_contract_path": adapter_contract_path,
        }

    def _render_executor_brief(self, packet: TaskPacket) -> str:
        lines = [
            f"# Executor Brief for {packet.task_id}",
            "",
            "## Task",
            f"- Project: {packet.project_id}",
            f"- Card: {packet.card_id} ({packet.card_title})",
            f"- Goal: {packet.goal}",
            "",
            "## Inputs",
        ]
        if packet.input_assets:
            lines.extend(f"- {item.asset_id}: {item.path} [{item.type}]" for item in packet.input_assets)
        else:
            lines.append("- No linked input assets.")
        lines.extend(
            [
                "",
                "## Expected Outputs",
            ]
        )
        lines.extend(f"- {item.role}: {item.path_hint} ({item.type})" for item in packet.expected_outputs)
        lines.extend(
            [
                "",
                "## Runtime Policy",
                f"- Allowed paths: {', '.join(packet.allowed_paths)}",
                f"- Readonly paths: {', '.join(packet.readonly_paths) if packet.readonly_paths else 'none'}",
                f"- Forbidden paths: {', '.join(packet.forbidden_paths)}",
            ]
        )
        if packet.executor_context:
            lines.extend(
                [
                    "",
                    "## Executor Context",
                    f"- Profile: {packet.executor_context.executor_profile or 'none'}",
                    f"- Skills: {', '.join(packet.executor_context.skills) if packet.executor_context.skills else 'none'}",
                ]
            )
            lines.extend(f"- Instruction: {item}" for item in packet.executor_context.instruction_blocks)
            lines.extend(
                f"- Reference: {item.path} ({item.type})" for item in packet.executor_context.references
            )
        lines.extend(
            [
                "",
                "## Reporting Contract",
                "- Report progress/issues/final summary through BP_EVENT stdout or manager_brief.json.",
                "- Preserve executed code under scripts/generated/{run_id}/ and declare it in manifest.code_artifacts.",
                "- Backend validation will reject missing outputs, missing code evidence, path violations, and placeholder data.",
            ]
        )
        return "\n".join(lines) + "\n"

    def _render_executor_prompt(self, packet: TaskPacket) -> str:
        lines = [
            f"You are the {self.name} executor for Blueprint run {packet.task_id}.",
            "",
            "Primary objective:",
            packet.goal,
            "",
            "Executor contract:",
            "- The backend validates outputs using task_packet.json, adapter_contract.json, "
            "manifest.json, manager_brief.json, and preserved code artifacts.",
            "- If validation fails, the run will return structured errors for repair instead of being accepted by Manager.",
            "- Keep executable analysis code under scripts/generated/{run_id}/ and declare it in manifest.code_artifacts.",
            "",
            "Task packet:",
            "- JSON path: task_packet.json",
            f"- Project root: {packet.run_context.project_root if packet.run_context else '.'}",
            f"- Run dir: {packet.run_context.run_dir if packet.run_context else 'runs/current'}",
            f"- Result dir: {packet.run_context.result_dir if packet.run_context else 'results/current'}",
            "",
            "Input assets:",
        ]
        if packet.input_assets:
            lines.extend(f"- {item.asset_id}: {item.path} ({item.type})" for item in packet.input_assets)
        else:
            lines.append("- No materialized input assets were attached.")
        lines.extend(
            [
                "",
                "Expected outputs:",
            ]
        )
        lines.extend(f"- {item.role}: {item.path_hint} ({item.type})" for item in packet.expected_outputs)
        if packet.executor_context:
            lines.extend(
                [
                    "",
                    "Executor context:",
                    f"- Profile: {packet.executor_context.executor_profile or 'none'}",
                    f"- Skills: {', '.join(packet.executor_context.skills) if packet.executor_context.skills else 'none'}",
                ]
            )
            lines.extend(f"- Instruction: {item}" for item in packet.executor_context.instruction_blocks)
            lines.extend(f"- Reference: {item.path} ({item.type})" for item in packet.executor_context.references)
        lines.extend(
            [
                "",
                "Output contract:",
                "- manifest.json must include every consumed input in inputs_used.",
                "- manifest.json must declare every created asset with role/type/path.",
                "- manifest.json must declare preserved code in code_artifacts when assets are created.",
                "- manager_brief.json should summarize final status for Manager; it must not mutate graph/card state.",
            ]
        )
        return "\n".join(lines) + "\n"
