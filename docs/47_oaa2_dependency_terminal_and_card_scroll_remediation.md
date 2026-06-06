# 47. OAA-2 Dependency Terminal Drift And Card Scroll Remediation

Status: remediation plan.

Date: 2026-06-05

Related:

- `docs/44_runtime_dependency_install_visibility_and_liveness_plan.md`
- `docs/46_runtime_dependency_terminal_receipt_contract.md`
- `docs/45_auto_dependency_failure_signal_consumption_review.md`

## Summary

The `oaa-2` `tmap` install exposed a deeper split in the runtime dependency
terminal chain:

- the live REST endpoint reported the dependency job as `succeeded`;
- `workspace/oaa-2/chat/runtime_dependency_jobs.json` still recorded the same
  job as `running/running_subprocess`;
- the initiating manual chat session did not contain the expected
  `depjob_terminal_{job_id}` receipt message;
- workboard / flow blocker derivation continued to treat persisted `running`
  dependency jobs as active blockers.

This is not the documented product behavior. Runtime dependency installation is
allowed to become an async boundary, but it must not lock the frontend. The user
must still be able to inspect cards, switch panels, and continue normal
interaction while the dependency job runs.

The same review also identified a separate but user-visible scroll bug: when the
cursor is inside a card detail or card page scroll area, wheel input can still
move the outer workspace / canvas scrollbar. CSS `overscroll-behavior` is
present in some places, but the actual scroll containers are not consistently
height-constrained and the card wheel handler does not stop propagation while
the inner area is scrollable.

This document turns both findings into one implementation plan.

## Observed OAA-2 State

Project: `oaa-2`

Dependency job:

```text
depjob_a8bc3dd736444b3ca3b76ad8559e4a44
runtime: R_env
ecosystem: R
package: tmap
```

Observed terminal REST state:

```text
status = succeeded
phase = succeeded
message = Dependencies installed.
changed = true
status_detail = install_completed
finished_at = 2026-06-05T11:08:52Z
```

Observed persisted state:

```text
status = running
phase = running_subprocess
finished_at = null
result = null
error = null
```

Observed chat session state:

```text
latest manager message = "直接安装 `tmap`... 到 `R_env`"
tool = install_runtime_dependencies
tool status = done
missing message id = depjob_terminal_depjob_a8bc3dd736444b3ca3b76ad8559e4a44
```

Observed frontend behavior:

- auto mode was disabled;
- the UI still felt locked / hard to switch during or after dependency install;
- backend logs showed repeated session and manager-auto traffic around the same
  project/session;
- workboard could still derive active dependency items from stale persisted job
  state.

## Intended Dependency Install Behavior

`install_runtime_dependencies` should behave like card background work:

1. Manager starts the dependency job.
2. The chat turn ends at an async boundary.
3. A small chip / notice shows active dependency progress.
4. The user can still inspect cards, workboard, files, results, settings, and
   chat.
5. Terminal state is published through one idempotent terminal path.
6. Manual sessions receive one stable terminal receipt message unless the
   session is the active auto owner.
7. Auto owner sessions rely on auto wake summary instead of receiving a
   duplicate manual receipt.

Important distinction:

- "Do not foreground-poll the job in the same Manager turn" is intended.
- "Disable or freeze frontend navigation while the job runs" is not intended.

## Root Cause Hypothesis

The most likely backend failure mode is an exception after in-memory terminal
mutation but before durable persistence and terminal publish.

Current `_run()` success path mutates the job first:

```python
job.status = "succeeded" if ok else "failed"
job.phase = "succeeded" if ok else "failed"
job.result = result
job.finished_at = utc_now()
```

Then it performs side effects:

```python
self.background_task_service.update_task(...)
self._persist_project_jobs_locked(job.project_id)
self._publish_terminal_chat_receipt(job)
```

If `background_task_service.update_task(...)` or
`_persist_project_jobs_locked(...)` raises after the in-memory job has already
been marked terminal, the background Future can fail without an explicit log at
the dependency job layer. The process memory then reports terminal success, but
disk and chat receipt remain stale.

This explains the observed split:

- REST uses `RuntimeDependencyJobService.get_for_project()`, which preserves an
  existing in-memory job and therefore returns `succeeded`;
- workboard and flow blockers read `chat/runtime_dependency_jobs.json`
  directly and therefore still see `running`;
- `_publish_terminal_chat_receipt()` is never reached, so the manual session
  has no terminal message;
- frontend exact-ID polling never finds the terminal receipt.

## Secondary Frontend Pressure

Two frontend patterns can amplify the visible lock-up:

1. `useManagerAuto()` polls every 4 seconds unconditionally, even when auto is
   disabled.
2. `ManagerChatPanel` has a hydrate/save loop shape where remote session data
   can update local `messages`, and local `messages` can schedule a save back to
   the server.

The session save storm may not be the original dependency terminal bug, but it
can make navigation and panel switching feel blocked while the project is also
trying to refetch workboard / manager-auto / chat state.

## Card Scroll Root Cause

There are two scroll surfaces:

### Card Detail Panel

`CardDetailPanel` renders:

```tsx
<section className="panel">
  <div className="panel-body meta-grid">...</div>
</section>
```

`.panel-body` has:

```css
flex: 1;
overflow: auto;
overscroll-behavior: contain;
```

But this only works when the parent height chain is constrained and the panel
itself participates in flex sizing. Current issues:

- the panel does not have a dedicated `card-detail-panel` class;
- the panel is not forced to `flex: 1; min-height: 0; max-height: 100%`;
- desktop advanced wraps it in `.card-detail-panel-shell`, but the inner panel
  is not explicitly constrained;
- mobile renders `CardDetailPanel` directly without the shell.

Result: the intended `.panel-body` may not be the actual scroll container, so
wheel input continues to the outer page/workspace.

### Module Card Inner Pages

`ModuleCard` has a wheel handler around `.file-bag-paper-slot`.

Current behavior:

```ts
const canScrollInside = (wantDown && !atBottom) || (!wantDown && !atTop);
if (canScrollInside) return;
```

When the inner `.page-content-scroll` can scroll, the handler returns without
`preventDefault()` or `stopPropagation()`. Native scrolling may move the inner
area, but the wheel event still bubbles to outer containers such as
`.specialist-canvas`. If the outer canvas also has available scroll, the user
can see both scrollbars move.

## Goals

- Dependency terminal state must be durable before the system exposes or relies
  on terminal completion.
- A terminal job must not remain `running` in persisted JSON while REST reports
  `succeeded`.
- Manual-session terminal receipt publish may be attempted multiple times, but
  it must result in one visible stable receipt message per job ID via upsert
  semantics.
- Workboard / flow blockers must not derive active blockers from stale persisted
  dependency jobs when the live service knows the job is terminal.
- Auto-disabled projects must not keep polling manager-auto every 4 seconds.
- Chat session hydration must not cause save storms.
- Wheel input over an inner card/detail scroll area must not move the outer
  workspace unless the intended ModuleCard page-switch behavior applies.

## Non-Goals

- Do not make Manager poll dependency status inside the same turn after
  `install_runtime_dependencies` starts.
- Do not add new REST endpoints for dependency status.
- Do not add a second explicit workboard terminal-signal persistence path.
- Do not globally disable outer workspace scrolling.
- Do not remove card page switching by wheel; only make it deterministic and
  non-leaky.

## Backend Remediation Plan

### 1. Make Terminal Finalization Exception-Safe

Create one internal terminal finalization path that owns:

```text
mutate job terminal fields
persist runtime_dependency_jobs.json
update background task
publish terminal receipt / event / wake
```

The key implementation rule is:

- no exception after in-memory terminal mutation may silently skip persistence
  and terminal publish.

Practical shape:

```python
terminal_job: RuntimeDependencyJob | None = None
publish_job: RuntimeDependencyJob | None = None

with self.lock:
    # mutate terminal fields
    terminal_job = job
    try:
        self._persist_project_jobs_locked(project_id)
    except Exception:
        logger.exception(...)
        # keep enough state for watchdog/self-heal retry
    try:
        self.background_task_service.update_task(...)
    except Exception:
        logger.exception(...)

if terminal_job is not None and persisted:
    self._publish_terminal_chat_receipt(terminal_job)
```

Preferred ordering:

1. mutate job terminal fields;
2. persist dependency job state;
3. update background task as best effort;
4. publish terminal event / receipt / background wake.

Rationale:

- dependency job state is the source of workboard derivation;
- background task update should not prevent dependency job durability;
- terminal publish should happen after dependency job persistence.

### 2. Add Terminal Drift Self-Heal

Add a reconciliation pass that detects:

```text
in-memory job status is succeeded/failed
persisted JSON status for same job is queued/running/waiting/launching
```

When detected:

1. overwrite the persisted job record from in-memory state;
2. call `_publish_terminal_chat_receipt(job)` outside the lock;
3. log a structured warning with `project_id`, `job_id`, memory status, and
   persisted status.

Trigger this self-heal from:

- `_reconcile_active_jobs()`;
- `get_for_project()` when it sees memory terminal but disk stale;
- startup `reconcile_orphaned_active_jobs()`.

This converts existing bad state into a recoverable condition instead of a
permanent stale blocker.

### 3. Keep Publish Idempotent

Continue using:

```text
message id = depjob_terminal_{job_id}
timeline id = depjob_terminal_timeline_{job_id}
ChatSessionService.upsert_message()
```

Do not reintroduce timestamped receipt IDs.

Auto owner skip must stay before chat upsert, but it must not skip project event
or background terminal notification.

### 4. Workboard Reads Should Be Fresh Enough

Do not add a separate workboard signal file. Instead:

- before deriving dependency workboard items, run the dependency job self-heal;
- ensure `runtime_dependency_jobs.json` is the durable truth after self-heal;
- keep workboard derivation simple and file-based after that.

If importing `RuntimeDependencyJobService` into `BackgroundWorkboardService`
would create a circular dependency, expose a narrow callback or reconciliation
service function from the API/service wiring layer.

## Frontend Remediation Plan

### 1. Stop Unconditional Manager-Auto Polling

Change `useManagerAuto()` from unconditional:

```ts
refetchInterval: 4_000
```

to data-dependent polling:

```ts
refetchInterval: (query) => {
  const state = query.state.data?.state;
  return state?.enabled || state?.wake_in_flight ? 4_000 : false;
}
```

Confirm the exact callback signature against the installed TanStack Query
version before landing the code. Both v4 and v5 support functional intervals,
but the callback argument shape differs slightly.

SideNav already uses conditional polling; workspace should follow the same
principle.

### 2. Harden Chat Session Hydration

`ManagerChatPanel` should not save immediately after adopting server state.

Required behavior:

- compare incoming server signature with current local signature before calling
  `setMessages`;
- set a remote-hydration guard for session-query hydration, not only SSE
  message_upsert;
- avoid putting unstable mutation objects in save effect dependencies;
- do not call `saveChatSession` when the only change is server normalization;
- include a regression test or instrumented dev assertion for "one refetch does
  not cause a PUT unless the user edits local state".

### 3. Keep Dependency Receipt Poll Thin

Keep exact ID matching:

```ts
m.id === `depjob_terminal_${jobId}`
```

Do not add a second completion heuristic. The exact receipt ID remains the
contract. If `getRuntimeDependencyJob()` reports terminal but the exact receipt
is absent after a normal chat-session refetch, treat that as a contract
violation:

- surface an explicit developer-facing error state or log entry with
  `project_id`, `session_id`, and `job_id`;
- keep the dependency chip state derived from the terminal job response;
- do not infer completion from message count, prefix matches, or generic manager
  messages;
- do not synthesize a fake receipt in the frontend;
- do not hide the problem behind a non-blocking fallback notice.

## Scroll Remediation Plan

### 1. Add Dedicated Card Detail Classes

Change the panel markup to expose specific hooks:

```tsx
<section className="panel card-detail-panel">
  <div className="panel-body meta-grid card-detail-panel-body">
```

Apply the same class for the empty state.

CSS:

```css
.card-detail-panel {
  flex: 1 1 auto;
  min-height: 0;
  max-height: 100%;
  overflow: hidden;
}

.card-detail-panel-body {
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior-y: contain;
}

.card-detail-panel-shell > .card-detail-panel {
  flex: 1 1 auto;
  min-height: 0;
}
```

For mobile, use the existing workspace/header/mobile-tab layout variables or a
bounded wrapper height. The value below is only a placeholder if no layout
variable is available yet:

```css
.mobile-content .card-detail-panel {
  max-height: calc(100vh - var(--mobile-workspace-offset));
}
```

The important contract is that the detail body becomes the actual vertical
scroll container. Do not hardcode a magic pixel offset as the final runtime
contract.

### 2. Wrap Mobile Card Detail In The Same Shell

Desktop already uses:

```tsx
<div className="card-detail-panel-shell">
  <CardDetailPanel ... />
</div>
```

Mobile should use the same wrapper or an equivalent mobile-specific shell. This
prevents desktop and mobile scroll behavior from diverging.

### 3. Rely On CSS Overscroll For Pure Scroll Containers

For scroll containers without custom wheel behavior, such as `CardDetailPanel`
body and ordinary `.panel-body` content, use CSS rather than JavaScript wheel
interception:

```css
overscroll-behavior-y: contain;
```

This is sufficient only when the height chain is bounded. Therefore the main
fix for `CardDetailPanel` is the dedicated panel/body classes and mobile shell
above, not a JS wheel handler.

Do not add `preventDefault()`, `stopPropagation()`, or manual `scrollTop`
assignment to pure scroll containers. That would move normal scrolling from the
browser compositor path onto the main thread.

### 4. Preserve ModuleCard Minimal Wheel Handling

`ModuleCard` is different from `CardDetailPanel` because it already has custom
wheel behavior for page switching. Keep the optimized strategy:

- when the inner `.page-content-scroll` can scroll, return early and let the
  browser handle native scrolling;
- do not call `stopPropagation()`;
- call `preventDefault()` only on ticks that actually trigger a page switch or
  are inside the page-switch cooldown;
- rely on `.page-content-scroll { overscroll-behavior-y: contain; }` and the
  bounded card height to prevent scroll chaining;
- do not reintroduce a helper that manually assigns `scrollTop` on every wheel
  event.

This preserves the recent ModuleCard performance fix while still making the
scrollbar choice depend on cursor location.

## Test Plan

### Backend Unit Tests

Add tests covering:

- normal dependency success persists terminal JSON and upserts receipt;
- normal dependency failure persists terminal JSON and upserts receipt;
- exception in background task update after job terminal mutation does not leave
  persisted job `running`;
- exception in publish path is logged and does not roll back persisted terminal
  state;
- self-heal converts memory terminal + disk running into disk terminal and
  publishes the stable receipt;
- duplicate terminal publish keeps one `depjob_terminal_{job_id}` message.

### Frontend Tests / Manual Checks

Manual checks are acceptable if there is no existing browser test harness:

- start an R dependency install from a manual session;
- switch to tasks, advanced, files, and results while the install is active;
- verify no full-page lock and no repeated session PUT storm;
- after terminal success, verify chip dismisses and workboard no longer shows
  `runtime_dependency_install_running`;
- open card detail with content taller than the panel and scroll with cursor
  over the detail body;
- verify only detail body scrolls, not the outer workspace;
- open an active module card page with inner overflow and scroll with cursor
  over `.page-content-scroll`;
- verify only the inner card page scrolls until page switch boundary logic
  intentionally changes pages;
- move cursor outside the card and verify outer canvas scrolls normally.

### Build / Regression Commands

Backend:

```bash
PYTHONPATH=backend .venv/backend/bin/python -m unittest discover -s backend/tests
```

Frontend:

```bash
cd frontend && npm run build
```

Live service smoke:

```bash
curl -fsS http://127.0.0.1:18001/healthz
curl -fsS http://127.0.0.1:18001/api/projects/oaa-2/runtime-dependency-jobs/<job_id>
curl -fsS http://127.0.0.1:18001/api/projects/oaa-2/work-order
```

## Implementation Order

1. Add backend tests that reproduce terminal drift by injecting an exception
   after in-memory terminal mutation.
2. Refactor dependency terminal finalization so persistence and publish are
   exception-safe and logged.
3. Add terminal drift self-heal and wire it into reconcile / `get_for_project`
   / startup orphan reconciliation.
4. Add a fixture-backed self-heal test for `memory=succeeded` plus
   `disk=running`; assert persisted JSON is overwritten and the stable receipt
   is published.
5. Stop unconditional manager-auto polling.
6. Harden chat session hydration/save guard.
7. Add card detail dedicated scroll classes and mobile shell.
8. Preserve ModuleCard's minimal wheel handling and verify CardDetailPanel uses
   CSS bounded scrolling only.
9. Run backend tests, frontend build, and live smoke checks.
10. Use the real `oaa-2` stale `tmap` job only as a live smoke check after the
    automated self-heal test passes.

## Acceptance Criteria

- A dependency job cannot be `succeeded` in REST while the persisted job remains
  `running` after reconcile/self-heal.
- Manual dependency installs result in one visible stable terminal receipt
  message, even if terminal publish is retried.
- Workboard no longer shows a running dependency item for terminal jobs.
- Auto-disabled projects do not poll manager-auto every 4 seconds forever.
- While a dependency job is active on `oaa-2`, switching between tasks,
  advanced, files, and results updates the selected view immediately without
  waiting for dependency-job completion or blocking on refetches.
- After the initial manager-auto query settles, auto-disabled sessions do not
  start interval-based manager-auto polling.
- When a chat session is idle, with no user edits, active stream, compaction, or
  backend receipt upsert, frontend hydration does not issue chat session PUT
  requests.
- Cursor over card detail scrolls card detail only.
- Cursor over module card inner page scrolls that inner page only or triggers
  the intended card page switch; it does not also scroll the outer canvas.
- Cursor outside the card scrolls the outer workspace normally.
