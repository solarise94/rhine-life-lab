# Runtime Dependency Install Visibility And Liveness Plan

Status: design and remediation plan.

Date: 2026-06-04

Related:

- `docs/31_install_runtime_dependencies_contract.md`
- `docs/26_auto_dependency_refresh_issue_notes.md`
- `docs/41_runtime_dependency_resolver_failure_reporting_review.md`

## Summary

`install_runtime_dependencies` currently has a product gap that is easy to misread as "dependency install is stuck":

1. the backend starts a real background job;
2. the job may complete quickly, especially when the package is already installed;
3. the frontend does not explicitly track the job lifecycle or render a terminal receipt for success;
4. retry attempts during the in-flight window are rejected as duplicates;
5. users therefore see "后台起了一下 mamba，然后前台没消息了".

This is partly a visibility problem and partly a liveness/state-model problem.

The visibility problem is already user-facing in healthy cases such as:

- package already installed;
- fast conda no-op transaction;
- successful install with no explicit card-scoped follow-up UI.

The liveness problem remains real in unhealthy cases:

- job marked active before the subprocess phase is proven to have started;
- no child pid / phase / heartbeat is persisted;
- no stale-job reconciliation exists during the same backend lifetime;
- duplicate suppression treats `queued` / `launching` / `waiting` / `running` uniformly as blockers.

This document proposes a layered fix:

- P0: minimal terminal visibility plus a lightweight floating chip;
- P1: make background dependency jobs observable while running;
- P2: make job state self-healing and less prone to permanent false blockers.

## Confirmed OAA-1 Evidence

Observed on 2026-06-03 in `workspace/oaa1`.

### Case under inspection

Job:

```text
depjob_c5a0f0a6536b4e4eabf8dcc471878b2d
```

Request:

```json
{
  "ecosystem": "R",
  "runtime": "R_env",
  "packages": ["tidyverse"],
  "installer_plan": [
    {
      "kind": "install",
      "installer": "conda",
      "name": "tidyverse",
      "candidate": "r-tidyverse"
    }
  ]
}
```

### What actually happened

The job was not stuck. It finished successfully.

Terminal state:

```text
created_at = 2026-06-03T15:45:51Z
started_at = 2026-06-03T15:45:51Z
finished_at = 2026-06-03T15:46:06Z
status = succeeded
```

The captured `mamba` output shows:

```text
All requested packages already installed
Transaction finished
```

Environment checks also confirmed:

```text
mamba list -p /home/solarise/miniforge3/envs/R_env r-tidyverse
-> r-tidyverse 2.0.0
```

and:

```text
Rscript --vanilla -e 'requireNamespace("tidyverse", quietly=TRUE)'
-> TRUE
```

So the user-visible symptom in this case was:

- not "install failed";
- not "subprocess never ran";
- but "successful background completion had no explicit foreground receipt".

## Current Root Causes

## 1. Success path is silent in the frontend

The frontend currently treats runtime dependency events mostly as refetch triggers.

It has explicit notice behavior for:

- `runtime_dependency_job_changed` + `job_status == failed`

but no explicit success rendering for:

- `runtime_dependency_job_changed` + `job_status == succeeded`

Practical effect:

- failed installs can produce a notice;
- succeeded installs are silent;
- already-installed no-op installs are indistinguishable from "nothing happened".

There is already a frontend API for:

```text
GET /projects/{project_id}/runtime-dependency-jobs/{job_id}
```

but no current frontend consumer tracks and resolves started dependency jobs through that endpoint.

## 2. Running installs are opaque by design

Backend install execution still uses:

```python
subprocess.run(..., capture_output=True)
```

Consequences:

- no streamable stdout/stderr while the job is active;
- no phase updates between start and finish;
- no child pid visible to state;
- no way to distinguish "solver busy", "downloading", "compiling", "waiting on lock", and "not actually launched yet".

This is tolerable for very short tasks, but poor for real installs.

## 3. Job status becomes active too early

`RuntimeDependencyJobService._run(...)` currently marks the job `running` before:

- runtime lock acquisition;
- command-building completion;
- subprocess launch confirmation.

That means one state value currently covers too many realities:

- queued in executor thread;
- blocked on runtime lock;
- building command;
- attempting subprocess launch;
- real subprocess running.

This makes duplicate suppression overly blunt and makes incident diagnosis harder.

## 4. No same-lifetime stale-job reconciliation

Backend restart recovery exists for persisted jobs reloaded from disk, but not for:

- a live backend process where one dependency thread wedges;
- a state record that stays active without a live child process;
- a future that never updates the persisted terminal state.

As a result, if a job really does wedge, retry suppression can remain in place indefinitely until restart or manual intervention.

## 5. Task and job persistence are not fully atomic

During investigation, `background_tasks.json` contained queued dependency tasks whose referenced job ids were absent from `runtime_dependency_jobs.json`.

That means the two records can drift if failure happens in the submission window.

This does not explain the tidyverse no-op case directly, but it is part of the same reliability surface and should be fixed in the same pass or the next one.

## Non-Goals

This plan does not attempt to:

- turn dependency install into a foreground shell transcript;
- let Manager poll dependency jobs aggressively in the same turn;
- expand `install_runtime_dependencies` into a generic environment doctor;
- bypass duplicate suppression entirely;
- blur the resolver/install boundary introduced in the recent resolver work.

## Desired Product Behavior

For a dependency install started from chat or auto mode, the user should be able to tell which of the following happened:

1. request accepted and queued;
2. waiting for same-runtime lock;
3. subprocess launched;
4. solver is resolving/downloading/installing;
5. install finished and changed the environment;
6. install finished but nothing changed because package already satisfied;
7. install failed before launch;
8. install failed after launch;
9. install became stale and was auto-failed by watchdog logic.

Today, several of these states collapse into:

```text
Started background dependency installation...
```

followed by silence.

That is the main product defect.

## Proposed Fixes

## P0: Minimal Terminal Visibility, Floating Chip, And No-Op Receipt

Goal: solve the "前台没消息" problem with the smallest possible product and code change, without changing the installer runtime model yet.

P0 is intentionally narrow:

- backend distinguishes normal success from no-op success;
- frontend shows a symmetric success notice, just as it already shows a failure notice;
- frontend shows a lightweight floating chip while the dependency job is active;
- the chip stays single-line and low-noise;
- no full log console, expandable panel, or phase timeline is required for the first pass.

### Backend changes

Extend the terminal result payload to distinguish:

- `succeeded`
- `succeeded_noop`
- `failed`

Recommended shape:

```json
{
  "ok": true,
  "status_detail": "already_satisfied",
  "changed": false,
  "message": "Dependencies already satisfied.",
  "manager": "conda",
  "runtime": "R_env",
  "packages": ["tidyverse"]
}
```

Recommended `status_detail` values:

- `install_completed`
  - success and the installer appears to have performed a real transaction;
- `already_satisfied`
  - success and the requested packages were already present before any install work was needed;
- `noop_transaction`
  - success and the solver completed a no-op transaction, but the exact reason is not confidently classified.

Recommended `changed` semantics:

- `changed == true`
  - environment may have changed;
- `changed == false`
  - environment is believed not to have changed;
- omitted or `null`
  - backend cannot classify whether the environment changed.

Frontend wording should primarily use `status_detail`; `changed` is a compact machine hint, not the whole product meaning.

Detection rule for conda-family no-op should be treated as heuristic, not as a product contract tied to one exact English sentence.

Practical first-pass rule:

- `returncode == 0`
- conda-family stdout matches one of a small curated set of known no-op signatures such as:
  - `All requested packages already installed`
  - other solver-version-equivalent no-op transaction text if observed in production

Do not overload this into failure logic. It is a successful terminal state with `changed=false`.

This heuristic can be refined later if solver output diversity becomes a problem. P0 does not require a preflight `--dry-run` architecture change.

### No-op detection fallback

If stdout parsing cannot confidently classify a no-op, the backend should still return a visible success result:

```json
{
  "ok": true,
  "status_detail": "install_completed",
  "changed": true,
  "message": "Dependencies installed."
}
```

That fallback may overstate that work happened, but it must not reintroduce frontend silence.

P1 should explicitly evaluate a dry-run preflight for better classification:

```text
solver install --dry-run ...
```

The dry-run path should be designed carefully because it adds an extra solver call and may make already-slow environments slower. It is a classification improvement, not a blocker for P0.

### Frontend changes

For P0, the frontend should add a symmetric handler for:

- `runtime_dependency_job_changed` + `job_status == succeeded`

using the event payload directly, the same way it already handles failed installs.

Receipt semantics for the notice text:

- `status == succeeded` and `changed == true`
  - show `Dependency install completed.`
- `status == succeeded` and `changed == false`
  - show `Dependency already satisfied. No installation was needed.`
- `status == succeeded` and `changed == null` / omitted
  - show `Dependency install completed.`

Unknown `changed` classification should be treated like a normal successful install in the frontend. It is better to slightly overstate completion than to create another silent or ambiguous success path.

The first pass can use the existing project-level notice path in `ProjectWorkspace.tsx`.

### P0 floating chip

P0 should include a lightweight floating chip in the chat/workspace area.

Required shape:

- bottom-anchored near the conversation input area;
- upward entrance animation;
- small rounded long-strip chip;
- single-line text only;
- non-blocking;
- disappears automatically after terminal transition.

Recommended active text:

- `依赖处理中`
- `正在安装 tidyverse`
- `正在处理 3 个运行环境依赖`

Recommended terminal text:

- `依赖安装完成`
- `依赖已满足，无需安装`
- `依赖安装失败`

The chip is not a log viewer. It is only a reassurance and state cue.

### Multiple active dependency jobs

P0 should not assume there can only be one active dependency job globally.

The current product permits different runtimes to have independent dependency work, even though same-runtime duplicate suppression remains in place.

Recommended P0 chip behavior:

- show one bottom chip, not a stack;
- aggregate all active dependency jobs into that chip;
- if one job is active, use package-specific text, for example `正在安装 tidyverse`;
- if multiple jobs are active, use aggregate text, for example `正在处理 2 个依赖任务`;
- terminal receipts should still be emitted per job.

This keeps the UI small while avoiding the "only newest job is visible" failure mode.

Backend admission should continue to reject duplicate in-flight work for the same ecosystem/runtime/package set. It does not need to globally limit dependency installs to one active job.

### Fast completion behavior

Dependency jobs may finish before the entrance animation is useful.

Recommended timing rules:

- active chip minimum visible duration: `1200ms`;
- terminal styling duration before disappearance: `900ms`;
- if a job starts and finishes before the chip first renders, skip the active wording and show the terminal wording for the minimum visible duration;
- do not replay a long animation sequence for a job that already reached a terminal state.

This avoids a confusing flash for no-op installs while still giving the user a perceptible result.

### Event race and reconciliation

The frontend should tolerate terminal events arriving before local pending registration completes.

Recommended P0 reconciliation:

- register the `job_id` immediately from the `install_runtime_dependencies` tool result;
- also react to any `runtime_dependency_job_changed` event with `job_status in {"succeeded", "failed"}` even if the job is not in the local pending map;
- if an active dependency chip exists, transition it to the terminal text;
- if no chip exists, show the terminal receipt or notice directly.

SSE reconnect should trigger a lightweight refetch of project state as it already does. A later enhancement can add explicit recovery of active dependency jobs from persisted job state.

P0 still does not require:

- a full pending-job console;
- terminal detail fetch on success;
- multi-line output rendering in chat;
- a dedicated background-task panel.

### Why this P0 first

This solves the exact tidyverse complaint with very low implementation risk:

- mamba ran;
- nothing needed to install;
- user got a deterministic completion message;
- user also saw a transient active-state cue instead of silence.

It also avoids inflating P0 into a UI-system change before the backend result shape is stabilized.

## P1: Running-Phase Observability

Goal: make active installs inspectable while they are still running.

### Replace `subprocess.run` with `Popen`

For dependency installs only, move to:

- `subprocess.Popen`
- stdout/stderr reader threads or non-blocking loop
- per-job log file
- rolling tail snapshots in job state

Persist additional fields:

```json
{
  "phase": "running_subprocess",
  "command_preview": ["mamba", "install", "-y", "-p", "...", "r-tidyverse"],
  "child_pid": 123456,
  "log_path": "chat/runtime_dependency_logs/depjob_xxx.log",
  "last_heartbeat_at": "2026-06-04T...",
  "last_stdout_at": "2026-06-04T..."
}
```

### Phase model

Replace the current overloaded `running` meaning with explicit phases:

- `queued`
- `waiting_for_runtime_lock`
- `building_command`
- `launching_subprocess`
- `running_subprocess`
- terminal:
  - `succeeded`
  - `failed`
  - `interrupted`

Keep `status` for coarse terminal grouping if needed, but add `phase` as the primary live-state field.

### Status / phase invariants

If both `status` and `phase` exist, they must obey these invariants:

- `status == "queued"` allows `phase == "queued"`;
- `status == "running"` allows only active phases:
  - `waiting_for_runtime_lock`
  - `building_command`
  - `launching_subprocess`
  - `running_subprocess`
- `status == "succeeded"` requires `phase == "succeeded"`;
- `status == "failed"` requires `phase == "failed"`;
- `status == "interrupted"` requires `phase == "interrupted"`;
- terminal phases must not appear with active statuses.

Frontend should use `status` for coarse terminal decisions and `phase` only for live-detail text.

If the two fields conflict, the backend response is invalid. Consumers may prefer terminal `status` for safety, but the bug should be logged and fixed at the producer.

### API additions

Extend dependency job detail response with:

- `phase`
- `child_pid`
- `command_preview`
- `log_tail`
- `last_heartbeat_at`
- `changed`
- `status_detail`

### Frontend rendering

If the job is active and the user opens the relevant surface, show:

- current phase;
- last output tail;
- last update timestamp.

This is enough to differentiate:

- real conda work;
- solver hanging;
- waiting on same-runtime serialization;
- pre-launch failure.

## P2: Liveness And Self-Healing

Goal: avoid false forever-running blockers.

### Watchdog / reconciliation loop

Add periodic reconciliation for active dependency jobs.

Recommended default thresholds:

- `runtime_dependency_prelaunch_stale_seconds = 120`
- `runtime_dependency_heartbeat_stale_seconds = 180`
- `runtime_dependency_timeout_grace_seconds = 15`
- `runtime_dependency_watchdog_interval_seconds = 30`

Pre-launch phases:

- `queued`
- `waiting_for_runtime_lock`
- `building_command`
- `launching_subprocess`

`running_subprocess` should primarily use the installer timeout plus grace, not only heartbeat staleness, because some solvers may be quiet while doing valid work.

Recommended checks:

1. if persisted phase is active but in-memory future is already done:
   - finalize immediately;
2. if phase is pre-launch and there is no heartbeat for threshold:
   - mark failed as stale pre-launch job;
3. if phase is `running_subprocess` and `child_pid` no longer exists:
   - finalize from collected exit state if available, otherwise fail stale;
4. if subprocess exceeds timeout + grace:
   - terminate child and mark timeout;
5. if task exists without matching job, or job exists without matching task:
   - reconcile into a terminal operator-visible failure.

### Duplicate suppression refinement

Duplicate suppression should remain, but the blocker message should become more truthful.

For example:

- `queued`
  - "同一 runtime 的依赖任务已排队"
- `waiting_for_runtime_lock`
  - "同一 runtime 正在等待前序依赖任务完成"
- `running_subprocess`
  - "同一 runtime 正在执行依赖安装"
- stale watchdog failed
  - no longer suppress retry

This is materially better than one generic:

```text
The same dependency installation is already running for this runtime.
```

### Atomic submission consistency

Job creation and background task creation should be treated as one logical transaction.

Minimum acceptable invariant after submit returns:

- background task exists;
- dependency job exists;
- each references the other.

If one side cannot be persisted, the submit path should fail and roll back the other side or repair immediately.

## Workboard And Auto Interaction

This issue touches, but is not defined by, auto mode.

### Non-auto case

Even when `manager_auto.enabled == false`, manual dependency installs still need:

- a visible start receipt;
- a visible terminal receipt.

This is the tidyverse case observed in OAA-1.

### Auto case

For auto mode, success should not be silent either.

Recommended behavior:

- terminal dependency install events should remain eligible to wake auto flow through the existing background terminal callback path;
- but the workboard should also expose a completed dependency install item that is visibly actionable when relevant to a blocked card/run.

Current completed dependency items use:

```json
{
  "kind": "runtime_dependency_install_succeeded",
  "payload": { "actionable_wake": false }
}
```

That is acceptable for noise control, but not sufficient as the only user-visible completion signal.

If auto remains disabled, the UI still needs a receipt.

### Workboard display rule

Workboard should not duplicate every successful dependency install as an actionable item.

Recommended rule:

- failed dependency jobs remain `needs_manager`;
- active dependency jobs may appear as deferred/running context;
- successful dependency jobs appear in completed history only when they are linked to `source.card_id` or `source.run_id`;
- successful unscoped manual installs should rely on the floating chip and notice/receipt, not a workboard item.

For P0, workboard behavior does not need to change. The floating chip and terminal notice are the user-facing fix.

## Frontend Presentation Model

This section defines the chosen active-state UI direction. The minimal floating chip belongs to P0. Richer observability inside the chip remains post-P0.

The recommended UI model is:

- non-blocking;
- strongly visible while active;
- low-noise after terminal state.

This should not reuse the semantic state machine of chat thinking, even if it reuses some of the same visual container styles.

Reason:

- thinking is message-scoped;
- dependency install is job-scoped;
- thinking usually lives inside one turn;
- dependency install may outlive the initiating turn and even page-level interaction timing.

### Core interaction rule

Runtime dependency installation must not lock the frontend.

While a dependency job is active, the user should still be able to:

- continue chatting;
- inspect cards and workboard items;
- switch panels;
- wait passively without losing confidence that the system is still doing real work.

The dependency job therefore occupies a visible UI slot, but not the input focus or the whole chat surface.

### Recommended active-state UI

When `install_runtime_dependencies` returns `job_id`, the chat/workspace should show a very small floating status chip that emerges from the bottom area of the conversation panel.

Recommended behavior:

- anchor near the bottom of the chat panel;
- animate upward into view;
- remain visually separate from normal assistant messages;
- stay single-line by default;
- use a small rounded-rectangle or soft-pill shape;
- show only concise active text such as:
  - `依赖处理中`
  - `正在安装 tidyverse`
  - `正在处理 3 个运行环境依赖`

The first implementation should not expand into a mini console, large panel, or multi-line output block in the main chat surface.

This chip can visually borrow from the existing thinking frame, but should read as:

```text
background job status
```

not:

```text
assistant is thinking in this message
```

### Recommended animation semantics

The animation is not decoration. It is part of the trust model.

Desired motion:

1. after background job start, a small single-line status chip slides upward from the bottom of the conversation area;
2. while active, subtle pulse/spinner/phase changes indicate liveness;
3. on terminal state, the chip transitions quickly to success or failure styling;
4. the active chip then disappears, leaving behind a short terminal receipt.

This directly addresses the current user anxiety pattern:

- "mamba flashed once"
- "then nothing happened"
- "I don't know whether it is still working"

### Terminal-state UX

The active floating card should not remain permanently on screen after completion.

Recommended terminal behavior:

- active chip resolves into terminal state;
- a short success or failure receipt is emitted into the conversation timeline or project notice surface;
- the floating active chip disappears after the terminal transition.

Receipt text examples:

- success with change:
  - `依赖安装完成`
- success without change:
  - `依赖已满足，无需安装`
- failure:
  - `依赖安装失败`

Failure receipts may retain an affordance elsewhere to inspect details:

- stderr tail;
- retry hint;
- requested package;
- attempted candidates.

But the floating chip itself should stay minimal and single-line. Success receipts should stay lightweight and usually do not need an expanded log by default.

### Why not a blocking modal

A blocking modal or foreground "occupy the whole chat" install experience is not recommended.

Reasons:

- conflicts with the existing async-boundary product model;
- makes long installs feel worse, not better;
- prevents the user from continuing adjacent work;
- collapses healthy no-op installs and slow real installs into the same intrusive UX.

The right behavior is:

- visible enough to reduce uncertainty;
- lightweight enough to preserve workspace flow.

### Minimal implementation shape

If the implementation should stay small while still adding active-state visibility, use this presentation model:

1. local pending dependency job store keyed by `job_id`;
2. one floating bottom chip that aggregates active dependency jobs;
3. single-line text only, with compact package/runtime wording;
4. terminal event triggers job-detail fetch;
5. floating chip disappears after terminal transition;
6. terminal receipt remains in chat or notice area.

That is sufficient to deliver the intended reassurance effect without requiring a full multi-job console UI in the first pass.

## Data Model Proposal

Recommended new fields on persisted dependency jobs:

```json
{
  "status": "running",
  "phase": "running_subprocess",
  "status_detail": "already_satisfied",
  "changed": false,
  "command_preview": ["..."],
  "child_pid": 123456,
  "log_path": "chat/runtime_dependency_logs/depjob_xxx.log",
  "last_heartbeat_at": "2026-06-04T...",
  "last_stdout_at": "2026-06-04T...",
  "last_stderr_at": "2026-06-04T..."
}
```

Recommended new fields on background tasks for dependency installs:

```json
{
  "adapter": {
    "kind": "dependency_installer",
    "process_id": 123456,
    "metadata": {
      "phase": "running_subprocess",
      "job_id": "depjob_xxx"
    }
  }
}
```

The task record does not need the full log tail if the job record already owns it.

### Log retention

If P1 writes per-job log files, logs need bounded retention.

Recommended first policy:

- store under `chat/runtime_dependency_logs/`;
- keep logs for the newest 100 dependency jobs per project;
- delete logs older than 14 days during normal project maintenance or service startup reconciliation;
- never write secrets or full environment dumps into these logs.

The job record should keep bounded `stdout_tail` / `stderr_tail` even if the full log is later deleted.

## Testing Plan

## P0 tests

Backend:

- conda install stdout containing `All requested packages already installed` yields:
  - `ok == true`
  - `changed == false`
  - `status_detail == already_satisfied`

Frontend:

- started dependency job enters local pending state;
- terminal success event updates the floating chip and notice;
- success-noop receipt is rendered;
- failure receipt remains rendered with existing error data.
- concurrent active jobs across different runtimes aggregate into one chip;
- fast completion under 2 seconds still shows a perceptible terminal chip or receipt;
- terminal SSE event without a matching local pending job still produces a receipt.

## P1 tests

Backend:

- phase transitions persist in order;
- status / phase invariants are enforced;
- `launching_subprocess` records child pid on successful spawn;
- log tail is truncated safely and returned through detail API;
- timeout still yields terminal failure with collected tail.
- restart recovery does not leave active phase values paired with terminal statuses.

## P2 tests

Backend:

- stale pre-launch active job becomes failed after watchdog threshold;
- dead child pid while active becomes terminal failure;
- duplicate suppression is released after watchdog failure;
- concurrent duplicate submissions for the same runtime/package remain suppressed while non-duplicate runtimes may proceed;
- task/job mismatch is reconciled into operator-visible failure;
- submit path never leaves a task without a job.

Frontend:

- SSE disconnect/reconnect followed by project refetch does not leave an active dependency chip stuck forever.

## Rollout Order

Recommended order:

1. P0 minimal terminal visibility, no-op detection, and floating chip
2. P1 running-phase observability
3. P2 watchdog and transactional consistency

Reason:

- P0 fixes the current user pain fastest with the smallest diff while still making the active period visible;
- P1 improves diagnosis without changing semantics too aggressively;
- P2 is the most stateful and should land after observability exists.

## Open Questions

1. Should job detail fetching happen only on terminal event, or also when a user opens a pending dependency chip?
2. Should duplicate suppression ignore `queued` jobs that never reached `waiting_for_runtime_lock` within a very short threshold, or should that remain watchdog-owned?

## Decisions

- No-op installs are recorded as `status = succeeded`, `changed = false`, and a precise `status_detail`.
- `already_satisfied` should render as a lighter success/information receipt, not as a failure or warning.
- P0 uses one aggregated floating chip, not one chip per dependency job.
- P0 does not change workboard behavior.

## Recommended Immediate Scope

If only one slice is implemented now, implement:

- no-op detection for conda installs;
- `changed` plus `status_detail` in the backend success result;
- symmetric frontend success notice handling in `ProjectWorkspace.tsx`;
- single-line floating chip for active dependency jobs.

That is the true minimal fix for the observed tidyverse experience and does not require the larger observability or watchdog refactor to deliver user-visible value.
