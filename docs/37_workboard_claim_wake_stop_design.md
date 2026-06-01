# Workboard Claim, Wake, And Stop Design

Status: follow-up design note for Module 4.

Date: 2026-06-01

## Purpose

`docs/36_manager_workboard_prompt_contract.md` now defines Module 4: collapse Auto into a permission envelope over the same Workboard execution path.

Current code already implements a partial version of that model:

- workboard claim/submit is the main batch/frontier execution path;
- auto wake uses workboard revision and persisted wake events;
- `/auto <goal>` creates owner-scoped continuation with directives;
- claim leasing and wake idempotency exist in basic form.

What is still missing is a precise protocol for three control surfaces:

1. workboard claim/lease ownership;
2. wake dedupe / revision / retry semantics;
3. stop/cancel semantics after Auto becomes only a permission envelope.

This document captures those follow-up design changes.

## Current Code Baseline

### Claim / Lease

Current workboard claim behavior:

- `claim_workboard_item(..., lease_seconds=300)` marks a todo item `claimed`;
- the claim is session-owned via `claimed_by_session_id`;
- `claim_expires_at` is stored on the item;
- expired `claimed` or `processing` items are reset to `pending` by `_release_expired_claims`;
- `submit_claimed_workboard_items` temporarily moves claimed todo items to `processing` before calling `start_run`.

This is already enough for single-session optimistic claiming, but it is still implicit rather than a documented state machine.

### Wake Dedupe

Current wake dedupe behavior:

- `ManagerWakeService.enqueue` dedupes persisted events by `idempotency_key`;
- `BackgroundWorkboardService._revision_for_items` computes a workboard revision hash from lane/status/task/card/run/job/source identity;
- `ManagerAutoState.last_signaled_board_revision` prevents re-emitting `workboard_actionable` for the same revision;
- `ManagerWakeProcessor` skips stale actionable wakes if the current snapshot is no longer actionable;
- wake processor claim retry uses `stale_after_seconds=120`.

This is already a real protocol, but it is spread across services and not yet written down as one contract.

### Stop / Cancel

Current stop behavior:

- `POST /manager-auto/stop` disables the owner envelope;
- when `reason == "user_stop"` and `active_run_id` is set, backend also calls `cancel_run(...)`;
- there is no equivalent runtime dependency job cancel path;
- `active_run_id` / `active_job_id` are compatibility fields in `ManagerAutoState`, not authoritative background truth.

So the current implementation still mixes:

- "stop autonomous continuation";
- "cancel active run";
- "clear compatibility runtime fields".

Module 4 should separate those concerns.

## Design Goals

After these follow-up changes:

- workboard claim ownership is explicit and recoverable;
- wake emission is idempotent and explainable by stable rules;
- stopping Auto only revokes continuation permission by default;
- cancelling runs or jobs requires explicit run/job cancellation actions;
- `ManagerAutoState` no longer needs `active_run_id` / `active_job_id` as primary truth.

## 37A: Workboard Claim / Lease State Machine

### Scope

This protocol applies only to persisted workboard consumption items such as `todo` lane items. It does not apply to derived `ready_to_start` projections.

### States

Suggested state model:

```text
pending
  -> claimed
  -> deferred
  -> blocked_for_user
  -> done

claimed
  -> processing
  -> pending        (lease expiry or explicit release)
  -> deferred
  -> blocked_for_user
  -> failed

processing
  -> done
  -> failed
  -> pending        (only if launch ownership was never durably handed off)

failed
  -> claimed
  -> pending

deferred / blocked_for_user
  -> pending

done
  -> terminal acknowledgement state; item may later disappear from merged view
```

### Lease Rules

1. `claimed` has a lease with `claim_expires_at`.
2. Lease expiry returns the item to `pending`.
3. `processing` should not share the same lease semantics forever.

Recommended change:

- when backend accepts launch ownership for a claimed todo item, either:
  - clear `claim_expires_at` immediately and treat `processing` as backend-owned transient state; or
  - replace the claim lease with a short launch-handoff timeout distinct from normal claim expiry.

The first option is simpler and better matches current behavior, because `processing` is only a short-lived bridge to `done`/`failed` around `start_run`.

### Ownership Rules

- only the claiming session may move a todo item from `claimed` to another mutable consumer state;
- lease expiry revokes that ownership automatically;
- `reopen_workboard_item` is an administrative/backend recovery path, not ordinary consumer flow.

### Non-Goals

- no multi-session shared editing protocol for one claimed todo item;
- no heartbeat channel is required in this pass;
- no durable "claimed by background worker" lane is required beyond `processing`.

## 37B: Wake Dedupe And Revision Protocol

### Event Classes

Treat wake events as belonging to three groups:

1. workboard-derived actionable wake:

   ```text
   kind = workboard_actionable
   idempotency_key = workboard:{project_id}:{revision}
   ```

2. directive-driven wake:

   ```text
   kind = directive_received
   idempotency_key = directive:{directive_id}
   ```

3. background terminal/blocking wake:

   ```text
   kind = <run/job specific event>
   idempotency_key = stable key derived from the terminal/blocking fact
   ```

### Revision Contract

Keep the current approach:

- compute one deterministic revision from merged workboard items;
- include only fields that materially affect work consumption identity:
  - item id;
  - lane;
  - status;
  - task/card/run/job ids;
  - source item id.

Do not include free-text summaries, timestamps, or transient display copy in the revision hash.

### Emission Rule

Emit `workboard_actionable` only when all are true:

- current envelope has `wake_allowed` / compatibility `enabled`;
- current envelope has `consume_workboard`;
- current snapshot `has_actionable == true`;
- current envelope is not in a live Manager turn state such as `running` or `thinking`;
- `last_signaled_board_revision != revision`.

### Processor Rule

When a wake is claimed:

- if it is no longer actionable at processing time, mark it `done` without running a new Manager turn;
- do not synthesize a replacement wake unless the latest revision separately qualifies for emission.

This preserves idempotency without forcing every stale wake to become a failure.

### Retry Rule

Keep retry-by-stale-claim for wake processing:

- claimed `running` wake events may be re-claimed after a processor stale timeout;
- the stale timeout is an execution safety parameter, not part of the user-facing contract.

### Optional Future Change

If wake history grows too large, add retention/pruning by terminal age. That is storage hygiene, not part of the semantic contract.

## 37C: Stop / Cancel Semantics Matrix

### Current Problem

`/auto stop` currently stops the envelope and may also cancel `active_run_id`. That makes a permission action mutate run execution as a side effect.

### Target Matrix

Use this split:

| User action | Permission envelope | Running card run | Runtime dependency job |
| --- | --- | --- | --- |
| `/auto stop` or `/auto off` | disable wake/continuation | unchanged | unchanged |
| explicit cancel run | unchanged unless caller also stops envelope | cancel that run | unchanged |
| explicit cancel dependency job | unchanged unless caller also stops envelope | unchanged | cancel that job |
| stop + cancel current run | disable wake/continuation | cancel selected run | unchanged |

### Required Change

Default `/auto stop` should mean:

```text
stop future wake-driven continuation
do not cancel running work unless the user explicitly asks for that too
```

That means:

- remove implicit `cancel_run` from plain `/manager-auto/stop`;
- add a separate explicit path if product wants "stop and cancel current run";
- do not infer job cancellation from envelope stop;
- add a new explicit runtime dependency job cancel API if product wants "stop and cancel current dependency repair job".

### Directive Cleanup

On stop:

- pending directives should be marked `superseded` or equivalent terminal status;
- future wakes must not consume old directives after the envelope is stopped.

### Compatibility

For a transition period:

- `enabled=false` may remain the compatibility representation of `wake_allowed=false`;
- stop messages may still say "exit auto mode";
- API names may remain `/manager-auto`.

But behavior should follow the matrix above, not the old mode semantics.

## 37D: Permission Envelope Field Migration

### Keep

- `owner_session_id`
- `state`
- `pending_directives`
- `started_at`
- `stopped_at`
- `stop_reason`
- `stop_message`
- `consume_workboard`

### Add

- `scope_objective: str | null`
- `wake_allowed: bool`
- `expires_at: str | null` (optional, if product wants bounded continuation)

### Mode And Budget Semantics

Current code still exposes `mode` with `continuous` / `once`.

Suggested direction:

- keep `mode` as an API compatibility field during migration;
- treat `mode="once"` as a compatibility shortcut for a very small continuation budget;
- move the real envelope semantics to:
  - `wake_allowed`;
  - `consume_workboard`;
  - `chain_count` / `max_chain_count` / `chain_limit_basis` for wake-count budget;
  - `expires_at` for wall-clock budget when time-bounded continuation is needed.

Budget split:

- `chain_count` family = count budget;
- `expires_at` = time budget;
- either limit being exhausted stops further autonomous continuation.

If a later pass removes `mode`, that removal should happen only after callers can express the same behavior through the explicit permission/budget fields above.

### Internal / Not Public Contract

- `last_wake_id`
- `last_signaled_board_revision`
- `chain_count`
- `max_chain_count`
- `chain_limit_basis`

These can stay as internal coordination fields and do not need to be framed as user-facing permission semantics.

### Deprecate As Primary Truth

- `active_run_id`
- `active_job_id`
- `view_workboard`

Notes:

- `active_run_id` / `active_job_id` may remain as cache fields during migration, but stop/cancel and wake gating should stop depending on them as authoritative truth;
- `view_workboard` is redundant once workboard is always the execution acquisition path.

## Tests

- claimed todo item expires back to `pending` after lease timeout;
- processing item does not silently re-open due to stale claim expiry once backend launch ownership is accepted;
- duplicate workboard actionable revisions do not enqueue duplicate wake events;
- stale actionable wake is marked done without running another Manager turn;
- directive wake dedupes by directive id;
- plain `/auto stop` disables future continuation but does not cancel a running run;
- explicit run cancellation still works without stopping the permission envelope;
- wake gating correctness does not depend on `active_run_id` / `active_job_id` once derived truth is in place;
- compatibility readers such as frontend/session status views still behave correctly when `active_run_id` / `active_job_id` are absent.

## Relationship To Other Docs

- `docs/35_oaa2_plan_accept_workboard_bug_review.md`: resolver and plan/accept consistency issues.
- `docs/36_manager_workboard_prompt_contract.md`: main repair modules, including Module 4 high-level target.
- This document: follow-up state-machine and control-protocol detail for Module 4.
