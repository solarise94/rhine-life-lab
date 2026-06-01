# OAA-2 Auto Wake Loop Review

Status: bug review note.

Date: 2026-06-01

## Summary

OAA-2 exposed a second auto/workboard wake issue after the earlier `/auto` command gap.

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

### Fix 1: Add Semantic Wake Fingerprint

Keep revision-level idempotency, but add a higher-level actionability fingerprint.

The fingerprint should include only fresh work classes that justify a Manager turn, for example:

- pending ready cards not claimed, not running, not already represented by active todo;
- newly failed runs not already completed/blocked/deferred by workboard state;
- newly actionable dependency repair outcomes with source context;
- explicit user directives.

It should exclude:

- old blocked-for-user items;
- already summarized dependency install failures;
- running-only changes;
- completed receipts that do not change downstream frontier.

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

### Fix 3: Filter Ready Projections

Do not count `ready_to_start` items as actionable when:

- the ready item status is not `pending`;
- a todo item already exists for the same source item;
- a run is already active for that card;
- the card is no longer `planned` or otherwise startable.

### Fix 4: Completed Run Wake Policy

Completed reviewed runs should not default to actionable.

Suggested policy:

- mark `card_run_reviewed` completed items as non-wake receipts by default;
- separately derive fresh downstream-ready facts;
- if no downstream-ready or diagnostic fact exists, do not wake.

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

### Fix 7: Enforce Chain Budget

Before enqueueing or processing another auto wake, enforce:

```text
chain_count < max_chain_count
```

If exceeded:

- stop wake permission;
- write a clear stop reason;
- append a session message explaining that auto paused due to wake chain budget.

### Fix 8: Single Settlement Authority

Clarify whether the wake processor or manager-agent `/turn-settled` owns post-turn reevaluation.

Avoid double evaluation paths. Options:

- wake processor owns settlement for wake-triggered turns, and sidecar skips `notifyAutoTurnSettled` for those;
- sidecar owns all turn settlement, and wake processor does not separately call evaluate after `run_to_session`;
- both may call, but one must be a no-op under a shared turn id / revision token.

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

## Operational Note

For OAA-2 specifically, the observed six-wake chain was caused by real workboard wakes:

```text
workboard_actionable -> wake_response_* -> workboard mutation -> new revision -> next workboard_actionable
```

The next implementation pass should address this as a semantic coalescing problem, not as a missing idempotency-key problem.

