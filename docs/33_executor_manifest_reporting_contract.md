# Executor Manifest Reporting Contract

Status: design note.

## Problem

Card executors currently have too many ways to report overlapping information:

- `BP_EVENT`;
- `manager_brief.json`;
- dependency helper output;
- `manifest.json`.

This creates unnecessary executor burden and makes the backend decide which source is authoritative after the fact. The executor should not have to submit the same result through multiple surfaces just so Manager can get a compact summary.

The desired direction is simpler:

- `BP_EVENT` is removed from the target executor reporting contract;
- executor has exactly two terminal outcomes: `report_complete` and `report_fail`;
- `report_complete` submits the final manifest and enters validation/review;
- `report_fail` submits a compact failure reason and skips validation/review;
- Manager receives a compact backend-owned projection derived from `manifest.json`, reviewer output, and workboard state;
- dependency failures are one `report_fail.reason_code`, not a separate executor-facing report concept;
- executor progress is backend-observed state, not an executor reporting API.

## Target Contract

Executor-facing contract should have two terminal report commands:

1. `report_complete`
2. `report_fail`

The executor chooses exactly one terminal report command for a run.

`report_complete` means:

- the executor believes the card contract has been completed;
- the executor submits a manifest;
- backend runs manifest validation and reviewer;
- acceptance still depends on backend validation/review.

`report_fail` means:

- the executor cannot complete the card contract;
- the executor submits a compact structured failure report;
- backend marks the run failed;
- backend skips manifest validation and reviewer;
- workboard derives `needs_manager` from the failure reason.

`manager_brief` should remain only as a backend-owned compact projection/compatibility port. It must not be exposed as an executor tool or required executor output.

`report_dependency_issue.py` should be replaced or wrapped by `report_fail(reason_code=runtime_dependency_missing)`.

There is no target third executor reporting surface. In particular:

- no `BP_EVENT` progress protocol;
- no separate dependency issue protocol;
- no separate manager brief submission.

## BP_EVENT

`BP_EVENT` should be removed from the target contract.

Reasons:

- Executor progress is not currently a first-class UI/workboard surface.
- Reviewer progress is backend-owned and should use backend run/project events, not executor stdout.
- Terminal result truth belongs in `manifest.json`.
- Dependency repair truth belongs in `report_fail` details and Manager/workboard repair flow.
- Keeping `BP_EVENT` creates a third reporting surface with weak product value.

Near-term compatibility:

- existing `BP_EVENT` lines may be preserved in transcript/logs;
- backend may temporarily ignore or best-effort parse old events;
- executor prompts and adapter contracts should stop advertising `BP_EVENT`;
- new executors should not rely on `BP_EVENT` for acceptance, progress, warnings, or final summaries.

## Progress Visibility

Executor progress still matters, but `BP_EVENT` should not be the target mechanism.

Today, old executor progress events may be recorded as run events and may update `card.progress_note`, but they are not clearly represented in the UI/workboard model.

That should change.

Target progress should come from backend-owned sources:

- process lifecycle: queued, launching, running, reviewing, terminal;
- wrapper observations: command started, manifest candidate written, manifest promoted, validation started;
- optional future bounded observation/timer status.

Progress should appear in:

- run detail timeline;
- card run status summary;
- workboard `running` lane item details.

Suggested running workboard projection:

```json
{
  "lane": "running",
  "kind": "card_run",
  "card_id": "card_x",
  "run_id": "run_y",
  "progress": {
    "stage": "qc",
    "percent": 40,
    "message": "QC finished.",
    "updated_at": "..."
  }
}
```

This keeps progress user-visible without waking Manager or treating progress as actionable work.

Progress does not create a workboard signal. Only terminal facts or actionable board state should signal Manager.

## report_complete

`report_complete` is the successful terminal submission path.

It should accept the final manifest payload or a path to a manifest candidate.

Target command shape:

```text
report_complete --manifest runs/run_y/manifest.candidate.json
```

Equivalent JSON payload:

```json
{
  "schema_version": "executor_completion.v1",
  "candidate_manifest_path": "runs/run_y/manifest.candidate.json"
}
```

The completion report itself should be tiny. It should not duplicate manifest contents, should not include `status`, and should not include a Manager brief. The submitted manifest is the evidence bundle; the completion report is only the terminal handoff.

The manifest contains two categories of information:

- audit truth: files and generated code;
- Manager projection source: concise summary and warnings that backend can compact before sending to Manager.

The executor submits one manifest through `report_complete`. The backend decides how much of it to show to Manager.

Suggested target shape:

```json
{
  "schema_version": "executor_manifest.v2",
  "summary": "Generated QC report and normalized expression matrix.",
  "created_assets": [
    {
      "role": "qc_report",
      "path": "results/card_x/run_y/qc_report.html",
      "artifact_class": "document",
      "format": "html",
      "description": "QC report for sample and gene filtering."
    }
  ],
  "code_artifacts": [
    {
      "path": "scripts/generated/run_y/qc_pipeline.py",
      "language": "python",
      "purpose": "Reproducible QC and normalization script.",
      "sha256": "..."
    }
  ],
  "manager_report": {
    "summary": "QC completed and outputs are ready for review.",
    "warnings": []
  }
}
```

`run_id` and `status` are intentionally absent. The backend already knows the current run from the execution context, and calling `report_complete` means the executor is claiming the card work is complete enough to submit. The final backend run state is still determined by manifest validation and reviewer outcome.

`report_complete` validation rules:

- accept `candidate_manifest_path`;
- require the manifest to resolve inside the current run directory;
- bind the report to the current run from backend context;
- if legacy `manifest.run_id` is present, require it to match the current run;
- reject path traversal and writes outside allowed result/code paths;
- promote a valid candidate to canonical `runs/{run_id}/manifest.json`;
- persist a small `executor_completion.json` marker for audit/debugging;
- append a backend-owned run event such as `executor_complete_reported`;
- then run normal manifest validation and reviewer.

`report_complete` repair loop guard:

- keep a per-run `report_complete` validation failure count;
- default budget: 3 failed `report_complete` submissions, matching the current manifest repair attempt scale;
- before the final allowed failure, return structured validation errors and let the executor repair the candidate manifest;
- near the budget limit, include guidance that the executor should call `report_fail` if it cannot produce a valid manifest;
- when the budget is exhausted, backend automatically applies `report_fail(reason_code=contract_violation)` semantics and marks the run failed;
- after budget exhaustion, no additional executor response is required to make the run terminal.

Suggested exhausted response:

```json
{
  "ok": false,
  "error_code": "report_complete_repair_budget_exhausted",
  "summary": "Manifest completion failed validation too many times.",
  "suggested_action": "stop",
  "failure_reason_code": "contract_violation",
  "terminal": true,
  "validation_errors": []
}
```

Suggested forced terminal behavior:

```text
invalid report_complete attempts exhausted
  -> persist executor_failure.json
  -> reason_code=contract_violation
  -> mark run failed
  -> derive workboard needs_manager
```

Candidate manifest path rules:

- relative paths are resolved from the current run directory;
- absolute paths are allowed only if their resolved real path is inside the current run directory;
- symlinks are resolved before validation and rejected if they escape the current run directory;
- directories are rejected; the candidate path must be a regular JSON file;
- backend writes/promotes only the canonical `runs/{run_id}/manifest.json`.

Non-interactive CLI adapters may still implement this as a helper script plus process-exit convention during migration. Native/tool-aware adapters should treat `report_complete` as the terminal tool call for the run.

After `report_complete`, backend flow is:

```text
executor report_complete
  -> persist executor_completion.json
  -> write/promote manifest.json
  -> validate manifest
  -> run reviewer
  -> accept/reviewed or failed/needs_manager
```

## Terminal State Flow

The two terminal report commands map to different backend paths:

```text
report_complete
  -> executor-complete submission
  -> manifest validation
  -> reviewer
  -> accepted/reviewed or failed/needs_manager

report_fail
  -> executor-failed submission
  -> persist failure reason
  -> skip manifest validation
  -> skip reviewer
  -> failed/needs_manager
```

This distinction is important: `report_fail` is not a low-quality manifest. It is an explicit statement that the executor cannot complete the card without Manager intervention.

## Terminal Idempotency

The first accepted terminal report wins.

After a terminal report is accepted, or after the run becomes terminal through budget exhaustion, cancellation, timeout failure, or process crash failure, later terminal report calls must not mutate run state.

Duplicate or late terminal calls should return:

```json
{
  "ok": false,
  "error_code": "run_already_terminal",
  "terminal_status": "failed"
}
```

Examples:

- `report_fail` followed by `report_complete`: reject `report_complete`;
- `report_complete` accepted for review followed by `report_fail`: reject `report_fail`;
- `report_fail` twice: reject the second call;
- budget-exhausted forced failure followed by `report_fail`: reject `report_fail`.

## No Terminal Report

If the executor process exits without calling `report_complete` or `report_fail`, backend/wrapper synthesizes a failure report.

Mapping:

- exit code `0` with no terminal report: `reason_code=contract_violation`;
- non-zero exit code with no terminal report: `reason_code=execution_error`;
- timeout with no terminal report: `reason_code=execution_error`;
- signal kill with no terminal report: `reason_code=execution_error`;
- missing or unreadable completion marker: `reason_code=contract_violation`.

Synthesized failures use the same terminal path as `report_fail`:

```text
no terminal report
  -> persist executor_failure.json
  -> persist terminal_report.json
  -> mark run failed
  -> skip manifest validation
  -> skip reviewer
  -> derive workboard needs_manager
```

## Command Behavior

The concrete adapter can expose these as helper scripts, local tools, or wrapper subcommands. The product contract is the same:

```text
report_complete --manifest runs/run_y/manifest.candidate.json
report_fail --reason-code runtime_dependency_missing --summary "Missing required R packages."
```

`report_complete` should be the normal successful terminal path. After it is called, the executor should not continue mutating outputs except through an explicit repair attempt controlled by the wrapper.

`report_fail` should be a terminal path. After it is called, the executor should stop. The helper may exit with a reserved non-zero code so the wrapper can stop the provider process, but backend must classify the run by the structured failure report rather than by the raw process exit code.

The failure payload is intentionally small. A dependency failure does not need a complete manifest and does not need `status`; it only needs the reason code, summary, and useful details.

## Adapter Contract Cleanup

The current adapter contract exposes legacy manifest path/status fields. The target contract should clean these up.

Remove from executor-facing adapter contract:

- `manifest_path`: canonical `manifest.json` is backend-owned. Executor should not write final manifest directly.
- `manifest_status_values`: executor does not submit `status`.
- `required_manifest_fields`: target schema should be expressed through the report/manifest schema, not a duplicated adapter list.

Compatibility-only:

- `manifest_candidate_path`: may remain temporarily as a default local file path for older non-interactive CLI wrappers.
- `BLUEPRINT_MANIFEST_CANDIDATE_PATH`: may remain temporarily as an environment hint for legacy executors.

Target behavior:

- executor writes a candidate manifest wherever the adapter allows;
- executor calls `report_complete --manifest <candidate-path>`;
- backend/wrapper validates and promotes the candidate to canonical `runs/{run_id}/manifest.json`;
- executor never writes canonical `manifest.json` directly in the target contract.

## report_fail

`report_fail` is the failed terminal submission path.

It should be small. The executor does not need to submit `status` because calling `report_fail` already determines the terminal state.

Required fields:

- `reason_code`
- `summary`

Optional field:

- `details`

Suggested target shape:

```json
{
  "schema_version": "executor_failure.v1",
  "reason_code": "runtime_dependency_missing",
  "summary": "Missing required R packages for enrichment analysis.",
  "details": {
    "ecosystem": "R",
    "missing_packages": ["clusterProfiler", "enrichplot"],
    "package_manager": "Bioconductor",
    "runtime": "R_env"
  }
}
```

After `report_fail`, backend flow is:

```text
executor report_fail
  -> persist executor_failure.json
  -> mark run failed
  -> skip manifest validation
  -> skip reviewer
  -> derive workboard needs_manager
```

Manager projection for `report_fail` comes directly from the failure report:

```json
{
  "summary": "Missing required R packages for enrichment analysis.",
  "reason_code": "runtime_dependency_missing",
  "details": {}
}
```

There is no `manifest.manager_report` on this path.

Initial `reason_code` values:

- `runtime_dependency_missing`
- `input_missing`
- `input_invalid`
- `permission_denied`
- `tool_unavailable`
- `execution_error`
- `contract_violation`
- `unknown`

`reason_code` is a backend-known enum. Unknown values should be normalized to `unknown`, preserved in `details.original_reason_code`, and logged as a contract warning.

Suggested `details` conventions:

- `runtime_dependency_missing`: `ecosystem`, `missing_packages`, `package_manager`, `runtime`
- `input_missing`: `missing_inputs`
- `input_invalid`: `input_id`, `expected`, `actual`
- `tool_unavailable`: `tool`, `runtime`
- `execution_error`: `command`, `exit_code`, `stderr_tail`
- `contract_violation`: `validation_errors`, `failed_report`, `attempt_count`

`details` remains free JSON so executors can report useful evidence without expanding the top-level schema.

If useful for debugging, `details.partial_code_artifacts` may list generated scripts that existed before failure. Backend may also derive partial code artifacts by scanning run-local generated-code paths. This remains diagnostic data only; it is not a successful result manifest.

## report_fail Workboard Mapping

`report_fail.reason_code` should map deterministically to workboard action data:

| reason_code | workboard kind | recommended_action |
| --- | --- | --- |
| `runtime_dependency_missing` | `runtime_dependency_missing` | `install_runtime_dependencies` |
| `input_missing` | `input_blocked` | `ask_user_or_inspect_inputs` |
| `input_invalid` | `input_blocked` | `ask_user_or_inspect_inputs` |
| `permission_denied` | `permission_blocked` | `request_permission_or_reconfigure` |
| `tool_unavailable` | `tool_unavailable` | `configure_runtime_or_tool` |
| `execution_error` | `execution_error` | `inspect_run_failure` |
| `contract_violation` | `contract_violation` | `repair_executor_contract_or_rerun` |
| `unknown` | `generic_run_failed` | `inspect_run_failure` |

All mappings produce failed/needs_manager unless the run was explicitly cancelled by the user.

## Field Ownership

Keep as core manifest fields:

- `schema_version`
- `summary`
- `created_assets`
- `code_artifacts`
- `manager_report`

Keep narrative fields under `manager_report`:

- `warnings`

Deprecate as required manifest fields:

- `run_id`
- `status`
- `validation_evidence`
- `metrics`
- top-level `key_findings`
- `recommended_graph_updates`
- top-level `warnings`

`validation_evidence` is removed from the target executor-facing contract. `metrics`, `key_findings`, and `recommended_graph_updates` are not executor-facing fields in the target contract. Executor warnings may remain under `manager_report.warnings`.

Do not expose these system-owned fields to the executor in the target contract:

- `run_id`: backend binds the report to the current run context;
- `status`: backend derives run status from `report_complete`, `report_fail`, validation, reviewer, and workboard state;
- `inputs_used`: backend derives declared input context from `task_packet.input_assets`; actual file-read usage may be derived from wrapper/runtime traces when available;
- `commands_executed`: backend derives command audit data from wrapper logs/traces.

These are not merely optional; they are backend-owned state or audit projections.

Do not expose these executor-authored top-level claim fields in the target manifest:

- `metrics`: if the user needs result interpretation, use the card result-explanation flow so Manager/reviewer reads actual outputs;
- `key_findings`: reviewer/backend should derive findings from outputs and scripts;
- `recommended_graph_updates`: executor must not propose graph mutations;
- top-level `warnings`: use `manager_report.warnings` instead.

## code_artifacts vs commands_executed

`code_artifacts` and `commands_executed` should not both be executor-authored manifest fields.

`code_artifacts` is delivery evidence. It tells backend/reviewer which generated scripts or notebooks belong to the result bundle:

```json
{
  "code_artifacts": [
    {
      "path": "scripts/generated/run_y/qc_pipeline.py",
      "language": "python",
      "purpose": "Reproducible QC and normalization script."
    }
  ]
}
```

This should remain executor-authored because the executor knows which generated code files are meaningful for review and reproducibility.

`commands_executed` is execution trace. It tells what actually ran:

```json
{
  "commands_executed": [
    "python scripts/generated/run_y/qc_pipeline.py"
  ]
}
```

This should be backend/wrapper-owned because the wrapper can capture command logs, provider attempts, repair attempts, exit codes, and sandbox traces. Executor-authored `commands_executed` is weak evidence and often duplicates `code_artifacts`.

Target behavior:

- executor manifest declares `code_artifacts`;
- executor prompt does not ask for `run_id`;
- executor prompt does not ask for `status`;
- executor prompt does not ask for `inputs_used`;
- executor prompt does not ask for `commands_executed`;
- adapter contract does not list `run_id` or `status` as manifest fields;
- adapter contract does not list `inputs_used` as a manifest field;
- adapter contract does not list `commands_executed` as a manifest field;
- manifest v2 executor-facing schema does not expose `run_id`, `status`, `inputs_used`, or `commands_executed`;
- wrapper/backend derives executed commands from `commands.log`, `agent_trace.json`, provider attempts, and sandbox/runtime traces;
- Manager/reviewer can see a compact executed-command projection, but it is not trusted just because the executor wrote it.

## Input Usage

Input usage should not be an executor-facing manifest/report field in the target contract.

Current backend already knows the declared input assets from `task_packet.input_assets`. That is the reliable source for what the executor was allowed and expected to use.

Actual input usage has two levels:

- declared input context: derived from `task_packet.input_assets`;
- observed input reads: future backend/wrapper trace data, if the runtime can record file reads safely.

Executor-authored input reports are weak evidence because the executor can omit, overstate, or misunderstand input usage. Reviewer should inspect the generated scripts, declared code artifacts, task packet inputs, output paths, and optional observed read traces instead.

Target behavior:

- no executor-authored `inputs_used`;
- no executor-authored `validation_evidence.input_conclusion`;
- no executor-authored `evidence.input_conclusion`;
- reviewer context gets declared inputs from backend and evaluates usage from scripts/traces.

## Manager Projection And manager_brief

Manager should not receive the full manifest by default.

Backend should create a compact projection:

```json
{
  "run_id": "run_y",
  "status": "success",
  "summary": "QC completed and outputs are ready for review.",
  "created_assets": [
    {
      "role": "qc_report",
      "path": "results/card_x/run_y/qc_report.html",
      "artifact_class": "document"
    }
  ],
  "warnings": []
}
```

Projection `status` is backend-derived from run/review state. It is not copied from executor-submitted manifest status.

Projection priority:

1. `manifest.manager_report.summary`
2. `manifest.summary`
3. reviewer summary
4. workboard failure/action summary

`manager_brief` can remain as the storage/API shape for this compact projection during migration, but it is backend-owned:

- executor prompt must not ask for `manager_brief.json`;
- adapter contract must not advertise `manager_brief_path`;
- executor tools must not include a manager brief write/update command;
- backend may materialize `manager_brief.json` from manifest/reviewer/workboard state for compatibility;
- Manager may consume the compact projection, not the full manifest.

## Dependency Failure Path

Missing runtime dependencies are not a separate executor report interface in the target contract.

Executors should call:

```text
report_fail(reason_code=runtime_dependency_missing)
```

when a required package, system tool, or runtime capability is unavailable.

Target dependency failure behavior:

- failure report records `reason_code=runtime_dependency_missing`;
- `details.missing_packages` is evidence and a suggested repair hint, not an executor-owned install command;
- Manager chooses the actual `install_runtime_dependencies` arguments from the failure details, card context, runtime binding, and package manager policy;
- workboard derives `needs_manager` from the terminal dependency issue;
- Manager sees dependency repair guidance through workboard, not through live stdout events;
- if Manager cannot safely identify package names, it should ask the user or mark the workboard item as blocked for user input.

## manager_brief.json Compatibility Port

`manager_brief.json` should become backend-owned compatibility data during migration, then be removed from the executor prompt and executor-facing adapter contract.

Near-term compatibility:

- backend may still read old `manager_brief.json`;
- backend may still merge reviewer output into a compatibility brief;
- backend may generate `manager_brief.json` from manifest/reviewer/workboard state;
- old executors that emit `BP_EVENT` may still be accepted if they also write a valid manifest;
- old executors that call `report_dependency_issue.py` may be mapped to `report_fail(reason_code=runtime_dependency_missing)`.
- legacy `manager_brief.json` is read-only compatibility input; it must not override terminal report state or manifest-derived projections.

Target state:

- executor prompt does not request `manager_brief.json`;
- adapter contract does not advertise `manager_brief_path`;
- executor tools do not expose a manager brief write/update interface;
- reviewer uses `manifest.json`, `task_packet.json`, run events, command logs, traces, and output previews;
- Manager gets a compact backend projection from manifest/reviewer/workboard state.

## Migration Plan

Migrate in phases. Do not remove legacy parsing before the new terminal tools and compatibility normalizer are working.

### Phase 0: Models And Compatibility Normalizer

Add new backend models:

- `ExecutorCompletionReport`
- `ExecutorFailureReport`
- `ExecutorManifestV2`
- internal normalized manifest/review context model

Compatibility rules:

- if `schema_version` exists and equals `executor_manifest.v2`, parse the target manifest path;
- otherwise parse the legacy `Manifest` path;
- accept legacy `Manifest` for existing executors;
- accept target `ExecutorManifestV2` for new executors;
- normalize both into one internal review context;
- if legacy manifest includes `run_id`, require it to match current run;
- ignore legacy `status` as executor-authored state;
- preserve legacy `validation_evidence.input_conclusion` only as compatibility data;
- ignore legacy executor-authored `commands_executed`, `inputs_used`, `metrics`, `key_findings`, and `recommended_graph_updates` for trusted backend decisions.

Schema generation must be updated after model changes.

### Phase 1: Executor-Facing Tools

Add target terminal helper:

```text
report_executor_result.py complete --manifest <candidate-manifest-path>
report_executor_result.py fail --reason-code <known-code> --summary <short-summary> [--details-json <json>]
```

The executor-facing adapter contract should advertise only the single helper with subcommands, not legacy side channels.

Remove from executor prompt / adapter contract:

- `BP_EVENT`;
- `manager_brief.json`;
- `manager_brief_path`;
- `report_dependency_issue.py` as a target interface;
- `manifest_path`;
- `manifest_status_values`;
- `required_manifest_fields`;
- executor-authored `run_id`;
- executor-authored `status`;
- executor-authored `inputs_used`;
- executor-authored `commands_executed`;
- executor-authored `validation_evidence`;
- executor-authored top-level `metrics`;
- executor-authored top-level `key_findings`;
- executor-authored `recommended_graph_updates`;
- executor-authored top-level `warnings`.

Keep temporarily for compatibility:

- `manifest_candidate_path` / `BLUEPRINT_MANIFEST_CANDIDATE_PATH`;
- old `report_dependency_issue.py`, internally mapped to `report_fail(reason_code=runtime_dependency_missing)`;
- old `BP_EVENT` parsing as best-effort transcript/run-event compatibility, but do not advertise it.

### Phase 2: WorkerService Terminal State Machine

Add one backend-owned terminal report registry per run.

Required behavior:

- first accepted terminal report wins;
- later terminal report calls return `run_already_terminal`;
- `report_complete` persists `executor_completion.json`;
- `report_fail` persists `executor_failure.json`;
- invalid `report_complete` increments a per-run failure count;
- completion failure budget defaults to 3;
- budget exhaustion automatically applies `report_fail(reason_code=contract_violation)` semantics;
- process exit with no terminal report synthesizes failure:
  - exit code `0`: `contract_violation`;
  - non-zero exit / timeout / signal kill: `execution_error`.

Registry persistence:

- persist the terminal report registry to `runs/{run_id}/terminal_report.json`;
- include current terminal kind, accepted timestamp, failure reason code, and terminal summary;
- reload the registry from disk on backend restart.

Promotion behavior:

- executor writes only candidate manifest;
- backend validates candidate path safety;
- backend promotes candidate to canonical `runs/{run_id}/manifest.json`;
- executor never writes canonical manifest in the target path.

### Phase 3: Manifest Validation And Reviewer Context

Update manifest validation to validate target manifest shape:

- required executor fields: `schema_version`, `summary`, `created_assets`, `code_artifacts`, `manager_report`;
- allowed executor warning field: `manager_report.warnings`;
- no executor-authored `run_id`, `status`, `inputs_used`, `commands_executed`, `metrics`, `key_findings`, `recommended_graph_updates`, or top-level `warnings`.

Reviewer context should be backend-derived:

- declared inputs from `task_packet.input_assets`;
- generated code from `code_artifacts`;
- created assets from `created_assets`;
- executed commands from `commands.log`, `agent_trace.json`, provider attempts, and sandbox/runtime traces;
- summary from `manager_report.summary`, then `manifest.summary`, then reviewer summary;
- warnings from `manager_report.warnings`;
- input usage from script/code inspection and optional runtime read traces, not executor self-report.

Remove executor-written `manager_brief.json` requirements:

- no missing-brief warning;
- no repair hint asking executor to write a brief;
- backend may generate `manager_brief.json` as a compatibility projection.

### Phase 4: Workboard And Manager Projection

Map `report_fail.reason_code` to workboard kind and recommended action using the table in this document.

Manager projection:

- completed path: compact projection from manifest/reviewer/workboard state;
- failed path: `report_fail.summary`, `reason_code`, and compact details;
- dependency path: `runtime_dependency_missing` details feed Manager's dependency-repair tool arguments as hints;
- no live progress wake from executor output.

Auto/workboard signal behavior remains:

- terminal run/job facts update the workboard;
- workboard signaler decides whether Manager should resume;
- progress-only observations update UI/running lane only.

### Phase 5: Tests And Compatibility Gates

Add tests for:

- `report_complete` valid candidate promotes to canonical manifest and enters reviewer;
- invalid `report_complete` returns structured validation errors;
- repeated invalid `report_complete` exhausts budget and forces `contract_violation` failure;
- terminal idempotency: late/duplicate terminal calls return `run_already_terminal`;
- no terminal report + exit code `0` synthesizes `contract_violation`;
- no terminal report + non-zero exit synthesizes `execution_error`;
- `report_fail(runtime_dependency_missing)` maps to workboard dependency action;
- unknown reason code normalizes to `unknown`;
- `report_complete` accepted once does not synthesize a failure on normal executor exit;
- `report_fail` accepted once does not mutate state on a later process exit;
- candidate manifest path traversal and symlink escape are rejected;
- `details.original_reason_code` is preserved when unknown reason codes are normalized;
- legacy `report_dependency_issue.py` maps to `report_fail`;
- old `BP_EVENT` is not advertised but remains harmless if emitted;
- executor prompt no longer mentions removed fields;
- adapter contract no longer exposes removed fields;
- reviewer context no longer requires executor-written `manager_brief.json`;
- backend-derived command audit appears from logs/traces;
- Manager projection for report_fail uses `report_fail.summary`.

Compatibility gates:

- keep legacy schema acceptance until all built-in adapters use terminal helpers;
- remove legacy prompt fields before removing legacy backend parsing;
- only remove legacy `BP_EVENT`/manager brief parsing after old runs are no longer relevant.

## Non-Goals

- Do not keep `BP_EVENT` as a supported executor reporting protocol.
- Do not let live progress wake Manager.
- Do not ask executor to submit the same final summary in multiple places.
- Do not put full manifest content into Manager context by default.
- Do not send `report_fail` through reviewer; failed terminal reports are Manager/workboard repair inputs.
