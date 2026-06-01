# Manager Workboard Repair Modules

Status: consolidated next-round repair plan.

Date: 2026-06-01

## Purpose

Recent OAA-2 and NMF runs exposed several related bugs across graph dependency identity, workboard auto control, runtime dependency repair, and executor terminal reporting.

This document consolidates the latest scattered notes into three implementation modules:

1. Graph/Input Resolution
2. Auto/Workboard Control
3. Executor Terminal Contract

`docs/35_oaa2_plan_accept_workboard_bug_review.md` remains the detailed OAA-2 evidence/root-cause document. This file is the execution-oriented repair index.

## System Boundary

Use one conceptual system, not three competing control paths.

```text
Blueprint graph = durable project facts
Workboard = derived execution/control projection over those facts
Auto = scoped permission for workboard to wake Manager again
```

Blueprint remains the source of truth for:

- cards;
- assets;
- runs;
- proposals;
- dependency edges;
- runtime configuration;
- background task/job facts.

Workboard should not become a second planning system. It should derive actionable lanes from Blueprint state and persist only control metadata:

- claims/leases;
- todo promotions from backend-derived ready items;
- deferred items;
- blocked-for-user items;
- acknowledgement/completion state;
- session scope and ownership metadata.

This is a merge at the product/control-model level, not a directive to collapse every table or service into one storage object. Keep Blueprint as the durable fact model and Workboard as a derived operational projection. The important simplification is that there should be one way to acquire and launch executable project work:

```text
Blueprint facts -> Workboard projection -> Manager claim/submit -> background run/job
```

There should not be a parallel "direct Manager execution path" for frontier or multi-card work that bypasses Workboard. Direct single-card launch can remain as an explicit low-level action, but any "continue", "run next", "run ready work", "resume", or batch/frontier instruction should enter through the Workboard projection.

This keeps the product model simple:

```text
Blueprint decides what exists and what is valid.
Workboard decides what can be handled now and who is handling it.
Auto decides whether the workboard may resume Manager without another user message.
```

Long-term, "Auto" should disappear as a separate mode. It should become a permission envelope over the same Workboard path:

```text
Auto = wake_allowed + owner_session + scope + stop/cancel controls
```

That means disabling Auto should not disable Workboard. It should only disable autonomous wake/resume after an async boundary.

## Module 1: Graph/Input Resolution

### Problems

The graph currently conflates:

- planned output contract ids;
- concrete run output asset ids;
- virtual/planned input ids;
- resolved executor input assets;
- dependency attention diagnostics;
- workboard readiness.

Observed failures:

- Accepting a run can replace a planned output id with a concrete run asset id, making downstream planned inputs appear missing.
- `rerun_card` behaves like strict retry: it reuses current saved inputs and does not automatically resolve latest upstream assets.
- Accepted-card reruns can drift `planned_asset_id` so the new asset aliases the previous concrete run asset instead of the original logical planned id.
- Workboard/Flow can treat a planned input alias as ready while `WorkerService._task_packet` still looks up `card.inputs[].asset_id` directly in concrete `graph.assets`.
- OAA-2 has dirty state: `提取样本名称` has a reviewed run and valid output but the card remains `planned`.
- OAA-2 has a short-link graph: `样本分组定义` consumes the raw matrix directly instead of consuming `sample_names`.

### First Fix: Stop Planned Alias Drift

Before adding the shared input resolver, fix accepted-card rerun alias drift.

Current failure mode:

```text
first accept:
  logical planned id asset_sample_metadata
  -> concrete asset asset_run_23e40..._sample_metadata_1
  -> metadata.planned_asset_id = asset_sample_metadata

rerun after accept:
  card.outputs[].asset_id is now asset_run_23e40..._sample_metadata_1
  -> new concrete asset asset_run_ed55..._sample_metadata_1
  -> metadata.planned_asset_id incorrectly becomes asset_run_23e40..._sample_metadata_1
```

The accept path must not blindly use the current visible `out.asset_id` as the alias. If `out.asset_id` already refers to a concrete asset, follow that asset's `metadata["planned_asset_id"]` chain and recover the original logical id. Only use `out.asset_id` directly when it is not already a concrete materialized asset or no better logical id exists.

The chain walk must reject concrete run-asset ids as stable logical aliases. If an id matches a concrete run-asset pattern such as `asset_run_*`, or if it exists in `graph.assets`, it is a materialized asset id, not the stable planned contract id. Continue only when the next metadata alias points to a non-materialized logical id; otherwise stop and mark alias provenance incomplete.

The invariant must hold across repeated reruns: the accepted output alias should always point back to the original logical planned id, not to the immediately previous concrete run asset. Multiple reruns must not create a concrete-to-concrete alias chain.

### Target Behavior

Add one backend-owned input resolver used by:

- `FlowService.get_work_order`;
- `DependencyAttentionService`;
- `WorkerService._task_packet`;
- direct `start_card_run`;
- `rerun_card`;
- workboard `submit_claimed_workboard_items`;
- future input rebinding tools.

Suggested resolver output:

```json
{
  "requested_asset_id": "asset_sample_metadata",
  "resolved_asset_id": "asset_run_..._sample_metadata_1",
  "resolved_path": "results/.../sample_metadata.tsv",
  "resolved_by": "planned_asset_alias",
  "producer_card_id": "card_card_20260531_141200",
  "producer_role": "sample_metadata",
  "status": "valid"
}
```

Rules:

- executor task packets must contain concrete resolved asset ids and paths;
- the original requested id should remain as provenance;
- unresolved virtual inputs must block startup with `input_resolution_failed`;
- Dependency Attention should report both requested and resolved ids for virtual inputs;
- virtual inputs should not be reported as `input_asset_outdated` merely because they are not concrete;
- if the virtual id resolves to stale/nonvalid output, report the current valid target or block launch.

### Rerun Semantics

Split semantics conceptually:

- `rerun_exact`: strict retry with the current saved concrete inputs.
- `rerun_latest`: resolve current upstream aliases/latest valid outputs before launch.

The production/default path should prefer latest dependency resolution unless the user explicitly asks for exact reproduction.

Accepted-card reruns must recover the stable logical output id from:

- current output asset `metadata["planned_asset_id"]`;
- earlier accepted output alias for the same card/role;
- original planned card/proposal snapshot when available.

Do not let a new run's `planned_asset_id` point at the previous concrete run asset unless no stable logical id can be recovered and the run is clearly marked for repair.

### Implementation Tasks

- Keep `Asset.metadata["planned_asset_id"]` as the near-term alias source.
- Ensure run accept writes missing `planned_asset_id` before replacing visible output ids.
- When accepting a rerun, recover the source logical planned id before writing the new asset alias; do not alias to the previous concrete asset.
- Ensure timeline/flow maps both concrete and planned ids to the same producer.
- Add shared input resolver and make task packet creation use it.
- Add startup guards for `start_card_run`, `rerun_card`, and workboard submit.
- Add a consistency check: reviewed run + valid output + card still `planned` should become a `needs_manager` inconsistency, not `ready_to_start`.
- Repair OAA-2 data:
  - mark `提取样本名称` accepted and link its reviewed run/assets;
  - decide whether `样本分组定义` must consume `sample_names`; if yes, add the dependency and rerun/review.

### Tests

- planned alias resolves to latest concrete asset path in `task_packet.json`;
- unresolved planned alias blocks startup and suppresses `ready_to_start`;
- Dependency Attention reports requested/resolved ids for virtual inputs;
- replacing a concrete old input with a virtual planned input does not hide launch-time resolution failure;
- accepted run followed by rerun preserves the original logical planned alias;
- accepted run followed by multiple reruns does not create a concrete-to-concrete alias chain;
- reviewed run + planned card is not exposed as `ready_to_start`;
- OAA-style sample_names -> sample_metadata dependency is represented when the downstream card semantically depends on it.

## Module 2: Auto/Workboard Control

### Problems

The workboard and Manager control flow still have several unstable edges:

- Wake-triggered Manager turns already receive a workboard-first instruction, but non-wake user turns such as "run next cards" do not always get the same workboard-first constraint.
- Even in wake-triggered turns, LLM adherence to workboard-first behavior is not guaranteed, so backend launch guards must still enforce readiness.
- `/auto <prompt>` target semantics are not fully implemented; current frontend mostly supports bare `/auto`.
- Runtime dependency install jobs are not asset dependencies, so the workboard can keep surfacing `ready_to_start` while dependency repair is still running.
- Workboard wake signals currently treat all completed items as actionable, including dependency-repair status receipts that require no Manager decision. This causes unnecessary wake loops.

### Core Boundary

Workboard is the unified task acquisition and execution surface for card work.

Auto mode should not mean "use workboard" while non-auto means "use direct run." Both auto and non-auto execution requests should use workboard when Manager needs to choose from pending/ready work, run a frontier, or start multiple cards.

Auto mode should only add permission for workboard-driven continuation:

```text
non-auto:
  Manager may read/claim/submit workboard items for the current user request.
  After one decision cycle or async boundary, stop.
  Workboard does not wake Manager again unless the user asks.

auto:
  Manager may read/claim/submit workboard items inside the scoped session.
  After async boundary, workboard may emit wake signals back to the owner session.
  Continue until scoped work is complete, blocked, or running-only.
```

So the product boundary is:

```text
workboard = source of executable work
auto = permission for workboard to wake Manager again
```

Long-term, avoid treating "auto mode" as a separate execution mode. It should become a scoped wake/consume permission attached to a workboard session.

Suggested minimal autonomous session state:

```text
session_id
objective / scope
view_workboard: bool
consume_workboard: bool
wake_allowed: bool
expires_at / timeout
state: active | idle | blocked | completed | cancelled
```

Fields such as `active_run_id` and `active_job_id` should gradually move out of auto state and into background task/workboard-derived state. Auto/session state should not duplicate the background task registry.

Migration target:

```text
before:
  auto mode owns session state, background running state, and wake behavior

after:
  workboard/background task registry owns running/claim state
  scoped wake permission owns only whether Manager may be called again
```

During migration, names such as `ManagerAutoService` can remain to avoid broad churn, but new logic should be written against the permission semantics above. Avoid adding new behavior that assumes "auto" is a separate execution channel.

### Target Workboard Semantics

Manager should call `get_background_workboard` first when:

- current turn is an auto owner turn;
- current turn was triggered by a workboard/background wake;
- user asks to continue, resume, start pending work, run ready cards, run next step, or run several cards;
- Manager needs to decide which card can safely start next.

This workboard-first hint should be injected for both wake-triggered and user-driven turns. The wake prompt alone is not enough.

Direct `start_card_run` should be reserved for one explicit card.

For frontier/batch execution:

```text
get_background_workboard
  -> promote_workboard_item_to_todo
  -> claim_workboard_item
  -> submit_claimed_workboard_items
  -> async boundary / yield
```

Manager should consume at most one workboard decision cycle per turn.

In non-auto turns, if a workboard submission enters async boundary, Manager must stop after reporting the started run ids. Workboard must not wake that session again solely because background work continues or completes; continuation requires a new user request. Auto sessions are the only mode where workboard signals may resume Manager without another user message.

### Scoped `/auto <prompt>`

Target behavior:

```text
/auto <user objective>
  -> create/activate scoped workboard wake session
  -> add directive
  -> allow workboard view/consume inside that session
  -> Manager reads workboard and works until actionable board is exhausted, blocked, or running-only
```

Implementation relationship to existing backend state:

- scoped auto should reuse `ManagerAutoState.owner_session_id` for ownership;
- add an explicit session objective/scope field if the current directive list is not enough to preserve user intent;
- `/auto <prompt>` should enable the scoped auto session and immediately call the existing `ManagerAutoService.add_directive` path with the prompt text;
- directive scope is session-scoped, not global project-scoped;
- later workboard wakes may continue only while the same owner session remains enabled and inside scope.

Naming can remain `ManagerAutoService` during migration, but the target product semantics should be closer to:

```text
WorkboardSession
AutonomousWorkboardSession
WakePermission
```

than a global "auto mode."

Bare `/auto` should not become a vague global mode. It should either:

- require a prompt/objective; or
- open a scoped auto composer affordance without starting unrelated work.

Deprecated text commands:

- `/auto once`
- `/auto status`
- `/auto off`
- `/auto stop`

should remain compatibility messages or UI controls, not separate command semantics.

Compatibility behavior:

- `/auto <prompt>` is the primary command form and should behave like the old scoped "auto once with objective" path.
- `/auto` without a prompt should not silently enter an open-ended global mode. Prefer opening UI affordance or returning a short local instruction asking for an objective.
- `/auto off` and `/auto stop` may keep local compatibility messages, but should map to stop/cancel scoped wake permission rather than a different execution path.
- Frontend and prompt copy should avoid saying "enter auto mode" when the implementation is really "allow this scoped workboard session to continue waking Manager."

Frontend composer target states:

- `/auto <prompt>` submission enters breathing/running state;
- input disabled while scoped auto is active for the current session;
- send button becomes stop;
- stop calls the existing auto stop API and cancels active run if present;
- wake responses append to the same owner session.

### Runtime Dependency Gate

Runtime dependency repair must block workboard ready-to-run signaling for the affected card scope while the dependency job is pending/running.

Bug pattern:

```text
runtime_dependency_missing
  -> Manager submits install_runtime_dependencies
  -> dependency job runs in background
  -> workboard sees card asset-ready
  -> ready_to_start signal wakes Manager again
  -> Manager tries to run before dependency repair is done
```

Target behavior:

```text
dependency job queued/running
  -> suppress ready_to_start for affected card
  -> show dependency_repair_running / waiting_runtime_dependency detail
  -> do not emit actionable wake just to start that card

dependency job succeeded
  -> reevaluate workboard
  -> if card is otherwise ready, emit ready_to_start/todo signal

dependency job failed
  -> create needs_manager item
  -> do not expose ready_to_start until handled
```

`start_card_run` and `submit_claimed_workboard_items` must re-check active dependency repair blockers immediately before launch and return:

```json
{
  "ok": false,
  "error_code": "runtime_dependency_repair_in_progress",
  "card_id": "card_x",
  "job_id": "depjob_y",
  "retry_after_signal": "runtime_dependency_install_terminal"
}
```

### Actionable Signal Scope

Separate Manager-actionable completed items from housekeeping completed items.

Dependency repair completion should not wake Manager merely because a completed job record exists. It should wake only when completion creates one of these actionable facts:

- dependency repair succeeded and an affected card now becomes ready;
- dependency repair failed and creates a `needs_manager` item;
- dependency repair changed a previously blocked item into a Manager-decision item.

If the completed item is only a status receipt and no decision is needed, update UI/workboard state but do not emit a workboard-actionable wake.

### Implementation Tasks

- Inject workboard-first guidance into user-driven "continue/run next/start pending" turns, not only wake-triggered turns.
- Keep prompt/tool descriptions aligned, but rely on backend guards for correctness.
- Route non-auto frontier/batch execution through Workboard claim/submit, with no autonomous wake after the async boundary.
- Implement `/auto <prompt>` as scoped auto command rather than bare global mode.
- Treat auto state as wake permission + scope, not as a separate execution path.
- Move new running/active job state toward background task/workboard-derived state, not auto/session state.
- Update frontend composer state for scoped auto running/stop.
- Add runtime dependency repair blocker to workboard derivation.
- Attach dependency job source metadata: card_id, run_id, runtime, packages, session.
- Suppress `ready_to_start` for affected card while dependency repair job is active.
- Keep original dependency issue processing until job terminal state; do not mark it done just because install started.
- Coalesce signals so housekeeping-only completed repair items and running-only changes do not wake Manager.

### Tests

- non-auto "run next cards" receives workboard-first prompt context and uses workboard path, not repeated direct starts;
- wake-triggered turns still start by reading workboard;
- `/auto <prompt>` creates scoped owner session and directive;
- bare `/auto` does not start unrelated global autonomous work;
- composer enters running/stop state for scoped auto;
- active dependency install suppresses `ready_to_start` for source card;
- direct start and workboard submit both return `runtime_dependency_repair_in_progress`;
- dependency job success clears blocker and signals newly ready work;
- dependency job failure creates `needs_manager`;
- completed dependency-repair status receipt without a Manager decision does not emit workboard-actionable wake;
- unrelated ready cards are not suppressed by card-scoped dependency repair.

## Module 3: Executor Terminal Contract

### Problems

The OAA-2 NMF run generated expected files but failed because the manifest did not declare `run_summary` and `run_preview`.

The executor wrote `manifest.candidate.json` with only four declared domain outputs. It omitted the two required system outputs even though the files existed. Backend recovered or read that candidate manifest, then failed validation:

```text
Manifest is missing declared outputs: run_preview, run_summary
```

This is primarily a manifest declaration gap combined with timeout preventing the existing wrapper repair loop from running. The missing terminal report is a side effect of timeout/termination, not the root cause.

### Existing Repair Mechanism

`agent_cli_executor` already has a manifest repair loop for normal provider exits:

```text
provider exits with return_code == 0
  -> promote candidate manifest
  -> validate manifest
  -> if validation errors:
       write manifest_repair_prompt.md
       restart provider with repair prompt
       re-promote and re-validate
       repeat up to MAX_MANIFEST_REPAIR_ATTEMPTS
```

This is already the lightweight "repair prompt -> restart provider -> re-validate" mechanism. It is not a missing abstraction.

The gap is timeout handling:

```text
backend waits for wrapper process with worker_timeout_seconds
  -> timeout kills the whole wrapper process group
  -> wrapper repair loop never gets a chance to run
  -> backend recovers incomplete candidate manifest
  -> validate_manifest fails
```

There is no `--continue` mode in the current pi launch path; the real continuation primitive is "restart provider with a repair prompt" inside the wrapper.

### Target Behavior

Keep Module 3 lightweight. Do not add a new terminal-report supervisor framework unless the simpler fixes fail.

Implement three targeted changes:

1. Increase the default main executor timeout.
2. Give the existing wrapper manifest repair loop its own timeout budget.
3. Add backend manifest auto-patch as a timeout/post-exit fallback when files exist but required manifest declarations are missing.

### 3A: Main And Repair Timeout Split

Current behavior:

```text
worker_timeout_seconds = 900
backend process.wait(timeout=900) waits for the whole wrapper
wrapper _run_provider_command waits without its own timeout
repair attempts also wait without their own timeout
backend timeout kills wrapper + provider + repair attempt together
```

Target settings:

```text
worker_timeout_seconds = 1800
manifest_repair_timeout_seconds = 180
max_manifest_repair_attempts = 3
```

The default main executor timeout should be 30 minutes. The previous 15-minute scale is too short for executor agents that need to generate scripts, run bioinformatics jobs, write outputs, and submit terminal reports in one turn.

The backend wait budget for the wrapper should cover main execution plus repair attempts:

```text
wrapper_wait_timeout =
  worker_timeout_seconds
  + max_manifest_repair_attempts * manifest_repair_timeout_seconds
  + small_buffer_seconds
```

Inside `agent_cli_executor`:

```text
main provider command uses worker_timeout_seconds
each manifest repair provider command uses manifest_repair_timeout_seconds
repair timeout stops that repair attempt cleanly instead of relying on backend SIGKILL
```

These values should be backend Settings values, not hardcoded constants. If exposed through deployment config, update the managed deploy env whitelist in `scripts/deploy_user_systemd.sh` together with the Settings model.

Timeout accounting should be explicit:

- main executor timeout means the initial provider command exceeded `worker_timeout_seconds`;
- manifest repair timeout means a repair provider command exceeded `manifest_repair_timeout_seconds`;
- backend wrapper timeout means the wrapper exceeded the combined outer budget and should be treated as supervisor failure;
- repaired-after-main-timeout should be recorded separately from clean success, because the run succeeded through recovery.

Do not hide repair time inside the main executor timeout. The wrapper should know the remaining phase and record which budget was exhausted. This keeps user-facing errors actionable:

```text
main analysis timed out before any valid candidate manifest
manifest repair attempt timed out after candidate manifest validation failed
backend auto-patched missing declarations for files already present
validation failed because required files were absent
```

### 3B: Backend Manifest Auto-Patch Fallback

Before failing a timeout/post-exit recovered manifest, backend should attempt a narrow deterministic patch:

- candidate manifest exists but misses declared outputs while files exist;
- missing expected output has a `task_packet.expected_outputs[].path_hint`;
- path exists inside the project output scope;
- file type/format can be inferred from expected output metadata or file extension.

Suggested behavior:

```text
for each required expected output role:
  if role not in manifest.created_assets:
    if expected path exists:
      append created_assets entry using expected role/path/class/format
      record auto_patch event
re-run validate_manifest
```

This should be narrow. Do not fabricate missing outputs. Only add declarations for files already present at expected paths.

NMF would be recoverable through this path because both `run_summary.md` and `run_preview.svg` existed.

### 3C: Full Repair Continuation Is A Future Option

A full backend terminal-report supervisor with new states such as `terminal_report_repairing` is not required for the NMF failure class.

Keep it as a future option only if:

- manifest schema is severely damaged and deterministic auto-patch is insufficient;
- wrapper repair loop cannot be made reliable with independent repair timeout;
- repeated real incidents show that deterministic patch + wrapper repair is not enough.

### Implementation Tasks

- Raise default `worker_timeout_seconds` to 1800.
- Add `manifest_repair_timeout_seconds` setting, suggested default 180.
- Make wrapper wait budget account for main execution plus repair attempts.
- Add timeout support to provider command execution inside `agent_cli_executor`.
- Use main timeout for the initial provider command and repair timeout for each manifest repair attempt.
- Record timeout phase in trace/failure metadata: main provider, repair provider, backend wrapper, or auto-patch validation.
- Add deterministic backend auto-patch for missing required manifest declarations when files exist.
- Improve timeout/post-exit failure messages to distinguish:
  - missing files;
  - files present but manifest omitted declarations;
  - repair loop timed out;
  - validation failed after auto-patch.

### Tests

- provider exits normally with incomplete manifest: existing wrapper repair loop still repairs and validates;
- main provider hits `worker_timeout_seconds` but files/candidate manifest exist: backend auto-patch missing required declarations and validates;
- repair provider attempt hits `manifest_repair_timeout_seconds`: wrapper stops that attempt and records a clear repair timeout;
- manifest candidate misses required role but expected file exists: backend auto-patch appends declaration and validation passes;
- manifest candidate misses required role and expected file is absent: validation fails with clear missing-file/manifest error;
- Settings values appear in generated backend env when deployment exposes them.

## Priority Order

Recommended next implementation order:

1. Module 1A: fix `planned_asset_id` drift on accepted-card rerun.
2. Module 1B: add shared input resolver and startup guards.
3. Module 1C: repair OAA-2 dirty state and short-link graph.
4. Module 2A: add runtime dependency repair gate, because it directly affects auto wake loops.
5. Module 2B: distinguish Manager-actionable completed items from housekeeping completed items.
6. Module 3A/3B: split main/repair timeouts and add backend manifest auto-patch fallback.
7. Module 2C: implement scoped `/auto <prompt>` and frontend/prompt polish.

Do not start by polishing Manager prompt alone. The current failures are primarily backend state-machine and resolver issues.
