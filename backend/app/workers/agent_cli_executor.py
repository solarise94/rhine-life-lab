from __future__ import annotations

from argparse import ArgumentParser
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def _parse_args() -> object:
    parser = ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--task-packet", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--project-root", required=True)
    return parser.parse_args()


def _load_task_packet(packet_path: Path) -> dict:
    return json.loads(packet_path.read_text(encoding="utf-8"))


def _render_launch_command(template: str, *, provider: str, packet_path: Path, run_dir: Path, project_root: Path) -> list[str]:
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    mapping = {
        "python": sys.executable,
        "project_root": str(project_root),
        "run_dir": str(run_dir),
        "result_dir": str(project_root / packet["run_context"]["result_dir"]),
        "task_packet_path": str(packet_path),
        "manifest_path": str(run_dir / "manifest.json"),
        "transcript_path": str(run_dir / "transcript.md"),
        "executor_brief_path": str(run_dir / "executor_brief.md"),
        "executor_prompt_path": str(run_dir / "executor_prompt.md"),
        "adapter_contract_path": str(run_dir / "adapter_contract.json"),
        "manager_brief_path": str(run_dir / "manager_brief.json"),
        "worker_type": provider,
    }
    try:
        return shlex.split(template.format(**mapping))
    except KeyError as exc:
        missing = exc.args[0]
        raise RuntimeError(f"Launch template for provider={provider} referenced unknown placeholder {{{missing}}}.") from exc


def main() -> int:
    args = _parse_args()
    provider = args.provider
    packet_path = Path(args.task_packet)
    run_dir = Path(args.run_dir)
    project_root = Path(args.project_root)
    env = os.environ.copy()
    template = env.get("BLUEPRINT_AGENT_LAUNCH_TEMPLATE", "").strip()
    if not template:
        print(f"[wrapper] missing launch template for provider={provider}", flush=True)
        return 2

    packet = _load_task_packet(packet_path)
    print(
        "BP_EVENT "
        + json.dumps(
            {
                "type": "progress_update",
                "stage": "dispatch",
                "progress": 1,
                "message": f"Dispatching {provider} wrapper with unified executor prompt.",
                "metadata": {"provider": provider},
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        command = _render_launch_command(template, provider=provider, packet_path=packet_path, run_dir=run_dir, project_root=project_root)
    except RuntimeError as exc:
        print(f"[wrapper] {exc}", flush=True)
        return 2
    try:
        process = subprocess.Popen(
            command,
            cwd=project_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        print(f"[wrapper] failed to launch provider={provider}: {exc}", flush=True)
        return 2
    if process.stdout is not None:
        for raw_line in process.stdout:
            line = raw_line.rstrip("\n")
            if line:
                print(line, flush=True)
    return_code = process.wait()
    manifest_path = run_dir / "manifest.json"
    if return_code == 0 and not manifest_path.exists():
        print(f"[wrapper] {provider} exited successfully but manifest.json is missing.", flush=True)
        return 1
    print(
        "BP_EVENT "
        + json.dumps(
            {
                "type": "progress_update",
                "stage": "dispatch_complete",
                "progress": 100 if return_code == 0 else None,
                "message": f"{provider} wrapper finished with exit code {return_code}.",
                "metadata": {"provider": provider, "input_assets": len(packet.get('input_assets', []))},
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
