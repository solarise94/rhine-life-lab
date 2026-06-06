# Runtime Dependency Terminal Receipt Contract

Status: remediation plan.

Date: 2026-06-05

Related:

- `docs/31_install_runtime_dependencies_contract.md`
- `docs/42_oaa2_terminal_completion_summary_wake.md`
- `docs/44_runtime_dependency_install_visibility_and_liveness_plan.md`
- `docs/45_auto_dependency_failure_signal_consumption_review.md`

## Summary

`install_runtime_dependencies` is now a real background task, but its terminal
visibility is split across several partial channels:

- project events drive chip / notice updates;
- dependency job polling compensates for missed project events;
- chat session messages provide the user-visible receipt in manual sessions;
- auto wake handles owner-session follow-up summaries;
- workboard derives runtime dependency items from persisted job state.

The current failure mode is not that any one channel is completely wrong. The
problem is that dependency job terminalization does not have one idempotent
publish contract. Normal `_run()` completion calls the chat receipt path, while
reconcile / watchdog paths can terminalize the job without writing the manual
session receipt.

This document defines the minimal contract for:

```text
dependency job terminal -> project event + manual-session receipt + auto wake
```

Workboard should keep deriving dependency items from
`chat/runtime_dependency_jobs.json`; it should not receive a second explicit
terminal-signal write path.

## Goals

- Every runtime dependency job terminal path must publish the same terminal
  side effects.
- The manual chat receipt must be idempotent.
- Repeated terminal publish for the same job must not create duplicate chat
  bubbles.
- Auto owner sessions must not receive the manual receipt, because auto wake
  produces the follow-up summary.
- Frontend polling must wait for the exact terminal receipt message ID, not infer
  completion from message count or prefix-only matches.
- Reconcile paths must publish outside `RuntimeDependencyJobService.lock`.

## Non-Goals

- Do not add a separate persisted workboard terminal signal. Existing workboard
  dependency items are already derived from persisted dependency jobs.
- Do not change the global semantics of `ChatSessionService.append_messages`.
- Do not move dependency install status into card run models.
- Do not make Manager poll dependency status in the same chat turn after a
  background install has started.
- Do not add `source_session_id` / `source_card_id` top-level persistence in this
  pass. Continue reading `payload.source.session_id` unless another consumer
  proves the field needs promotion.

## Current Chain

### Submit

1. `ManagerChatPanel.submit()` calls `/chat-stream`.
2. `manager-agent` invokes the `install_runtime_dependencies` tool.
3. `ManagerBlueprintTools.install_runtime_dependencies()` validates the request
   and injects `session_id` into `payload.source.session_id`.
4. `RuntimeDependencyJobService.submit()` creates a background task and a
   `RuntimeDependencyJob`.
5. The tool response returns `job_id`.
6. `manager-agent` stores `job_id` on the per-turn async boundary and sends it
   back in response metadata:

```text
metadata.async_boundary_tool = "install_runtime_dependencies"
metadata.async_boundary_job_id = <job_id>
```

### Display

1. Project SSE receives `runtime_dependency_job_changed`.
2. `ProjectWorkspace` updates `dependencyJobsByProject`.
3. `DependencyJobChip` renders active / terminal state.
4. `DependencyJobChip` also polls active jobs as a compensation path if terminal
   project events are missed.

### Completion

1. The job handler returns success / failure, or an exception is caught.
2. The job status, phase, result, error, and background task record are updated.
3. Project event is emitted.
4. Manual session receipt is appended today only on the normal `_run()` paths.
5. Auto background terminal notification is emitted.

### Session Message

Manual sessions do not subscribe to the session EventSource continuously. The
frontend therefore starts a short compensation poll after an async-boundary
dependency install response. The poll refetches the chat session until the
terminal receipt message is present.

Auto owner sessions do subscribe to session events, but dependency terminal
receipts should be skipped for them to avoid duplicating the auto wake summary.

## Required Contract

### Stable Receipt ID

Use one stable message ID per dependency job:

```text
receipt_message_id = depjob_terminal_{job_id}
timeline_item_id = depjob_terminal_timeline_{job_id}
```

Do not include timestamps in either ID.

Rationale:

- multiple terminal paths may call the publish helper;
- watchdog / reconcile may repeat a terminal publish;
- stable IDs let `ChatSessionService.upsert_message()` replace the same bubble
  instead of appending duplicates;
- frontend can wait for an exact ID.

### Terminal Receipt Publisher

Reuse the existing chat-receipt path instead of adding another public surface.
Rename and expand `_append_terminal_chat_message(job)` into one lock-free
terminal receipt publisher:

```python
def _publish_terminal_chat_receipt(self, job: RuntimeDependencyJob) -> None:
    self._emit_project_event(job)

    # Manual-session chat receipt branch:
    # - skip if chat_session_service is unavailable;
    # - skip if payload.source.session_id is missing;
    # - skip chat upsert only when the session is the active auto owner;
    # - otherwise upsert depjob_terminal_{job_id}.

    self._notify_background_terminal(job.project_id, job_id=job.job_id)
```

Rules:

- call only after job state and background task state have been persisted;
- call only outside `self.lock`;
- make the helper tolerant of missing services;
- keep project event, manual chat receipt, and auto wake together so no terminal path
  forgets one side effect.
- auto-owner skip only skips the chat upsert. It must not skip project event
  emission or background terminal notification.
- repeated `_notify_background_terminal(project_id, job_id=...)` calls are
  allowed from this publisher. Duplicate auto-wake idempotency belongs in
  `ManagerAutoService.notify_background_task_terminal`, keyed by terminal
  `job_id` / wake state, not in this stateless publisher.

### Chat Receipt Upsert

Do not change `ChatSessionService.append_messages`.

Use `ChatSessionService.upsert_message()` for dependency terminal receipts.

Rationale:

- `append_messages()` currently means append if missing and skip duplicate IDs;
- other callers may depend on that append-only behavior;
- `upsert_message()` already provides the exact "replace same ID, append if new"
  behavior needed for idempotent receipts;
- each upsert emits `message_upsert` SSE and increments the session revision.

Auto-owner skip must happen before `upsert_message()` is called. Repeated
publish for an auto owner session must not bump session revision or emit
redundant session SSE. The publish function must still continue to
`_notify_background_terminal(...)` so auto wake is not lost.

### Receipt Content

Receipt content should be built by one pure helper.

Inputs:

```text
status
changed
status_detail
error_tail
```

Recommended output:

```text
succeeded + changed false or already_satisfied:
  依赖已满足，无需安装。

succeeded + changed true / unknown:
  依赖安装完成。

failed + error_tail:
  依赖安装失败：{error_tail}

failed + no error_tail:
  依赖安装失败。

interrupted by backend restart:
  依赖安装被后端重启中断。
```

`error_tail` should be the final non-empty line from `job.error`, not the full
traceback.

`reconcile_orphaned_active_jobs()` currently uses the English error string:

```text
Runtime dependency job was interrupted by backend restart.
```

The implementation should avoid surfacing that raw English sentence in the chat
receipt. Either set `job.error` to `依赖安装被后端重启中断。` when reconcile marks
the job interrupted, or have `_format_terminal_content(job)` recognize the
interruption text and render the dedicated Chinese receipt.

### Frontend Exact Match

`ManagerChatPanel.scheduleAsyncBoundaryPoll(jobId)` should wait for:

```ts
m.id === `depjob_terminal_${jobId}`
```

Remove prefix fallback such as:

```ts
m.id.startsWith("depjob_terminal_")
```

The frontend should not infer terminal completion from message count, arbitrary
new terminal prefixes, or generic dependency job status. The terminal receipt ID
is the contract.

## Terminal Paths To Cover

### Normal handler success / structured failure

Path:

```text
RuntimeDependencyJobService._run()
handler returns result
```

Expected behavior:

1. lock: update job status, phase, result, status_detail, changed, error;
2. lock: update background task;
3. lock: persist jobs;
4. lock released;
5. `_publish_terminal_chat_receipt(job)`.

### Handler exception

Path:

```text
RuntimeDependencyJobService._run()
handler raises exception
```

Expected behavior:

1. lock: mark failed;
2. lock: set `job.error` to formatted exception;
3. lock: update background task;
4. lock: persist jobs;
5. lock released;
6. `_publish_terminal_chat_receipt(job)`.

### Startup reconcile / orphaned active jobs

Path:

```text
RuntimeDependencyJobService.reconcile_orphaned_active_jobs()
```

Expected behavior:

1. lock: load active persisted jobs;
2. lock: mark jobs with no live future as failed / interrupted;
3. lock: update background tasks;
4. lock: persist jobs;
5. lock released;
6. for each unique terminalized job, call `_publish_terminal_chat_receipt(job)`.

### Watchdog future-done reconcile

Path:

```text
RuntimeDependencyJobService._reconcile_active_jobs()
RuntimeDependencyJobService._reconcile_single_job()
```

Expected behavior:

1. `_reconcile_single_job()` must not publish while holding `self.lock`;
2. it should update job state and return the terminalized job, or return `None`
   when no terminalization happened;
3. `_reconcile_active_jobs()` should release `self.lock`;
4. then publish each terminalized job with
   `_publish_terminal_chat_receipt(job)`.

## Workboard Boundary

Do not add `_record_workboard_terminal_signal()`.

Current workboard dependency items are already derived from:

```text
chat/runtime_dependency_jobs.json
```

The derived items are sufficient:

- `runtime_dependency_install_running` in deferred lane;
- `runtime_dependency_install_succeeded` in completed lane;
- `runtime_dependency_install_failed` in needs_manager lane.

Adding a second explicit workboard terminal signal would create two sources of
truth:

- persisted dependency job state;
- persisted workboard signal state.

That would require extra invalidation and reconciliation rules without improving
the manual-session receipt contract.

## Minimal Final Chain

```text
submit(job)
  -> job_id in async boundary metadata
  -> chip active through project SSE

job terminal
  -> all terminal paths call the same lock-free _publish_terminal_chat_receipt(job)
       -> _emit_project_event(job)
            -> chip / notice terminal
       -> manual receipt branch
            -> upsert depjob_terminal_{job_id}
            -> skip only chat upsert for auto owner
       -> _notify_background_terminal(project_id, job_id=job_id)
            -> auto wake

manual frontend
  -> scheduleAsyncBoundaryPoll(job_id)
  -> refetch session with backoff
  -> stop when exact depjob_terminal_{job_id} is present
```

## Frontend Poll Backoff

Current polling is acceptable but can be made thinner.

Recommended schedule:

```text
initial delay: 3s
then: 15s fixed interval until max attempts / max duration
```

The exact-ID contract keeps this simple; more complex backoff has little value.
The important rule is:

- exact receipt ID stops polling immediately;
- timeout stops polling without changing the local message state;
- normal navigation or session refetch still recovers any late receipt.

## Tests

### 1. Normal terminal receipt

Given:

- a dependency job started with `payload.source.session_id`;
- handler returns success or structured failure.

Assert:

- `ChatSessionService.upsert_message()` is called;
- message id is `depjob_terminal_{job_id}`;
- timeline id is `depjob_terminal_timeline_{job_id}`;
- content matches success / no-op / failure classification;
- project event is still emitted;
- background terminal notification is still emitted.

### 2. Reconcile terminal receipt

Given:

- an active persisted job with `payload.source.session_id`;
- `reconcile_orphaned_active_jobs()` marks it interrupted.

Assert:

- `_publish_terminal_chat_receipt(job)` is called after the lock-protected state
  update;
- chat receipt id is `depjob_terminal_{job_id}`;
- content includes the restart interruption message;
- project event and auto terminal notification still happen.

Also cover `_reconcile_single_job()` / watchdog future-done if practical.

### 3. Idempotent duplicate publish

Given:

- the same terminal job is published twice.

Assert:

- session contains only one `depjob_terminal_{job_id}` message;
- the second publish replaces the same message rather than appending another;
- auto owner skip produces no session message and does not bump session
  revision;
- auto owner skip still allows background terminal notification to fire;
- duplicate background terminal notifications for the same `job_id` do not
  create duplicate auto wake follow-ups.

## Implementation Order

1. Change receipt ID and timeline ID to stable job-based IDs.
2. Switch dependency terminal receipt from `append_messages()` to
   `upsert_message()`.
3. Rename and expand `_append_terminal_chat_message(job)` into
   `_publish_terminal_chat_receipt(job)`, reusing `_emit_project_event(job)`,
   `ChatSessionService.upsert_message()`, and `_notify_background_terminal(...)`.
4. Add `_format_terminal_content(job)` and handle backend-restart interruption
   with the Chinese receipt text `依赖安装被后端重启中断。`.
5. Replace normal `_run()` terminal side-effect calls with
   `_publish_terminal_chat_receipt(job)`.
6. Refactor `reconcile_orphaned_active_jobs()` to call
   `_publish_terminal_chat_receipt(job)` outside the lock.
7. Refactor `_reconcile_active_jobs()` / `_reconcile_single_job()` so watchdog
   terminal publish also happens outside the lock.
8. Confirm `ManagerAutoService.notify_background_task_terminal` is idempotent
   for repeated calls with the same `job_id`. If it is not, add the guard there
   rather than adding state to `_publish_terminal_chat_receipt(job)`.
9. Change frontend session polling to exact receipt ID match.
10. Change frontend polling interval to 3s initial delay + 15s fixed interval.
11. Add the three test groups above.

## Acceptance Criteria

- A manual chat-triggered dependency install always receives one terminal
  manager message when the job reaches terminal state.
- The message id is exactly `depjob_terminal_{job_id}`.
- Repeated publish for the same job does not create duplicate chat bubbles.
- Auto owner sessions do not receive dependency receipt bubbles.
- Auto owner terminal publish still emits project events and auto wake
  notification.
- Repeated terminal publish for the same `job_id` does not create duplicate auto
  wake follow-ups.
- Backend-restart interruption receipts are rendered in Chinese, not as the raw
  English reconcile error string.
- Chip / notice terminal behavior still comes from project events and existing
  job polling compensation.
- Workboard continues to derive dependency items from
  `runtime_dependency_jobs.json`; no new persisted workboard signal layer exists.
- Backend tests cover normal terminal, reconcile terminal, and duplicate publish
  idempotency.
