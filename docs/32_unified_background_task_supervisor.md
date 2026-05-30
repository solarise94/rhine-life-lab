# Unified Background Task Supervisor

Status: design note.

## Problem

Blueprint now has several kinds of real background work:

- card execution runs, backed by `WorkerService`;
- runtime dependency installs, backed by `RuntimeDependencyJobService`;
- future batch run-control operations, such as starting several cards together;
- future maintenance or cleanup jobs.

These tasks currently share product expectations but not a single backend abstraction:

- start work and return immediately;
- publish status and progress through project-state events;
- update a workboard when terminal states need narrative or repair;
- signal Manager only when the workboard has actionable information;
- avoid same-turn foreground polling by Manager;
- allow explicit user status checks without creating tight loops;
- support cancellation where the task type can be cancelled.

The current `async_boundary` should be treated as a Manager-turn yield point. It prevents the Manager from starting background work and then immediately polling or mutating state in the same conversation turn. It does not end the autonomous session. After the turn settles, the workboard signaler decides whether there is more actionable work to continue.

We need a unified background-task owner so card runs, dependency jobs, and future batch operations behave consistently.

## Do Not Delegate Ownership To Agent CLIs

Agent CLIs such as `pi` and `opencode` may have their own session or resume mechanisms. Those are useful adapter capabilities, but they should not become Blueprint's source of truth.

Blueprint must own:

- task id and task type;
- project id and affected card/run/job ids;
- status lifecycle;
- cancellation semantics;
- project locking;
- stdout/event capture;
- filesystem boundary;
- manifest/reviewer validation;
- dependency attention;
- project-state event emission;
- workboard signal emission;
- persisted recovery state.

`pi`, `opencode`, or another executor may own:

- internal agent reasoning;
- provider/session files;
- CLI-specific resume metadata;
- stdout text and structured `BP_EVENT` reports.

Any CLI session id should be stored as adapter metadata on a Blueprint-owned task. It should not replace Blueprint's run/job state.

## Target Model

Introduce a common background task record:

```json
{
  "task_id": "bgtask_...",
  "task_type": "card_run | runtime_dependency_install | batch_card_start | cleanup",
  "project_id": "project",
  "status": "queued | launching | running | waiting | succeeded | failed | cancelled | interrupted",
  "created_at": "...",
  "started_at": "...",
  "finished_at": "...",
  "affected": {
    "card_ids": [],
    "run_ids": [],
    "job_ids": []
  },
  "adapter": {
    "kind": "worker_service | dependency_installer | pi | opencode",
    "session_id": null,
    "process_id": null
  },
  "result": {},
  "error": null
}
```

The exact storage can stay project-local. The important contract is that a background task has a durable id, a durable terminal state, and enough metadata for UI, workboard signals, and recovery.

## Reference Patterns

No surveyed open-source agent framework provides this exact project-level workboard abstraction. The closest reusable ideas come from workflow and data-orchestration systems:

- [Temporal](https://docs.temporal.io/) is the strongest reference for durable execution: persist workflow/task state, resume after crashes, separate external signals/queries from actual work, and make timers explicit. Blueprint should borrow the durability and signal/query split, but not require Temporal as infrastructure in P0.
- [Prefect work pools and work queues](https://docs.prefect.io/v3/concepts/work-pools) are a useful model for priority and concurrency control. The workboard `todo`, `running`, and `deferred` lanes should behave more like controlled queues than like free-form agent memory.
- [Prefect background tasks](https://docs.prefect.io/v3/concepts/tasks#background-tasks) match the "submit now, execute elsewhere, inspect status later" shape for long-running installs or card runs.
- [Dagster declarative automation](https://release-1-8-9.dagster.dagster-docs.io/concepts/automation/declarative-automation) and [asset sensors](https://legacy-versioned-docs.dagster.dagster-docs.io/concepts/partitions-schedules-sensors/asset-sensors) are the best reference for ready-frontier logic over assets. Blueprint's `ready_to_start` should be derived from asset/card state in the same spirit, not authored by Manager.
- [Celery Canvas](https://docs.celeryq.dev/en/stable/userguide/canvas.html) is a useful reference for parallel groups and join callbacks. Blueprint's future batch run-control can borrow the group/chord idea without exposing arbitrary task graphs to Manager.
- [LangGraph interrupts/checkpointing](https://reference.langchain.com/python/langgraph/types/interrupt) and [AutoGen human-in-the-loop](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/human-in-the-loop.html) are useful for turn interruption and resume semantics, but they do not replace a backend-owned project workboard.
- [OpenHands](https://docs.openhands.dev/sdk/api-reference/openhands.sdk.agent) is a useful reference for event-ordered agent state and resume compatibility checks. Its state should be treated as adapter/session metadata, not as Blueprint's authoritative task/workboard state.

Design takeaway: Blueprint should implement a lightweight local version of durable task records, scoped queues, frontier derivation, explicit interrupts, and idempotent signals. It should avoid importing a full external orchestrator until local project-scoped execution outgrows the current storage model.

## Lifecycle

Every background task should follow the same high-level lifecycle:

1. `queued`
2. `launching`
3. `running`
4. terminal state: `succeeded`, `failed`, `cancelled`, or `interrupted`

`waiting` is an optional non-terminal pause state. It should be used when a task has been accepted but cannot actively run yet, for example because a project/session concurrency cap is full, a required resource is temporarily unavailable, or the task is intentionally paused by scheduler policy. The task scheduler or background supervisor is responsible for moving `waiting` back to `queued` or `launching` when the blocking condition clears.

Task-specific states can still exist underneath. For example, a card run can move through `reviewing`, and a dependency install can record solver/package resolution details. The shared task state is the product-level shell around those details.

## Manager Turn Boundary

The Manager-facing start response should keep the existing protocol:

```json
{
  "ok": true,
  "background": true,
  "async_boundary": true,
  "do_not_poll": true,
  "wait_for_wake": true,
  "task_id": "bgtask_...",
  "run_id": "run_...",
  "job_id": "depjob_..."
}
```

For compatibility, card runs can continue returning `run_id`, and dependency installs can continue returning `job_id`. A future unified supervisor should add `task_id` without removing the existing ids.

Manager behavior:

- report the id and end the turn after successful start;
- do not poll status in the same turn;
- do not inspect project state just to wait;
- yield to the workboard signaler;
- use explicit status tools only when the user asks or a later workboard-consumption turn needs recovery.

This remains separate from task execution. `async_boundary` prevents same-turn loops; the supervisor owns actual process/job lifecycle. The `wait_for_wake` response field can remain as a compatibility flag, but the future delivery mechanism should be the workboard signaler.

Important semantic change:

```text
async_boundary = stop the current Manager turn
async_boundary != stop the autonomous work session
```

If the workboard still has signalable items after the turn settles, the signaler may schedule the next Manager turn on a subsequent scheduler tick or event-loop turn. It must not synchronously recurse back into the same Manager turn. If the workboard only has `running` items, Manager idles until a task event changes the board.

## Timer-Based Observation

Timer polling should be a user-visible observation mode, not an uncontrolled Manager habit.

A future Manager or UI control may allow:

```json
{
  "watch_task_id": "bgtask_...",
  "poll_interval_seconds": 10,
  "max_duration_seconds": 600
}
```

Rules:

- polling interval is explicit and bounded;
- polling reads the unified task status, not arbitrary project state;
- polling does not let Manager mutate the graph while a background task is running;
- project-state events remain the preferred live-refresh path;
- terminal state still updates the workboard so a signal can prompt Manager to summarize or repair once.

This gives users a way to watch long jobs without letting Manager repeatedly call inspect/status tools as fast as the model loop allows.

The observation control should be a bounded timer or poll loop, not a synchronous retry loop inside the same Manager turn.

## Workboard Signaler

Terminal task handling should not directly wake Manager per card/run/job. It should update the workboard first.

The workboard signaler emits idempotent signals based on the current board state, not directly from a background wake queue.

Examples of raw terminal task inputs:

- `card_run_reviewed`
- `card_run_failed`
- `runtime_dependency_install_succeeded`
- `runtime_dependency_install_failed`
- `batch_card_start_completed`

These inputs become workboard items such as:

- `completed`
- `needs_manager`
- `ready_to_start`
- `deferred`

But terminal task inputs are not the only signal source. The signaler should also run after a Manager turn settles.

For example:

```text
Manager claims todo A
Manager starts background run A
Manager hits async_boundary and yields
workboard signaler sees todo B still pending
workboard signaler schedules next turn on subsequent tick
Manager claims todo B
```

The workboard signal payload should be compact:

```json
{
  "signal_id": "wbsig_...",
  "project_id": "project",
  "reason": "workboard_actionable",
  "counts": {
    "needs_manager": 1,
    "completed": 2,
    "ready_to_start": 3
  },
  "created_at": "..."
}
```

The detailed task/card/run information stays in the workboard store. Manager should respond to the signal by calling `get_background_workboard`, not by relying on a single event payload to contain all context.

Manager workboard processing then becomes consistent:

1. raw task event or Manager turn settlement triggers a board evaluation;
2. workboard derives or updates actionable items;
3. workboard emits a compact signal if actionable work exists and delivery is allowed;
4. Manager reads the workboard;
5. Manager claims one item or action batch;
6. Manager reports, repairs, or submits a scoped run batch;
7. the next background start creates a new boundary and a new task.

This prevents simultaneous card completions from starting multiple unmanaged Manager turns. If `card_1` and `card_2` finish at the same time, both terminal facts are stored, but Manager receives one "workboard has information" signal and chooses what to consume.

## Background Workboard

The supervisor should expose a workboard for Manager instead of making Manager reconstruct project state from general inspection tools.

The workboard is not a persisted card field. It is a project-level coordination surface. Some lanes are derived from current cards, assets, runs, dependency attention, background tasks, and task consumption state. Other lanes may contain Manager-promoted work items, but those items must be backed by existing system/workboard item ids and validated by the backend.

Suggested lanes:

- `running`: background tasks are active; no Manager action is needed yet.
- `todo`: scoped, backend-verified work items promoted from existing workboard items. In P0 this should not be free-form Manager-authored planning text.
- `needs_manager`: failed or blocked items that need Manager repair, user reporting, or a safe follow-up action.
- `completed`: terminal successful items that have not yet been consumed by Manager.
- `ready_to_start`: cards/tasks that backend has already determined are safe to start now.
- `blocked_for_user`: items Manager has determined it cannot solve without user input or an external change.
- `deferred`: actionable items intentionally held back because Manager is already processing another item or has just started async work.

`ready_to_start` should be backend-derived. Manager should not infer it by reading the full graph and guessing.

`ready_to_start` means "technically startable", not "authorized for this autonomous command". Autonomous consumption still needs a session scope or a user-selected batch.

Example:

```json
{
  "running": [
    {"task_id": "bgtask_run_1", "card_id": "card_1", "status": "running"}
  ],
  "needs_manager": [
    {
      "task_id": "bgtask_run_2",
      "card_id": "card_2",
      "reason": "runtime_dependency_missing",
      "recommended_action": "install_runtime_dependencies"
    }
  ],
  "todo": [
    {
      "item_id": "todo_start_card_4",
      "kind": "start_ready_card",
      "source_item_id": "ready_card_4",
      "source_lane": "ready_to_start",
      "status": "pending"
    }
  ],
  "completed": [
    {"task_id": "bgtask_run_3", "card_id": "card_3", "summary": "Reviewer accepted outputs."}
  ],
  "ready_to_start": [
    {
      "item_id": "ready_card_4",
      "card_id": "card_4",
      "reason": "all_inputs_current",
      "parallel_group": "step_4",
      "safe_to_batch_start": true
    }
  ]
}
```

Manager policy:

- If the workboard has only `running`, stop and wait for events/timer.
- If `todo` or `needs_manager` exists, handle the highest-priority actionable item or batch first.
- If `completed` exists, summarize or advance downstream work.
- If scoped `ready_to_start` exists, promote allowed ready items into `todo`, claim a todo batch, submit that batch, then stop on async boundary.
- Each Manager turn should consume at most one action batch. If it starts new background work, remaining workboard items stay pending/deferred.

Workboard items that require Manager action need explicit consumption state:

- `pending`
- `claimed`
- `processing`
- `done`
- `deferred`
- `failed`

This prevents repeated summaries and prevents multiple simultaneous terminal events from being delivered to Manager at once.

Claim state needs a lease. A claimed or processing item should record `claimed_by_session_id`, `claimed_at`, and `claim_expires_at`. If Manager crashes, times out, or the session is cancelled before completing the item, the backend can release the claim or mark it interrupted instead of leaving the workboard stuck forever.
Lease recovery needs an explicit reaper. The backend should scan expired claims on workboard evaluation and on a periodic maintenance tick so a dead Manager cannot hold a todo forever when no new task events arrive. A reasonable default maintenance interval is about 30 seconds, with deployment configuration allowed to tune it.

### Todo Dependencies And Ready Frontier

The workboard todo lane must not be a flat list. It is an action graph with dependencies.

For an autonomous request such as "run this line", Manager/session scope may include a target lineage or module, but only currently satisfied frontier items should become `todo`.

Definitions:

- `candidate_scope`: cards/items that belong to the user's requested line/module/goal.
- `ready_frontier`: scoped items whose upstream asset/run/todo dependencies are currently satisfied.
- `todo`: ready frontier items promoted for Manager action.
- `blocked_descendants`: scoped items that are in the requested line but cannot be promoted yet.

Item metadata should support dependencies:

```json
{
  "item_id": "ready_card_downstream_001",
  "card_id": "card_downstream",
  "depends_on_items": ["completed_card_upstream_001"],
  "depends_on_assets": ["asset_upstream_current"],
  "blocked_by_items": [],
  "unblocks_item_ids": ["ready_card_final_001"],
  "status": "pending"
}
```

Promotion rule:

```text
candidate_scope -> derive ready_frontier -> promote scoped ready_frontier to todo
```

Do not create todo for downstream work before its upstream assets/runs are accepted and current. When a todo completes or a run is reviewed, the workboard is reevaluated; newly satisfied downstream items may then enter `ready_to_start` and be promoted to `todo`.

This gives the loop:

```text
todo A -> run A -> async yield
workboard reevaluates
if B is now ready -> promote B to todo -> signal Manager
if B is still blocked -> no todo for B yet
```

### Todo Source Restrictions

`todo` must not become a second free-form planning system controlled by Manager. It is a backend-verifiable action queue.

P0 rule:

```text
todo items can only be promoted from existing system/workboard items.
```

Allowed sources:

- scoped `ready_to_start` items;
- system-created `needs_manager` items that have a clear repair action;
- system-created `completed` items that have a clear follow-up action.

For run submission, P0 should be narrower:

```text
ready_to_start -> todo -> claim -> start background run
```

Manager should not be able to create arbitrary todo items such as "analyze X" or "fix project" unless they are backed by a system item id.

Todo item shape should preserve provenance:

```json
{
  "item_id": "todo_start_kegg",
  "lane": "todo",
  "kind": "start_ready_card",
  "source_item_id": "ready_card_kegg_001",
  "source_lane": "ready_to_start",
  "session_id": "autosession_...",
  "status": "pending",
  "action_type": "start_card_run",
  "payload": {
    "card_id": "card_kegg"
  }
}
```

Backend must validate todo creation:

- the source item exists;
- the source item is still actionable;
- the source item is within the current autonomous session scope;
- the action type is allowed for the source lane;
- no equivalent todo already exists for the same source item/session.

Manager can mark an item as not solvable only after claiming an existing workboard item:

```json
{
  "item_id": "todo_install_private_tool",
  "status": "blocked_for_user",
  "reason": "requires_host_admin_install",
  "message": "This requires a host-level system package that Manager cannot install safely."
}
```

This gives the user a durable explanation instead of leaving an unresolved failure buried in chat.

Suggested Manager-facing workboard tools:

- `get_background_workboard`: read lanes, counts, and actionable items.
- `promote_workboard_item_to_todo`: promote an existing scoped workboard item into `todo`.
- `claim_workboard_item`: mark an item as being handled by the current Manager turn.
- `complete_workboard_item`: mark a handled item as done and attach summary/result references.
- `defer_workboard_item`: keep an item pending for a later turn.
- `block_workboard_item_for_user`: mark an item as blocked and user-visible.
- `reopen_workboard_item`: clear a user block or stale defer state after the user provides missing input or changes the source state.
- `submit_workboard_run_batch`: submit claimed todo items as background runs.

These tools should operate on workboard item ids, not arbitrary graph ids, whenever possible.
Backend may implement `promote_workboard_item_to_todo` and `claim_workboard_item` as an atomic helper for a single item or batch when the session already has consume permission. The separate tool names exist so the lifecycle stays observable and recoverable, not to force extra round-trips.

This intentionally separates workboard todo from CLI/agent plans:

- CLI/agent plan: private reasoning or execution strategy inside an agent turn.
- card plan: persisted project blueprint.
- workboard todo: backend-verified, scoped action queue derived from system state.

## Workboard Signal Loop

The signaler should decide whether Manager needs another turn from the board state, not from individual card/run events or a separate wake queue.

Only lanes with actionable Manager work should emit a workboard signal.

Signalable lanes:

- `todo`
- `needs_manager`
- `completed`
- scoped `ready_to_start`

Non-signalable lanes:

- `running`
- `blocked_for_user`
- `deferred`
- out-of-scope `ready_to_start`

`ready_to_start` is signalable only as an invitation to promote scoped frontier work into `todo`. It should not bypass the `ready_to_start -> todo -> claim -> start background run` path. If an autonomous session policy wants fully automatic promotion, that promotion should still be performed by backend validation and recorded as todo provenance, not hidden inside run submission.

`blocked_for_user` should be user-visible, but it should not wake Manager again. When Manager marks an item as `blocked_for_user`, the current response should explain the block, and the workboard/UI should keep the item visible for the user. After that, no additional signal should be emitted for the blocked item unless the user changes it, reopens it, or provides the missing input.

Loop rules:

1. A task event or Manager turn settlement updates/evaluates task facts and workboard lanes.
2. If a Manager workboard-consumption turn is already active, do not start another one.
3. When a Manager turn finishes, inspect the workboard:
   - if there are actionable `todo`, `needs_manager`, `completed`, or scoped `ready_to_start` items, emit another workboard signal if the autonomous session permits consumption;
   - if there are no actionable items but there are `running` tasks, enter async idle and wait for future task events;
   - if only `blocked_for_user` items remain, stop and surface them to the user.
   - if there are no actionable items, no `running` tasks, and no unresolved blocked items, close the autonomous session as complete.
4. If Manager starts new background work, async boundary stops the current turn and pending board items remain queued.

The signaler must run after async-boundary turns. Starting a background run should place Manager into a waiting/yield state for that turn, then the signaler checks whether the board still has other work. If it does, Manager continues with the next item before truly resting. If the board only has running work, Manager rests until a task event changes the board.

This makes the continuation condition explicit:

```text
continue Manager = autonomous session active AND board has unconsumed actionable work
idle Manager = board only has running work
stop Manager = board has no actionable work and no running work
ask user = board has blocked_for_user items that cannot be resolved automatically
```

Workboard signals should have backpressure:

- only one Manager workboard-consumption turn should be active per project;
- while Manager is processing one workboard item or action batch, new task facts update the workboard but do not start another Manager turn;
- if Manager starts background work and hits async boundary, remaining actionable items stay pending/deferred;
- after the Manager turn settles, the signaler may emit another compact signal if actionable items remain and the current autonomous session permits consumption.

Signals also need a durable dedupe key. Store a board revision or digest such as `last_signaled_board_revision` per autonomous session. Re-evaluating an unchanged board must not emit a new signal forever. A new signal is allowed only when actionable item state, session permission, or claim/lease state changes.

## Failure And Dependency Flow Rules

Workboard classification should normalize raw terminal events before deciding what Manager sees.

### Dependency Missing Beats Generic Run Failure

If a run has dependency issue metadata, classify it as dependency repair work even when the terminal event is a generic failure such as timeout or non-zero executor exit.

Priority:

```text
runtime_dependency_missing > executor_validation_failed > manifest_validation_failed > filesystem_audit_failed > generic_run_failed
```

This prevents a missing-package run from becoming an opaque `run_failed` item.

Workboard item example:

```json
{
  "item_id": "need_dep_run_123",
  "lane": "needs_manager",
  "kind": "runtime_dependency_missing",
  "source_run_id": "run_123",
  "card_id": "card_wgcna",
  "recommended_action": "install_runtime_dependencies",
  "payload": {
    "ecosystem": "R",
    "runtime": "R_env",
    "packages": ["WGCNA"]
  },
  "status": "pending"
}
```

### Mid-Run Attention Must Not Signal Manager

Executor `BP_EVENT` issue reports may set run/card attention while the subprocess is still running. These should update the `running` lane details only.

Do not create signalable `needs_manager` items until the run reaches a terminal state. This prevents Manager from mutating card inputs, runtime config, or graph state while the executor may still be writing files.

### Dependency Install Success Does Not Prove The Card Is Fixed

A successful dependency-install job only completes the install todo. It does not prove the original failed card will now pass.

After install success:

1. mark the install todo `done`;
2. preserve the source workboard item id, source `needs_manager` item, card id, and run id in provenance;
3. create or reveal a scoped `ready_to_start` / rerun follow-up item only if the original autonomous session scope allows it;
4. require normal run execution and validation to prove the card is fixed.

Do not mark the card or original failure item as resolved merely because package installation returned `ok: true`.

### Interrupted Jobs Must Enter The Workboard

If backend restart turns a queued/running dependency job or background task into `interrupted`/`failed`, create a workboard item.

Recommended classification:

- retryable runtime install interruption -> `needs_manager`;
- host/runtime corruption that Manager cannot fix -> `blocked_for_user`;
- interrupted card run -> `needs_manager` with a rerun/review recommendation only if scoped and safe.

Do not silently persist interrupted job state without a board item; otherwise autonomous sessions may idle forever.

### Completed And Needs-Review Items Must Be Consumable Once

Successful reviewed runs and review-incomplete runs should become workboard items with stable ids and consumption state.

Rules:

- `completed` items are signalable until claimed and marked `done`;
- `needs_review` / review-incomplete items should become `needs_manager`;
- repeated board evaluations must not recreate a consumed item for the same `run_id` and reason;
- item ids should include the source run/job id and terminal reason.

Example idempotency key:

```text
workboard_item:run_123:card_run_reviewed
```

### Failure Repair Must Follow Asset Dependency Order

Failure repair is also dependency-ordered. If several cards are affected by stale/missing/outdated assets, Manager should not repair or rerun downstream cards before upstream current assets are available.

Workboard repair ordering should be derived from asset/card dependency graph diagnostics, not from LLM ordering.

Rules:

- classify dependency issues into source/upstream and downstream affected cards;
- compute a topological repair order from asset dependencies;
- create signalable `needs_manager` only for the current repair frontier;
- keep downstream repair items as `blocked_descendants` until upstream repairs are complete;
- after each repair action or accepted run, reevaluate dependency attention and promote the next frontier.

For `input_asset_outdated`, the repair item should point to the downstream card whose saved input must be revised, but it should still respect upstream order. If the upstream replacement asset is not accepted/current yet, the downstream revise item must not become todo.

Example:

```json
{
  "item_id": "repair_card_kegg_input",
  "lane": "needs_manager",
  "kind": "input_asset_outdated",
  "card_id": "card_kegg",
  "old_asset_id": "asset_old_modules",
  "current_asset_id": "asset_new_modules",
  "depends_on_assets": ["asset_new_modules"],
  "recommended_action": "revise_card_plan",
  "status": "pending"
}
```

If `asset_new_modules` is not accepted/current, this item should remain blocked and should not signal Manager yet.

## Scoped Run Batches

Global `ready_to_start` should not give Manager permission to start every technically ready card. A user may ask Manager to "run this line", "continue this module", or "finish the KEGG branch"; unrelated ready cards must not be started just because they are startable.

Use a scoped run batch between the workboard and actual run submission.

### Branch Scope From Existing Blueprint State

P0 does not need a persisted `branch_id` field.

The current blueprint already has enough structure to derive branch scopes:

- `card.linked_modules`;
- module hierarchy through `Module.submodules`;
- card inputs and outputs through asset ids;
- materialized asset lineage through `Asset.depends_on`;
- run-to-card mapping through `RunRecord.card_id` and `Asset.created_by_run`;
- timeline/dependency projections from `AssetTimelineService`;
- downstream repair order from dependency attention analysis.

For a user command such as "run the oaa-2 KEGG line", Manager should identify a target scope in user terms:

```json
{
  "scope_kind": "module_lineage",
  "module_ids": ["module_oaa_2"],
  "target_card_ids": ["card_kegg"],
  "intent": "run the oaa-2 KEGG line"
}
```

Backend then expands that into a derived candidate scope:

```json
{
  "scope_id": "scope_module_oaa_2_card_kegg_...",
  "card_ids": ["card_wgcna", "card_module_trait", "card_kegg"],
  "asset_ids": ["asset_modules", "asset_trait_table"],
  "root_card_ids": ["card_wgcna"],
  "target_card_ids": ["card_kegg"]
}
```

`scope_id` can be deterministic and synthetic. It is a workboard/session id, not a new card or graph truth.

Recommended derivation order:

1. Start from explicit `target_card_ids`, selected module ids, or selected card ids.
2. Expand module ids through module hierarchy and `card.linked_modules`.
3. Build the card dependency DAG from card inputs/outputs and asset producers.
4. Include upstream cards required by the target cards.
5. Optionally include downstream cards when the user's command says "continue" or "finish this branch".
6. Filter out inactive cards unless explicitly requested.
7. Compute the ready frontier from the resulting candidate scope.

This keeps Manager responsible for interpreting intent and selecting blueprint scope, while backend remains responsible for dependency correctness.

Longer term, a UI may expose named branches, but they should still be projection/cache records over the graph, not the source of dependency truth.

Flow:

1. Workboard derives global `ready_to_start` items.
2. The autonomous session defines a scope from the explicit user command.
3. Manager promotes matching ready items into scoped `todo` items.
4. Manager claims a todo batch.
5. Backend submits the claimed batch as one background action.
6. Async boundary stops the turn.

`ready_to_start` items should have stable workboard item ids:

```json
{
  "ready_to_start": [
    {
      "item_id": "ready_card_kegg_001",
      "card_id": "card_kegg",
      "module_id": "module_oaa_2",
      "lineage": ["card_wgcna", "card_module_trait", "card_kegg"],
      "parallel_group": "step_4",
      "safe_to_batch_start": true
    }
  ]
}
```

A run batch records user/session intent:

```json
{
  "run_batch_id": "runbatch_...",
  "session_id": "autosession_...",
  "intent": "run the oaa-2 KEGG line",
  "scope": {
    "allowed_card_ids": ["card_wgcna", "card_module_trait", "card_kegg"],
    "allowed_modules": ["module_oaa_2"]
  },
  "items": [
    {
      "item_id": "todo_start_kegg_001",
      "card_id": "card_kegg",
      "source": "todo",
      "source_item_id": "ready_card_kegg_001"
    }
  ]
}
```

Submission should use the claimed workboard batch, not free-form card ids:

```text
submit_workboard_run_batch(run_batch_id)
```

or, for a narrower P0 API:

```text
submit_claimed_workboard_items(todo_item_ids[])
```

The key constraint is that the ids come from the workboard and are inside session scope, not from arbitrary Manager-generated `card_id` lists.

Backend must revalidate every item at submission time:

- the claimed todo item and its source ready item still exist;
- the card is still technically ready;
- the item is inside session scope;
- there is no active run for the card;
- saved inputs are still current and accepted;
- the batch has not already been consumed.

If at least one item starts, the response enters async boundary. Items that are no longer ready are returned as `blocked` and remain for a later Manager turn or user report. The response-level `blocked` list is for logging and current-turn completeness only; the same blocked facts must also be persisted in the workboard, and later handling should read them from the workboard.

If no items start, the response should not enter async boundary; Manager may repair or report the blockers.

## Auto Commands As Workboard Consumption Permission

`/auto` should not need to be a long-lived global "auto on" state. It can be treated as an explicit command that grants Manager permission to consume the background workboard for the current autonomous work session.

The session should still have durable state, for example `active`, `idle`, `blocked`, `completed`, or `cancelled`. The session becomes `idle` when the board only contains running background tasks waiting for future terminal events. It becomes `blocked` when only user-blocked work remains. It becomes `completed` only when there is no actionable work, no running work, and no unresolved blocked work inside the session scope.

Examples:

- `/auto continue this project`
- `/auto finish all ready analysis cards`
- `/auto resolve dependency issues and rerun affected cards`
- a future explicit command such as `/run-workboard` or `/continue-background`

Default behavior without such a command:

- Manager responds only to the current user request.
- Workboard signals may update project/UI state and create pending workboard items.
- User-requested status questions may read the workboard, but should not ack, defer, repair, or start work unless the user explicitly asks.

Behavior during an explicit autonomous work session:

- Manager may call `get_background_workboard`.
- Manager may consume workboard items by repairing `needs_manager`, summarizing `completed`, and promoting/claiming scoped `ready_to_start` work before submitting background runs.
- If a consumed item starts background work, async boundary stops the turn.
- When a workboard signal/timer resumes the same explicit autonomous session, Manager may continue consuming the workboard.
- When the workboard has no actionable items, or only `running` items, Manager stops and the autonomous session is considered idle until the next terminal event.
- When Manager needs user input or approval, it stops and the autonomous session becomes blocked rather than continuing to guess.

Session records should store scope and permission separately from the workboard items: `session_id`, user command/intent, allowed card/module scope, `view_workboard`, `consume_workboard`, and timeout/expiry policy. This prevents a later signal from accidentally continuing outside the user's original request.

Read-only behavior:

- In btw/read-only mode, Manager may view workboard state when relevant.
- Manager must not consume items or start/repair background work.

This implies two permissions:

- `view_workboard`: read the derived workboard for explanation/status.
- `consume_workboard`: claim/ack/defer workboard items and perform follow-up actions. This should require an explicit autonomous command or a direct user command for the specific action.

Workboard signaling should respect these permissions:

- no active autonomous session: record pending workboard items; do not start Manager solely because work exists.
- active autonomous session: workboard signal/timer may start a Manager turn to consume actionable workboard items until the session completes, idles, or blocks.
- btw/read-only: allow explanation but no mutation or task starts.

## Batch Card Start

Parallel card starts should not be implemented by relaxing the boundary to allow arbitrary post-start tools.

Preferred shape:

```json
{
  "todo_item_ids": ["todo_start_card_a", "todo_start_card_b"],
  "start_policy": "start_ready_only"
}
```

Backend response:

```json
{
  "ok": true,
  "background": true,
  "async_boundary": true,
  "do_not_poll": true,
  "wait_for_wake": true,
  "task_id": "bgtask_...",
  "started": [
    {"card_id": "card_a", "run_id": "run_a"},
    {"card_id": "card_b", "run_id": "run_b"}
  ],
  "blocked": []
}
```

The batch operation can validate and start several cards in one backend-owned action. The Manager still ends the turn after that one action.

## Implementation Plan

### P0: Align Current Background Work

1. Keep `async_boundary` as a turn-control guard.
2. Add `task_id` to dependency jobs and card run start responses, while preserving `run_id` and `job_id`. In P0 this can be a minimal project-local registry attached to the existing job/run services; P1 can extract a dedicated `BackgroundTaskService` once the mapping is stable.
3. Normalize project-state event payloads so they include `task_id` when available.
4. Ensure dependency install terminal states update workboard items and emit compact workboard signals with structured result/error details.
5. Keep explicit status endpoints, but treat them as recovery/user-check tools, not normal polling.

### P1: Add `BackgroundTaskService`

Create a service that owns:

- task creation;
- status transitions;
- durable task records;
- project-state event emission;
- workboard item updates;
- workboard signal emission;
- cancellation dispatch.

`WorkerService` and `RuntimeDependencyJobService` should register/update tasks through this service instead of each owning a parallel task lifecycle.

### P2: Batch Run-Control

Add `start_card_runs` or equivalent.

This should be the supported path for Manager to start multiple independent claimed ready-card todo items in parallel. Do not rely on same-turn repeated `start_card_run` calls after a boundary.

The batch should also enforce a project/session concurrency cap, for example `max_concurrent_runs`, so one oversized autonomous command cannot launch an unbounded number of simultaneous tasks.

### P3: Adapter Resume Metadata

For CLIs that support sessions, store adapter metadata such as:

- `adapter.session_id`;
- `adapter.session_dir`;
- `adapter.resume_command`;
- `adapter.provider`.

Use this for diagnostics and possible recovery, but keep Blueprint task state authoritative.

## Non-Goals

- Do not make `pi` or `opencode` the authoritative background scheduler.
- Do not allow unrestricted Manager tool calls after a background start.
- Do not replace project-state events with interval polling.
- Do not require every background task type to be cancellable in v1.
- Do not build a full distributed queue before local project-scoped execution needs it.

## Open Questions

1. Should `task_id` become the primary id in frontend UI, with `run_id`/`job_id` shown as task-specific details?
2. Should timer observation live in Manager tools, frontend UI controls, or both?
3. Should the concurrency cap be enforced per session, per project, or both?
4. On backend restart, should non-terminal tasks become `interrupted` immediately, or should adapters get one chance to reattach when session metadata exists?
