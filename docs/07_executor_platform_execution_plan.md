# Executor Platform Execution Plan

## Purpose

This document turns the recent executor-platform discussion into an implementation plan for evolving the current demo-capable executor chain into a reliable real executor platform.

It covers three things together:

1. What has already landed.
2. What phases are still pending.
3. The newly discussed executor context, skill/profile injection, and reporting-to-manager protocol.

## Scope

In scope:

- run start gate and task packet contract
- run/card state machine
- executor runtime lifecycle and reconcile
- manifest validation and post-run audit
- cleanup, cancel, rerun semantics
- git persistence strategy for run lifecycle
- executor context and skill/profile contract
- executor-to-manager reporting protocol
- real worker adapter contract for opencode/codex/pi/claude_code

Out of scope for this document:

- Manager planning quality
- frontend visual redesign beyond minimal executor workflow support
- cross-project distributed scheduling

## Current Baseline

As of the latest backend/frontend changes, the platform is no longer in the original unsafe demo state.

Already landed:

- `start_run` is gated by work-order `can_start`; blocked cards return `409` with block details.
- task packet input assets now come from `card.inputs[].asset_id` plus `linked_assets`.
- task packet now includes `card_inputs`, `card_outputs`, and `run_context`.
- default worker selection prefers `opencode` when configured, otherwise falls back safely.
- `run_id` is generated with short UUIDs instead of `len(runs)+1`.
- manifest validation is stricter:
  - validates `inputs_used` coverage
  - validates output role/type against expected outputs
  - detects duplicate output paths
  - detects collisions with existing valid outputs
- post-run filesystem audit is in place.
- backend startup reconciles hanging `queued` and `running` runs.
- frontend now handles blocked starts, pending approval runs, and latest event notice on start.

Still missing:

- frontend run controls and structured reporting panels are still minimal
- archived metadata is retained, but UI-facing archive/recovery flows still need polish
- vendor-specific real adapter prompts still need per-runtime tuning on top of the shared contract

## Target State

The target platform is:

- deterministic to start
- explicit in state transitions
- durable across backend restarts
- strict on filesystem boundaries
- capable of passing executor context to different agent runtimes
- capable of receiving structured progress, issue, and final reports from executors
- able to cleanly cancel, clean up, rerun, and review runs

## Phase Overview

### Phase 1: Do Not Run the Wrong Thing

Status: mostly completed

Goal:

- prevent incorrect downstream execution
- give executors a minimally usable task contract
- enforce bounded write scope

Landed items:

- work-order gate before `start_run`
- task packet reads `card.inputs[].asset_id`
- task packet includes `card_inputs`, `card_outputs`, `run_context`
- opencode-preferred worker selection
- non-fragile `run_id`
- stricter manifest validation
- post-run filesystem audit
- restart reconcile for hanging runs

Residual follow-ups in this phase:

- add explicit API payload shape for `409 blocked` responses to backend schema/docs
- expose latest run event and block details more clearly in frontend task details
- add backend tests for manifest collision and filesystem-audit violation cases

Acceptance criteria:

- downstream card cannot start while upstream acceptance requirements are unmet
- task packet always lists usable input assets for executor consumption
- executor cannot silently succeed if it writes outside declared paths
- backend restart cannot leave `running` or `queued` runs in limbo indefinitely

### Phase 2: Formal State Machine and Control APIs

Status: completed

Goal:

- make run and card lifecycle explicit
- support safe operator actions after failure, review, or interruption

Target run state machine:

- `queued -> needs_approval? -> running -> success | failed | cancelled -> reviewed`

Target card state machine:

- `planned -> running -> needs_review -> accepted | failed | rejected`

Required APIs:

- `POST /projects/{project_id}/runs/{run_id}/cancel`
- `POST /projects/{project_id}/runs/{run_id}/cleanup`
- `POST /projects/{project_id}/cards/{card_id}/reset-run-state`
- `POST /projects/{project_id}/cards/{card_id}/rerun`

Behavior requirements:

- cancel only applies to `queued`, `needs_approval`, `running`
- cleanup only applies to non-running terminal runs
- reset card state must move `failed` or `needs_review` cards back to `planned`
- rerun must create a new run, never mutate the old run in place

Data impacts:

- run record may need `archived_at`, `cancel_reason`, `cleanup_status`
- card may need `last_run_id` or equivalent helper field if UI needs explicit current run

Acceptance criteria:

- every control action has clear allowed source states
- repeated API calls are idempotent or safely rejected
- UI no longer relies on manual file deletion to recover from demo runs

Landed:

- `cancel_run`, `cleanup_run`, `reset_card_run_state`, `rerun_card` APIs are implemented
- run metadata includes `cancel_reason`, `archived_at`, `cleanup_status`, `needs_manager_attention`
- task workspace UI now exposes cancel, cleanup, reset, and rerun controls
- task detail panel now shows work-order blockers, latest run, and executor context summary

### Phase 3: Cleanup and Git Lifecycle Strategy

Status: completed

Goal:

- keep project git history readable
- keep failed or abandoned runs from polluting materialized history

Commit classes:

- blueprint edit commit
- run lifecycle commit
- review/materialization commit

Recommended commit policy:

- do not commit on run creation
- commit on terminal lifecycle transitions when state is useful for audit
- commit on accepted review/materialization
- commit on cleanup

Cleanup behavior:

- remove `runs/{run_id}`
- remove `results/{card_id}/{run_id}`
- remove or archive run from `graph.runs`
- remove run from `card.linked_runs` or mark archived explicitly
- clean candidate assets, claims, report items, and transient references created by this run

Suggested cleanup commit title:

- `Cleanup run {run_id}`

Open design question:

- whether to hard-delete old run records or keep archived run metadata in `graph.runs`

Recommended direction:

- keep archived metadata for auditability, but remove heavy filesystem artifacts

Acceptance criteria:

- cleanup produces a stable graph and no dangling candidate outputs
- git history no longer fills with transient mid-run commits

Landed:

- cleanup removes run/result directories and strips candidate assets, claims, and report items produced by the run
- cleanup leaves archived run metadata in `graph.runs`
- git commit policy is now concentrated on terminal lifecycle stages, review/materialization, and cleanup

### Phase 4: Executor Context Contract

Status: completed

Goal:

- allow a card to pass more than task data
- give executors explicit runtime profile, skill, and reference context

Problem today:

- executors receive task context
- executors do not receive structured skill/profile/tooling context
- there is no card-level contract for runtime persona or helper references

Add to `TaskPacket`:

- `executor_profile`
- `skills`
- `instruction_blocks`
- `references`
- `tool_policy`
- `runtime_bindings`

Proposed shape:

```json
{
  "executor_context": {
    "executor_profile": "bioinfo_r_worker",
    "skills": ["deseq2", "gsea", "report_markdown"],
    "instruction_blocks": [
      "Prefer reproducible scripts over ad-hoc shell pipelines.",
      "Summarize biological findings conservatively."
    ],
    "references": [
      {"type": "file", "path": "configs/params.yaml"},
      {"type": "file", "path": "scripts/curated/deseq2_template.R"}
    ],
    "tool_policy": {
      "network": "prompt",
      "python": true,
      "rscript": true,
      "git_write": false
    },
    "runtime_bindings": {
      "conda_env": "rnaseq",
      "working_dir": "."
    }
  }
}
```

Important constraint:

- this is platform contract, not vendor-specific agent glue
- avoid binding the data model to a single tool runtime or product SDK

Acceptance criteria:

- a card can explicitly declare runtime profile and allowed helper context
- different adapters can consume the same context contract consistently

Landed:

- `TaskPacket` carries `executor_context`
- cards can store explicit `executor_context`
- default executor context is synthesized from card/module metadata when a card-level override is absent

### Phase 5: Executor-to-Manager Reporting Protocol

Status: completed

Goal:

- allow executors to report progress, issues, and final summaries in a structured way
- keep manager as decision-maker rather than letting executors mutate blueprint state directly

Important design rule:

- do not give executors full Manager tool-call authority
- give executors a restricted reporting protocol only

Reporting message types:

- `progress_update`
- `issue_report`
- `final_report`

Recommended transport:

- stdout structured JSON lines with a stable prefix, or
- `runs/{run_id}/manager_updates.jsonl`

Preferred initial implementation:

- stdout lines prefixed by `BP_EVENT `

Example:

```json
{
  "type": "progress_update",
  "stage": "deseq2",
  "progress": 45,
  "message": "Count matrix loaded, running normalization.",
  "artifacts": ["runs/run_xxx/qc_summary.json"]
}
```

```json
{
  "type": "issue_report",
  "severity": "high",
  "needs_manager": true,
  "message": "Sample metadata is missing condition column.",
  "suggested_actions": ["abort_run", "fallback_to_demo", "ask_user_for_mapping"]
}
```

```json
{
  "type": "final_report",
  "summary": "DE analysis completed.",
  "key_findings": ["1324 significant genes", "IFN pathway enriched"],
  "warnings": ["Batch effect remains possible"]
}
```

Backend responsibilities:

- parse structured executor events
- persist them as typed run events
- optionally materialize a `manager_brief.json`
- map `final_report.summary` into run summary if present
- map `needs_manager=true` into explicit blocked or attention-needed status

Suggested future run event types:

- `executor_progress`
- `executor_issue`
- `executor_final_report`
- `run_blocked_on_manager`

Acceptance criteria:

- manager can see structured progress rather than raw stdout only
- executor can raise decision-needed issues without mutating graph state
- final summary is available before review acceptance

Landed:

- stdout `BP_EVENT` parsing is implemented in the worker service
- structured events are materialized as `executor_progress`, `executor_issue`, `executor_final_report`, and `run_blocked_on_manager`
- `manager_brief.json` is generated and used to seed run summary / review context
- task workspace now surfaces structured progress, issues, and final report sections separately from raw event logs

### Phase 6: Real Adapter Contract

Status: in progress

Goal:

- unify the execution contract across `opencode`, `codex`, `pi`, `claude_code`, and demo shell worker

Shared adapter contract:

- input:
  - `task_packet.json`
  - project root as cwd
  - declared env vars
- output:
  - `manifest.json`
  - optional structured reporting events
  - optional generated scripts under allowed paths
- policy:
  - write only in `runs/{run_id}/`, `results/{card_id}/{run_id}/`, `scripts/generated/`
  - no graph writes
  - no `.git/` writes
  - optional network only under policy

Adapter responsibilities:

- translate `TaskPacket` into runtime-specific prompt or command line
- preserve platform event contract
- guarantee `manifest.json` emission or terminal failure

Post-run responsibilities:

- manifest validation
- filesystem audit
- review context assembly

Acceptance criteria:

- all supported workers obey the same input/output contract
- switching worker type does not change review or audit semantics

Landed:

- `opencode`, `pi`, and `claude_code` now launch through a shared `agent_cli_executor` wrapper
- command-template adapters now emit shared contract artifacts:
  - `runs/{run_id}/executor_brief.md`
  - `runs/{run_id}/executor_prompt.md`
  - `runs/{run_id}/adapter_contract.json`
- command-template adapters now expose standard env vars for:
  - task packet path
  - manifest path
  - result dir
  - allowed / readonly / forbidden paths
  - executor profile and skills
  - manager reporting stdout prefix
- command-template adapters now enforce adapter-level network policy:
  - real agent adapters (`opencode`, `codex`, `pi`, `claude_code`) are blocked when `tool_policy.network=deny`
  - runtime network approval is only requested when the adapter declares network access and the card policy is `prompt`
- synthesized default executor context now uses `network=allow` for real agent adapters and keeps `prompt` for local/demo shell execution
- execution files panel now exposes `adapter_contract`, `executor_brief`, and `filesystem_audit`
- project snapshot now exposes `worker_capabilities`, including whether each adapter is configured, which launch-template setting it uses, and recommended wrapper-style examples for real agent CLIs
- task detail now surfaces configured worker adapters so operators can confirm whether `opencode`, `pi`, or `claude_code` is actually available before starting a run

Still open in this phase:

- tune local wrapper scripts or exact provider flags for each installation of `opencode`, `codex`, `pi`, and `claude_code`
- periodically review provider launch examples as upstream CLIs change behavior

## Recommended Execution Order

1. Finish residual Phase 1 follow-ups.
2. Implement Phase 2 control APIs and explicit state guards.
3. Implement Phase 3 cleanup and git policy.
4. Implement Phase 4 executor context contract.
5. Implement Phase 5 reporting protocol.
6. Implement Phase 6 real adapter normalization.

Reasoning:

- lifecycle safety and cleanup must come before richer runtime capability
- otherwise more capable executors will only make current state ambiguity harder to debug

## Concrete Backlog

### Backend

- add run control endpoints
- extend run/card models for cancel/cleanup/archive metadata
- add cleanup service for run artifact removal
- add `executor_context` to `TaskPacket`
- add structured executor event parser in worker service
- add `manager_brief.json` materialization
- add backend tests for cancel/cleanup/reporting/audit edge cases

### Frontend

- show blocked reasons inline in run controls
- show current run state and control actions per card
- show structured progress/issues/final-report separately from raw logs
- surface manager-attention-needed runs clearly
- improve mobile task view so detail and structured report sections are as visible as desktop

### Documentation

- document task packet schema changes
- document executor event schema
- document operator recovery flows: cancel, cleanup, reset, rerun

## Risks

- allowing executor-originated graph mutations will blur accountability
- cleanup without asset lineage rules can leave dangling report/claim references
- adapter-specific prompt glue can fragment behavior if platform contract is weak
- overusing git commits during runtime can preserve noisy intermediate state
- runtime-specific command templates may still diverge in behavior even with a shared adapter contract if prompt glue is not reviewed periodically

## Guardrails

- manager remains the only authority for blueprint mutation and review acceptance
- executors may report, not decide
- every filesystem write must remain auditable
- every run transition must be explicit and validated against source state

## Definition of Done

This executor-platform rewrite is complete when:

- cards cannot be started, rerun, or reset incorrectly
- runs can be cancelled, cleaned up, reconciled, and reviewed deterministically
- executors receive both task context and runtime context
- executors can report structured progress and final summary back to manager
- all supported adapters obey one shared execution contract
- cleanup and review leave graph, files, and git history in a coherent state
