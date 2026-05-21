# Real Pi Executor Validation Plan

## Goal

Blueprint should treat `pi` as a real external agent CLI. The backend executor framework owns run setup, boundaries, event capture, validation, and Manager handoff. The `pi` agent owns code generation, tool execution, data processing, and evidence submission.

The Manager must not trust executor claims directly. It should receive a `manager_brief.json` assembled from executor events plus validator/reviewer results, then decide how to update cards/assets/claims during review.

## Target Flow

1. Manager creates or reruns a card.
2. Worker service creates `task_packet.json`, `executor_prompt.md`, `executor_brief.md`, and `adapter_contract.json`.
3. `pi_worker` launches the configured external `BLUEPRINT_PI_COMMAND` through the shared `agent_cli_executor` wrapper.
4. `pi` reads the task packet, writes reproducible code under `scripts/generated/{run_id}/`, runs tools, writes output assets under `results/{card_id}/{run_id}/`, and writes `manifest.json`.
5. Backend performs deterministic validation:
   - manifest schema and expected outputs;
   - allowed path and filesystem audit;
   - code artifact presence and path/hash checks;
   - placeholder output heuristics;
   - manager brief presence.
6. Backend runs an optional read-only ReviewerWorker using the same Manager AI config. The reviewer can inspect allowed files through tools and returns only a validation verdict and issues. It does not mutate graph state.
7. If validation/reviewer fails, the run fails with structured errors in `manager_brief.json`.
8. If validation/reviewer passes, the run enters `success`/`needs_review`.
9. Manager review uses `review_context.json`, `manager_brief.json`, and assets to update cards/assets/claims.

## Executor Contract

The contract should be enforced by schema and validators, not strong prompt instructions.

Executor output requirements:

- `runs/{run_id}/manifest.json`
- `runs/{run_id}/manager_brief.json` or `BP_EVENT final_report`
- Reproducible code artifact under `scripts/generated/{run_id}/`
- Declared outputs matching `task_packet.expected_outputs`

Manifest evidence fields:

- `code_artifacts`: executable scripts or notebooks preserved for review
- `validation_evidence`: optional executor-provided evidence such as input hashes, row counts, command stdout paths, or tool versions

The executor may report progress through `BP_EVENT` stdout. Future plugin/tool calls should map to the same brief fields and must not bypass backend validation.

## Validator

The deterministic validator is mandatory and runs after normal manifest validation.

Checks:

- Every code artifact exists, is inside `scripts/generated/{run_id}/` or `runs/{run_id}/`, and matches declared hash when present.
- At least one code artifact is required when the manifest declares created assets.
- Created output files are non-empty.
- Table outputs are not obvious toy placeholders such as `feature score term_1 term_2`.
- `manager_brief.json` is present or executor emitted a final report.

## Reviewer

The reviewer is an optional read-only agent worker using the same DeepSeek settings as Manager AI.

Inputs:

- task packet
- manifest
- deterministic validator issues
- read-only tools for allowed task, manifest, code, input, and output files

Output:

- `verdict`: `pass`, `warn`, or `fail`
- `summary`
- `issues`
- `repair_hints`

The reviewer must not propose graph mutations. Its tools are limited to listing reviewable files, reading previews, inspecting tables, and compiling Python code artifacts. Its result is written into `manager_brief.json` and used only to decide whether the run can enter Manager review.

## Configuration

`BLUEPRINT_PI_COMMAND` is required for the `pi` worker. The built-in DeepSeek JSON executor is not a valid default for real runs.

Example command templates:

```bash
BLUEPRINT_PI_COMMAND="pi --no-session -p @{executor_prompt_path}"
BLUEPRINT_PI_COMMAND="bash /absolute/path/to/pi-wrapper.sh {executor_prompt_path}"
```

Available placeholders:

- `{project_root}`
- `{task_packet_path}`
- `{run_dir}`
- `{result_dir}`
- `{manifest_path}`
- `{manager_brief_path}`
- `{executor_prompt_path}`
- `{executor_brief_path}`
- `{adapter_contract_path}`

## Current Implementation Scope

This iteration implements the external `pi` requirement, manifest evidence fields, deterministic validator, read-only DeepSeek ReviewerWorker, and worker-service integration. It does not yet implement a native Pi plugin protocol; stdout `BP_EVENT` and files remain the transport.

For Pi CLI 0.75.x, `-p/--print` is the non-interactive mode and `@file` injects a prompt file. A wrapper is still recommended when Pi needs custom `PI_CODING_AGENT_SESSION_DIR`, model flags, extension loading, or environment setup.
