from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path
import json
import time

from app.models.runs import CodeArtifact, CreatedAsset, Manifest, TaskPacket
from app.services.utils import atomic_write_json


def _content_for_output(output_type: str, label: str, role: str, packet: TaskPacket) -> str:
    if output_type == "markdown":
        return f"# {label}\n\n{packet.goal}\n\nInputs: {len(packet.input_assets)}\n"
    if output_type == "figure":
        return (
            "\n".join(
                [
                    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 720 320'>",
                    "  <rect width='720' height='320' fill='#0f1724' />",
                    "  <rect x='48' y='56' width='620' height='220' rx='18' fill='#122031' stroke='#35506d' />",
                    f"  <text x='72' y='96' fill='#edf2fb' font-size='24' font-family='Arial'>{label}</text>",
                    f"  <text x='72' y='128' fill='#9cb0cd' font-size='15' font-family='Arial'>Synthetic output for {role}</text>",
                    "  <polyline fill='none' stroke='#4a8cff' stroke-width='6' points='92,232 184,198 276,210 368,148 460,164 552,118 644,134' />",
                    "  <circle cx='184' cy='198' r='6' fill='#f0af44' />",
                    "  <circle cx='368' cy='148' r='6' fill='#3fb37b' />",
                    "  <circle cx='552' cy='118' r='6' fill='#ef5f67' />",
                    "</svg>",
                ]
            )
            + "\n"
        )
    if output_type == "json":
        return json.dumps({"role": role, "label": label, "inputs": len(packet.input_assets)}, ensure_ascii=False, indent=2) + "\n"
    return "feature_id\tqc_score\ninput_trace\t0.91\nretained_signal\t0.84\n"


def main() -> int:
    parser = ArgumentParser()
    parser.add_argument("--task-packet", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()

    packet_path = Path(args.task_packet)
    run_dir = Path(args.run_dir)
    project_root = Path(args.project_root)
    packet = TaskPacket.model_validate(json.loads(packet_path.read_text(encoding="utf-8")))

    result_dir = project_root / "results" / packet.card_id / packet.task_id
    result_dir.mkdir(parents=True, exist_ok=True)

    print(f"[worker] starting {packet.task_id}", flush=True)
    print(
        "BP_EVENT "
        + json.dumps(
            {
                "type": "progress_update",
                "stage": "bootstrap",
                "progress": 5,
                "message": "Task packet loaded and demo execution started.",
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    time.sleep(0.1)
    created_assets: list[CreatedAsset] = []
    code_dir = project_root / "scripts" / "generated" / packet.task_id
    code_dir.mkdir(parents=True, exist_ok=True)
    code_path = code_dir / "demo_executor_replay.py"
    code_path.write_text(
        "\n".join(
            [
                '"""Replay note for the local scaffold executor."""',
                f"TASK_PACKET = {json.dumps(str(packet_path.relative_to(project_root)), ensure_ascii=True)!r}",
                f"RESULT_DIR = {json.dumps(str(result_dir.relative_to(project_root)), ensure_ascii=True)!r}",
                "print('Local scaffold executor produced declared outputs from the task packet contract.')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    total_outputs = len(packet.expected_outputs) or 1
    for index, output in enumerate(packet.expected_outputs, start=1):
        print(f"[worker] writing {output.role}", flush=True)
        output_path = project_root / output.path_hint
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            _content_for_output(output.type, output.label or output.role, output.role, packet),
            encoding="utf-8",
        )
        created_assets.append(
            CreatedAsset(
                role=output.role,
                type=output.type,
                path=str(output_path.relative_to(project_root)),
                description=f"Synthetic output for {output.label or output.role}.",
            )
        )
        print(
            "BP_EVENT "
            + json.dumps(
                {
                    "type": "progress_update",
                    "stage": output.role,
                    "progress": int(index / total_outputs * 100),
                    "message": f"Wrote {output.role}.",
                    "artifacts": [str(output_path.relative_to(project_root))],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        time.sleep(0.1)

    transcript_path = run_dir / "transcript.md"
    transcript_path.write_text(
        "\n".join(
            [
                f"# {packet.task_id}",
                "",
                "Executor transcript",
                "- started local scaffold execution",
                "- wrote expected output assets",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    manifest = Manifest(
        run_id=packet.task_id,
        status="success",
        summary="Local scaffold worker completed successfully.",
        inputs_used=packet.input_assets,
        created_assets=created_assets,
        code_artifacts=[
            CodeArtifact(
                path=str(code_path.relative_to(project_root)),
                language="python",
                purpose="Preserved scaffold executor replay note for validator review.",
            )
        ],
        commands_executed=["demo_executor"],
        metrics={"synthetic_score": 0.91, "asset_count": len(created_assets)},
        key_findings=["Generated representative downstream result set."],
        recommended_graph_updates=[
            {"op": "create_asset", "asset_type": asset.type, "role": asset.role}
            for asset in created_assets
        ],
        warnings=[],
    )
    atomic_write_json(run_dir / "manifest.json", manifest.model_dump())
    print(
        "BP_EVENT "
        + json.dumps(
            {
                "type": "final_report",
                "summary": manifest.summary,
                "key_findings": manifest.key_findings,
                "warnings": manifest.warnings,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    print("[worker] manifest complete", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
