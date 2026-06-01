# OAA-2 Auto Wake Loop Review

Status: bug review note.

Date: 2026-06-01

## Summary

OAA-2 exposed a second auto/workboard wake issue after the earlier `/auto` command gap.

This plan assumes the `/auto <objective>` durable owner envelope from
`docs/38_oaa2_auto_command_wake_gap.md` has already been implemented. Doc 38
owns command parsing and authorization. This document only controls wake
frequency after auto is already enabled for an owner session.

After auto was eventually enabled, Manager entered a rapid chain of `workboard_actionable` wakes. The 35/36 workboard rectifier did not prevent this because it currently suppresses repeated wakes for the same workboard revision, but each Manager turn changed the workboard state and therefore produced a new revision.

This is not evidence that another hidden mechanism was waking Manager. The observed wake source was the persisted workboard wake queue.

## Observed OAA-2 Evidence

Project:

```text
workspace/oaa-2
```

Wake queue:

```text
workspace/oaa-2/chat/manager_wake_events.jsonl
```

The queue contains six `workboard_actionable` wakes:

| Wake | Created | Processed | Counts Summary |
| --- | --- | --- | --- |
| `wake_workboard_26a6a91b1dc3` | 2026-06-01T07:30:25Z | 2026-06-01T07:31:38Z | needs_manager=10, ready_to_start=2 |
| `wake_workboard_c2e160ff78c9` | 2026-06-01T07:31:38Z | 2026-06-01T07:32:24Z | running=1, todo=1, needs_manager=9, ready_to_start=1 |
| `wake_workboard_c46daa7b8781` | 2026-06-01T07:32:24Z | 2026-06-01T07:33:51Z | running=2, todo=1, needs_manager=9 |
| `wake_workboard_c6cffdc7c8c1` | 2026-06-01T07:33:51Z | 2026-06-01T07:34:34Z | running=2, needs_manager=1, blocked_for_user=7 |
| `wake_workboard_44346e2844c8` | 2026-06-01T07:34:34Z | 2026-06-01T07:35:20Z | running=1, needs_manager=1, ready_to_start=1, blocked_for_user=7 |
| `wake_workboard_9d3a1b145d0f` | 2026-06-01T07:35:20Z | 2026-06-01T07:35:43Z | running=2, blocked_for_user=7 |

Each wake was claimed and marked `done`, and each generated a corresponding chat session message:

```text
wake_response_wake_workboard_...
```

So these were real Manager turns, not stale queued events.

The final persisted auto state shows the chain:

```json
{
  "enabled": false,
  "owner_session_id": "session_65c671ef6eb9",
  "state": "cancelled",
  "started_at": "2026-06-01T07:30:25Z",
  "last_wake_id": "wake_workboard_9d3a1b145d0f",
  "chain_count": 6,
  "last_signaled_board_revision": 80298094136682,
  "stop_reason": "user_stop"
}
```

This means the chain stopped because the user stopped auto, not because a chain budget or rectifier stopped it.

## Current Wake Path

The main path is:

1. `ManagerAutoService.evaluate_workboard_and_maybe_signal(...)` reads `BackgroundWorkboardService.signal_snapshot(...)`.
2. If snapshot is actionable and revision differs from `last_signaled_board_revision`, it enqueues `workboard_actionable`.
3. `ManagerWakeProcessor` claims the event.
4. Processor runs a Manager turn with a workboard-first prompt.
5. Manager changes workboard/card/run state.
6. Processor calls `evaluate_workboard_and_maybe_signal(..., from_turn_settlement=True)`.
7. A new workboard revision can immediately enqueue the next wake.

There is a second settlement path from Manager sidecar:

```text
manager-agent notifyAutoTurnSettled -> POST /manager-auto/turn-settled
```

This also calls the same evaluation logic. Same-revision dedupe prevents exact duplicate enqueue, but this path is still another evaluator and should be treated as part of the wake contract.

## Why The 35/36 Rectifier Did Not Stop It

The existing rectifier has two main protections:

- `ManagerAutoState.last_signaled_board_revision` prevents repeated `workboard_actionable` events for the same revision.
- `ManagerWakeService.enqueue` dedupes events by idempotency key:

  ```text
  workboard:{project_id}:{revision}
  ```

Those protections work only when the workboard revision stays the same.

In OAA-2, each wake turn changed workboard state:

- claiming workboard items;
- completing items;
- blocking dependency failures for user;
- revising card input/output contracts;
- starting new card runs;
- cleaning up failed runs;
- changing running/todo/needs_manager/blocked counts.

Because `BackgroundWorkboardService._revision_for_items(...)` includes item identity, lane, status, task/card/run/job ids, and source item id, each of those mutations can produce a new revision.

So the rectifier saw each wake as legitimate:

```text
old revision != new revision
has_actionable == true
consume_workboard == true
```

This is a valid explanation of the OAA-2 behavior, but it is not the desired product behavior.

## Root Causes

### 1. Revision Dedupe Is Too Low-Level

Revision-level dedupe prevents exact duplicates. It does not coalesce semantically equivalent continuation states.

Example:

```text
needs_manager decreases from 10 to 9
```

That is a new revision, but it may not justify a new autonomous Manager turn if the remaining items are old dependency failures already summarized or blocked for user.

### 2. `has_actionable` Is Too Broad

Current `signal_snapshot(...)` counts these as actionable:

```python
todo items not processing
needs_manager items
actionable completed items
ready_to_start items
```

This means old failed dependency jobs, stale run failures, ready projections, and completed receipts can all keep the auto loop alive.

### 3. Wake Settlement Immediately Re-Enqueues

`ManagerWakeProcessor` calls:

```python
evaluate_workboard_and_maybe_signal(project_id, owner_session_id, from_turn_settlement=True)
```

at the end of each wake turn.

That makes the wake processor a self-chain:

```text
wake -> Manager turn -> workboard mutation -> new revision -> next wake
```

This is useful when the new revision exposes a truly new ready frontier. It is too aggressive when the new revision is only housekeeping or a partial cleanup of old blockers.

### 4. Ready Projections Can Be Counted While Already Claimed Or Running

OAA-2 had ready/todo/running transitions around the same cards. The workboard state showed a `ready_card` projection for DEG Visualization with `status=claimed`, plus todo/running activity for related cards.

The wake actionability check should not treat claimed, already-submitted, or already-running ready projections as fresh actionable work.

### 5. Completed Reviewed Runs Are Potentially Too Actionable

Current completed run items are derived for every reviewed run. `_completed_item_is_actionable(...)` returns true for most completed items unless the item explicitly opts out.

Completed run receipts should not by themselves wake Manager repeatedly. They should wake only when they create a new downstream actionable fact:

- downstream card becomes ready;
- dependency attention appears;
- review decision is needed;
- contract inconsistency appears.

### 6. Chain Budget Is Not Enforced As A Stop Guard

OAA-2 ended with:

```text
chain_count = 6
max_chain_count = 12
```

The code records chain count, but the observed behavior does not show a hard stop/check before enqueuing or processing more wakes.

Even if the threshold was not crossed here, the budget should be an explicit safety guard.

## Non-Causes

This incident was not primarily caused by:

- duplicate enqueue of the exact same workboard revision;
- `ManagerWakeService` failing to dedupe by idempotency key;
- only project-state SSE refreshes;
- only frontend session event streaming.

The persisted queue and `wake_response_*` messages show actual Manager wake turns.

## Recommended Fix Plan

### Implementation Contract

The fix should not add another ad-hoc wake throttle. The wake contract should
move from:

```text
new workboard revision + has_actionable -> enqueue
```

to:

```text
new semantic actionable frontier -> enqueue
```

Concrete ownership:

- `BackgroundWorkboardService.signal_snapshot(...)` owns classification of the
  current workboard into semantic wake classes.
- `ManagerAutoService.evaluate_workboard_and_maybe_signal(...)` owns comparing
  the semantic fingerprint against the last signaled fingerprint and enforcing
  chain budget before enqueue.
- `ManagerWakeProcessor` owns wake-turn execution and may ask for settlement
  reevaluation, but it must not bypass fingerprint/budget checks.
- `ManagerWakeService.enqueue(...)` remains a storage-level idempotency guard;
  it should not be the primary semantic loop control.

State additions should be explicit rather than inferred from revision:

```text
ManagerAutoState.last_signaled_workboard_fingerprint: str | None
ManagerAutoState.last_signaled_workboard_fingerprint_at: str | None
ManagerWakeEvent.payload_summary.fingerprint
ManagerWakeEvent.payload_summary.actionability
```

Keep `last_signaled_board_revision` for diagnostics/backward compatibility, but
do not use it as the only enqueue predicate.

Bootstrap rule:

- Existing projects may have `last_signaled_workboard_fingerprint=None`.
- Do not require a migration script. The first evaluate after deployment should
  compute the current fingerprint and persist it when a wake is signaled.
- Enqueue comparison must use fingerprint only. Revision is diagnostic metadata.
  A new revision with the same fingerprint must not enqueue another wake.

### Fix 1: Add Semantic Wake Fingerprint

Keep revision-level idempotency, but add a higher-level actionability fingerprint.

The fingerprint should be deterministic and derived from a normalized list of
fresh action units. It should include only work classes that justify a Manager
turn, for example:

- pending ready cards not claimed, not running, not already represented by active todo;
- newly failed runs not already completed/blocked/deferred by workboard state;
- newly actionable dependency repair outcomes with source context;
- explicit user directives.

It should exclude:

- old blocked-for-user items;
- already summarized dependency install failures;
- running-only changes;
- completed receipts that do not change downstream frontier.

Suggested normalized shape:

```json
{
  "startable_frontier": ["card:<card_id>"],
  "manager_attention": ["run:<run_id>:<issue_kind>", "dep:<coalesce_key>"],
  "directive": ["directive:<directive_id>"]
}
```

The digest should be computed after sorting all lists. Counts and revision can
remain in diagnostics, but must not be part of the fingerprint. This avoids a
loop where `needs_manager=10 -> 9` becomes a new wake even when the underlying
unhandled problem set is unchanged.

Open mapping detail:

- For `ready_card`, prefer `card_id` as the action unit.
- For failed runs, prefer `run_id + issue_kind` unless dependency failures are
  coalesced separately.
- For runtime dependency install failures, prefer a coalescing key, not `job_id`.
- For completed run receipts, produce no action unit by default.

Action units must be derived only from the current non-completed actionable
view, primarily `ready_to_start` and `needs_manager`. Do not derive action units
from `completed` receipts. This prevents old run ids from staying in the
fingerprint after a retry produces a new run.

### Fix 2: Narrow `has_actionable`

`signal_snapshot(...)` should distinguish:

```text
has_manager_actionable
has_startable_frontier
has_only_blocked_for_user
has_only_running
has_only_housekeeping
```

Then wake only on `has_manager_actionable` or `has_startable_frontier`.

Suggested snapshot shape:

```json
{
  "revision": 123,
  "counts": {},
  "actionability": {
    "has_manager_actionable": true,
    "has_startable_frontier": false,
    "has_only_blocked_for_user": false,
    "has_only_running": false,
    "has_only_housekeeping": false
  },
  "fingerprint": "sha1:...",
  "fingerprint_items": []
}
```

Compatibility rule:

- Existing call sites that only need a boolean may temporarily use
  `has_actionable = has_manager_actionable or has_startable_frontier`.
- New enqueue logic must use `fingerprint` and `actionability`, not the legacy
  boolean alone.

### Fix 3: Filter Ready Projections

Do not count `ready_to_start` items as actionable when:

- the ready item status is not `pending`;
- a todo item already exists for the same source item;
- a run is already active for that card;
- the card is no longer `planned` or otherwise startable.

Implementation note:

`_merge_items(...)` already removes a ready projection when a persisted todo
exists for the same `source_item_id`. That is necessary but not sufficient. The
snapshot classifier should also re-check active todo/running/card status because
derived ready projections can race with run submission and status settlement.

The ready-card action unit should be emitted only when the card is a fresh
pending startable frontier:

```text
lane == ready_to_start
status == pending
no active todo with source_item_id == ready item id
no running item/run for card_id
card is still startable according to flow/work order
```

### Fix 4: Completed Run Wake Policy

Completed reviewed runs should not default to actionable.

Suggested policy:

- mark `card_run_reviewed` completed items as non-wake receipts by default;
- separately derive fresh downstream-ready facts;
- if no downstream-ready or diagnostic fact exists, do not wake.

Implementation note:

The current default in `_completed_item_is_actionable(...)` treats every
completed item as actionable unless it is `runtime_dependency_install_succeeded`
or carries `payload.actionable_wake=false`. Reverse that default:

```text
completed item is not actionable unless payload.actionable_wake == true
```

Then make the producer of the completed item opt in only when the receipt
creates a distinct downstream fact. This keeps reviewed-run receipts visible in
the workboard without using them as autonomous wake fuel.

### Fix 5: Dependency Failure Coalescing

Runtime dependency install failures need coalescing:

- group repeated failures by runtime + package/root error;
- summarize once;
- mark old repeated failures as blocked/deferred/receipt;
- do not let each historical failed job independently keep `needs_manager` actionable.

OAA-2 had repeated package-not-found failures for:

```text
omicverse / pydeseq2
R_env / limma-family Bioconductor packages
```

These should have become one user-facing blocker summary, not repeated wake fuel.

Coalescing key should avoid per-job churn:

```text
runtime + normalized package set + normalized root cause
```

Examples:

- `python|omicverse,pydeseq2|package_not_found`
- `r|bioconductor:limma-family|repository_or_package_unavailable`

The classifier should emit one manager-actionable unit per coalesced key. After
Manager blocks or summarizes that key for the user, future identical failures
should become receipts/deferred items until a new root cause or package set
appears.

Persisting the handled coalescing keys is preferable to guessing from message
text. Candidate locations:

- workboard item payload/status, if the blocker is tied to an item;
- manager auto metadata, if the blocker is project-global;
- dependency state service, if the key belongs with dependency resolution.

Recommended first implementation:

- Persist the coalescing key and handled/summarized marker on the workboard item
  payload/status.
- Use stable payload field names:
  `payload.coalescing_key` and `payload.coalescing_handled=true`.
- Defer moving this state into `RuntimeDependencyStateService` until dependency
  resolution has a dedicated service or agent.

### Fix 6: Settlement Requeue Guard

When `from_turn_settlement=True`, require stronger evidence before enqueueing the next wake.

Possible rule:

```text
Only enqueue on settlement if this turn produced a new pending startable frontier
or a new unhandled manager-actionable blocker.
```

Do not enqueue merely because:

- counts changed;
- a blocker moved from needs_manager to blocked_for_user;
- a completed item appeared without downstream action;
- running count changed.

Concrete rule:

```text
if from_turn_settlement:
  enqueue only if fingerprint is non-empty
  and fingerprint != last_signaled_workboard_fingerprint
  and actionability has startable frontier or newly unhandled manager blocker
```

This preserves useful autonomous continuation when a turn opens a genuinely new
frontier, while preventing cleanup/status transitions from self-chaining.

Also filter wake notices from LLM context. `ManagerWakeProcessor` writes
`wake_notice_*` messages into the chat session before each wake turn. Long wake
chains can otherwise pollute future Manager prompts. Treat these like doc 38
command ack messages:

```text
message id starts with wake_notice_
or message source == "wake"
```

should be excluded when constructing Manager LLM session history, while staying
durable in chat history for audit/UI.

Implementation location:

- Filter in the Manager LLM payload construction path, for example
  `ManagerService._build_session_messages(...)` or the closest equivalent
  helper.
- Do not filter in `ChatSessionService.list_messages(...)`; UI and audit views
  must still see durable `wake_notice_*` records.

### Fix 7: Enforce Chain Budget

Before enqueueing or processing another auto wake, enforce:

```text
chain_count < max_chain_count
```

If exceeded:

- stop wake permission;
- write a clear stop reason;
- append a session message explaining that auto paused due to wake chain budget.

Current code increments `chain_count` after a wake turn, but enqueue does not
check `chain_count < max_chain_count`. Add the guard in both places:

- before enqueue in `evaluate_workboard_and_maybe_signal(...)`;
- before processing a claimed wake in `ManagerWakeProcessor`, to cover stale
  queued events created before the guard existed.

Use a distinct stop reason, for example:

```text
auto_chain_budget_exceeded
```

The user-facing message should say auto paused because the wake chain exceeded
the safety budget, not because the work completed.

Implementation semantics:

- Use `ManagerAutoService.stop(..., reason="auto_chain_budget_exceeded", ...)`.
- This should set `enabled=false`, `wake_allowed=false`, and require the user to
  explicitly start auto again with `/auto <objective>`.
- Do not introduce a separate soft-paused state for budget exhaustion.
- Budget is the strongest safety guard. Check it before settlement re-enqueue
  and before processing a stale queued wake.
- In `mode=="once"`, the budget guard still runs first. The once-complete stop
  is only reached after the wake turn finishes without violating budget.

Chain count reset rule:

- Reset `chain_count` on disable -> re-enable.
- Reset `chain_count` when a new user directive is accepted, because the user
  has supplied fresh intent.
- Do not reset `chain_count` on ordinary workboard revision/fingerprint changes.

### Fix 8: Single Settlement Authority

Clarify whether the wake processor or manager-agent `/turn-settled` owns post-turn reevaluation.

Avoid double evaluation paths. Options:

- wake processor owns settlement for wake-triggered turns, and sidecar skips `notifyAutoTurnSettled` for those;
- sidecar owns all turn settlement, and wake processor does not separately call evaluate after `run_to_session`;
- both may call, but one must be a no-op under a shared turn id / revision token.

Recommended direction:

Use wake processor as the settlement authority for wake-triggered turns. The
sidecar `/turn-settled` path should remain for user/directive turns that cross
an async boundary, but wake-triggered turns should carry a turn/wake id that
prevents a second equivalent reevaluation.

If both paths must remain temporarily, add a shared settlement token:

```text
auto_turn_settlement:<project_id>:<wake_id>:<workboard_revision_before_turn>
```

Only the first caller that records the token may enqueue. The second caller must
return current state without evaluating.

## Unstable Directions To Validate

These areas are not stable enough to implement only from intuition:

1. Whether ready frontier should be computed from workboard view or directly
   from `FlowService.get_work_order(...)`. Workboard view is convenient, but
   flow service is the source of truth for startability.
2. Whether dependency failure coalescing belongs in `BackgroundWorkboardService`
   or `RuntimeDependencyStateService`. Coalescing in workboard is fast to ship;
   coalescing in dependency state is cleaner if dependency resolution will gain
   its own agent/service later.
3. Whether blocked-for-user suppresses all future identical manager-actionable
   units or only suppresses them until a user/directive event changes context.
4. Whether completed reviewed runs should ever wake by themselves. The safer
   default is no; opt-in should be reserved for explicit review decisions or
   generated downstream frontier.
5. How much diagnostic detail should be persisted in wake events. Store enough
   fingerprint/actionability detail to audit future loops without replaying the
   whole project graph.

## Completed Producer Audit

Reversing `_completed_item_is_actionable(...)` from default-true to default-false
has non-trivial blast radius. Before implementation, audit every producer of
`lane="completed"` and make wake behavior explicit.

Known producers:

- `card_run_reviewed`: should be a non-wake receipt by default.
- `runtime_dependency_install_succeeded`: already sets
  `payload.actionable_wake=false`; keep it non-wake.
- Any future completed kind must opt in with `payload.actionable_wake=true` only
  when it creates a distinct downstream-ready or review-required fact.

Regression coverage must include one test per completed producer so no producer
silently relies on the old default-true behavior.

## Follow-Up Tests

Add tests shaped like OAA-2:

1. Historical failed dependency jobs plus ready cards should emit at most one wake summary, then become blocked for user.
2. Handling one dependency failure and blocking the rest should not immediately enqueue another wake if no new startable work exists.
3. A ready card that is already claimed or has an active todo should not count as fresh actionable.
4. A reviewed run receipt alone should not wake Manager.
5. A reviewed run that makes a downstream card ready should wake exactly once for the new frontier.
6. A wake turn that only changes running counts should not enqueue another wake.
7. Chain budget stops auto when exceeded.
8. Wake processor and manager-agent settlement do not enqueue duplicate or semantically equivalent wakes.
9. Same revision with same fingerprint is deduped by storage idempotency and state fingerprint.
10. New revision with same fingerprint does not enqueue another wake.
11. New revision with a new pending startable card enqueues exactly one wake.
12. Runtime dependency failures with different job ids but same coalescing key produce one manager-actionable unit.
13. A user directive can trigger a wake even when the workboard fingerprint is unchanged.
14. A stale queued wake is skipped or stops auto when chain budget has already been exceeded.
15. Existing projects with no stored fingerprint bootstrap without migration.
16. `last_signaled_board_revision` changing without fingerprint changing does not enqueue.
17. Budget exhaustion calls `ManagerAutoService.stop(..., reason="auto_chain_budget_exceeded")`.
18. New accepted directive resets `chain_count`; ordinary fingerprint changes do not.
19. `wake_notice_*` messages remain durable but are excluded from Manager LLM context.
20. Each completed producer has explicit non-wake or opt-in wake coverage.
21. Frontend renders `stop_reason="auto_chain_budget_exceeded"` with a clear
    budget-exhausted message, not a generic or unknown stop reason.

## Operational Note

For OAA-2 specifically, the observed six-wake chain was caused by real workboard wakes:

```text
workboard_actionable -> wake_response_* -> workboard mutation -> new revision -> next workboard_actionable
```

The next implementation pass should address this as a semantic coalescing problem, not as a missing idempotency-key problem.
