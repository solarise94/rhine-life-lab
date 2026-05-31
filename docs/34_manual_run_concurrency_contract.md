# Manual Run Concurrency Contract

Status: design note.

## Problem

Manual card execution, Manager-triggered execution, and auto/workboard execution currently share the same lower-level `WorkerService.start_run` path, but they have different product semantics:

- manual UI/API starts are user-directed and should be able to start several independent cards;
- Manager `start_card_run` starts background work and then hits `async_boundary` to end the current Manager turn;
- auto/workboard execution should submit ready work in controlled batches and then let the workboard signaler decide the next turn.

This has created confusing behavior and confusing copy. A response such as "only one background task can run" can be correct for a Manager turn boundary, but incorrect for manual execution when the executor is sandboxed and project concurrency is available.

The fix is not to make every path behave the same. The fix is to make each path expose its own limit clearly.

The same applies to time limits: timeout should be a system-level runtime setting, not an ad hoc task-level constant hidden in a prompt or card payload.

## Current Runtime Facts

The intended default runtime path is sandboxed execution:

```text
BLUEPRINT_EXECUTOR_SANDBOX_MODE=bwrap
BLUEPRINT_EXECUTOR_MAX_CONCURRENT_RUNS=3
```

In that path, the backend should allow up to `executor_max_concurrent_runs` active runs per project, with a per-card lock so the same card cannot be started twice.

If an executor adapter is not using the sandbox path, the backend falls back to a project-wide execution lock. That path intentionally allows only one active run per project because non-sandboxed workers do not have the same isolation guarantees.

## Target Semantics

### Manual UI/API Start

Manual run start means the user intentionally clicked or called a direct run endpoint.

Rules:

- allow several different cards to run at the same time, up to the project concurrency cap;
- reject a second run for the same card while that card already has an active run;
- reject additional project runs when the project concurrency cap is full;
- if the selected executor is non-sandboxed, reject any second project run until the first one finishes;
- do not apply `async_boundary` semantics to manual UI starts;
- emit normal project events such as `run_created` and `run_status_changed` so the frontend can refresh card status.

Manual UI copy should never say the system only supports one background task unless the actual selected execution path is non-sandboxed.

### Manager Direct Start

Manager `start_card_run` and `rerun_card` are conversation tools, not manual UI controls.

Rules:

- the backend may start the run using the same execution guard as manual start;
- after a successful background start, the tool response should set `async_boundary=true`;
- Manager must stop the current turn and wait for a future workboard/event signal;
- this is a turn-control limit, not a project concurrency limit;
- Manager-facing summaries should say the run was started and the Manager yielded, not that the project can only run one task.

If Manager needs to start multiple independent cards, it should use the workboard batch path instead of repeatedly calling `start_card_run` in the same turn.

### Auto/Workboard Batch Start

Auto execution should be driven by the workboard frontier.

Rules:

- `ready_to_start` is derived by the backend from card and asset dependencies;
- Manager may promote/claim only backend-derived ready items inside the active session scope;
- batch submission should start as many claimed ready items as the available concurrency slots allow;
- items that are no longer ready or cannot get a slot remain on the workboard for a later turn;
- if at least one item starts, the Manager turn should hit `async_boundary`;
- blocked/leftover items in the response are informational only; the workboard remains the source of truth.

This lets auto mode run independent branches in parallel without letting Manager invent work or ignore dependency order.

## Backend Repair Plan

### 0. Add A System Timeout Setting

The executor and backend should expose a single system-level timeout source for background runs.

Suggested shape:

```text
executor_timeout_seconds
```

Rules:

- it lives in backend/runtime config, not in the card payload;
- it applies to the execution supervisor and any wrapper that enforces process lifetime;
- it can be overridden by deploy config or environment, but it should have one authoritative default;
- the timeout reason reported to the user should distinguish "system timeout" from "task logic failure".

That lets operators tune long-running ML or R jobs without editing every task contract.

### 1. Return Structured Execution-Guard Blocks

`WorkerService._acquire_execution_guard` should return a structured block reason instead of a generic `None`.

Suggested block codes:

```text
same_card_already_running
project_concurrency_limit_reached
non_sandbox_project_run_in_progress
executor_not_configured
```

Suggested response fields for HTTP 409 and Manager tool failures:

```json
{
  "error_code": "project_concurrency_limit_reached",
  "message": "Project concurrency limit reached.",
  "card_id": "card",
  "sandboxed": true,
  "max_concurrent_runs": 3,
  "active_run_ids": ["run_a", "run_b", "run_c"],
  "active_card_ids": ["card_a", "card_b", "card_c"]
}
```

This prevents the UI and Manager from treating all 409s as "one task only".

### 2. Preserve The Existing Execution Guard

Do not remove the current isolation rules.

Keep:

- non-sandboxed project-wide lock;
- sandboxed per-card lock;
- sandboxed project semaphore.

The change is in the diagnostics and caller behavior, not in the core safety rule.

### 3. Count Active Runs From Durable State

The block response should include active runs from graph/run state, not only in-memory semaphore state.

This matters after backend restart or partial failure. If the semaphore and graph disagree, the response should make the discrepancy visible through debug fields or logs.

## Frontend Repair Plan

### 1. Do Not Globally Disable Manual Start For Running Projects

The card start button should be disabled only when:

- the card itself already has an active run;
- auto mode owns the project mutation surface;
- the selected executor/profile is unavailable;
- the backend returned a precise concurrency block for that card/project.

Independent planned cards should remain startable while other cards run, as long as there is project capacity.

### 2. Show Concurrency State Explicitly

The UI should show a compact run capacity indicator when runs are active:

```text
Running 2 / 3
```

If the executor is non-sandboxed:

```text
Running 1 / 1 (non-sandboxed executor)
```

This makes the one-task path explainable instead of surprising.

### 3. Refresh From Project Events

Starting a run should update the UI through both:

- mutation success refresh;
- project SSE events such as `run_created` and `run_status_changed`.

Manager text output should not be treated as the source of run status. If a Manager tool says a run started but the UI does not update, the bug is in event delivery/refetch timing, not in Manager prose.

## Manager-Agent Repair Plan

### 1. Stop Saying "Only One Task" From Async Boundary

Manager prompts and tool summaries should distinguish:

```text
The run started; I am yielding this turn and waiting for background events.
```

from:

```text
The project concurrency limit is full.
```

The first is turn control. The second is executor scheduling.

### 2. Prefer Workboard Batch For Parallel Starts

When auto/workboard mode has several independent ready items, Manager should:

1. inspect the ready frontier;
2. claim/promote allowed ready items inside session scope;
3. submit them through the workboard batch tool;
4. stop on `async_boundary`.

Manager should not emulate parallelism by repeatedly calling `start_card_run` until the async boundary rejects more tool calls.

This is not only prompt guidance. The first successful background start must still trigger `async_boundary`, and any later same-turn call should be blocked by the tool/session boundary or by the concrete execution guard. If the Manager ignores the guidance and retries anyway, the backend block reason should be enough to stop the loop:

- `project_concurrency_limit_reached` when the project is full;
- `same_card_already_running` when the same card is targeted again;
- `non_sandbox_project_run_in_progress` when a non-sandbox executor is already busy.

### 3. Surface Precise Backend Block Reasons

Tool reports should preserve:

- `error_code`;
- `max_concurrent_runs`;
- `active_run_ids`;
- `active_card_ids`;
- `sandboxed`;
- `blocked_by_card_ids` when dependency frontier blocks the start.

This lets Manager decide whether to wait, repair dependencies, or tell the user a selected executor cannot run concurrently.

For the non-sandbox path, the response should include the current `active_card_ids` so Manager can tell the user exactly which card is occupying the single slot instead of saying only that the project is busy.

## Workboard Repair Plan

The workboard should separate dependency readiness from executor capacity.

Suggested lanes:

- `ready_to_start`: dependency frontier says the card can run;
- `todo`: selected by the current auto session for execution;
- `running`: submitted and active;
- `waiting_capacity`: dependency-ready but not submitted because concurrency is full;
- `needs_manager`: failed, blocked, or needs repair;
- `completed`: terminal and should be summarized or chained.

`waiting_capacity` should not wake Manager repeatedly. It becomes actionable when capacity frees and the board reevaluation can promote it back to startable work.

Capacity release should be explicit: a `running` task reaches a terminal state, the backend calls `notify_background_task_terminal`, and the workboard reevaluation runs again. At that point any `waiting_capacity` item that is still dependency-ready and has an available slot should be promoted back to `ready_to_start`.

## Acceptance Checks

Minimum checks before considering this fixed:

1. With `bwrap` and `executor_max_concurrent_runs=3`, manually start two independent planned cards and confirm both reach `running`.
2. Start the same card twice and confirm the second request returns `same_card_already_running`.
3. Start four independent cards with max concurrency 3 and confirm the fourth returns `project_concurrency_limit_reached`.
4. Switch to a non-sandboxed test adapter and confirm the second project run returns `non_sandbox_project_run_in_progress`.
5. Confirm Manager `start_card_run` still hits `async_boundary` after one successful direct start.
6. Confirm workboard batch can start multiple independent ready items in one submission.
7. Confirm dependent cards stay out of the start batch until upstream assets are done.
8. Confirm frontend card status refreshes from `run_created` / `run_status_changed` without requiring a page reload.

## Non-Goals

This document does not require:

- a new global task queue;
- a distributed scheduler;
- changing the default `bwrap` requirement;
- allowing multiple active runs for the same card;
- allowing Manager to invent todo items outside the backend-derived workboard frontier.
