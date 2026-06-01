# OAA-2 Terminal Completion Summary Wake

Status: design note.

Date: 2026-06-02

## Summary

OAA-2 exposed a terminal feedback gap in auto mode:

```text
KEGG run accepted
-> workboard has completed=3, ready_to_start=0, needs_manager=0
-> terminal settlement correctly does not enqueue workboard_actionable
-> auto remains enabled/completed with no Manager summary turn
```

This is not a missing `card_run_reviewed` actionability bug. Doc 39 correctly
made completed receipts non-actionable by default to prevent wake loops. The
missing piece is a distinct one-shot terminal summary wake, separate from
workboard actionability.

## OAA-2 Evidence

Observed state after the KEGG pathway run:

```text
run_2803b6f80def finished/reviewed at 2026-06-01T16:53:16Z
card_kegg_pathway_enrichment_analysis_20260601_164817 status=accepted
workboard counts: completed=3, ready_to_start=0, needs_manager=0, running=0
signal_snapshot.has_actionable=false
signal_snapshot.fingerprint=[]
```

No additional wake event was written after the KEGG terminal review. The user
waited and then manually stopped auto at `2026-06-01T16:53:38Z`, which produced
the later `stop_reason=user_stop`. That stop reason is not the root cause; it is
only the manual cleanup action after the missing terminal feedback.

## Current Behavior

The current settlement path is:

```text
WorkerService review/auto-review terminal
-> _notify_background_terminal(project_id, run_id=...)
-> ManagerAutoService.notify_background_task_terminal(...)
-> evaluate_workboard_and_maybe_signal(..., from_turn_settlement=True)
```

For OAA-2, terminal settlement did run, but the workboard had no startable
frontier and no manager blocker. Doc 39 settlement guard therefore correctly
rejected `workboard_actionable`.

Current result:

```text
state = completed
no wake
no chat summary
continuous auto may remain enabled
```

That is scheduler-safe but poor product feedback. The Manager never gets a final
turn to summarize the completed result or decide whether the original directive
clearly requires one more planning step.

## Design Goal

Add one terminal summary wake when a background run/job settles and no semantic
workboard action units remain.

This wake should:

- give Manager one final chance to summarize completion;
- allow Manager to create another card only if the active directive clearly
  requires more work;
- allow auto-once to reach its existing once-complete path;
- avoid making completed workboard receipts actionable;
- avoid reintroducing completed-item wake loops.

## Proposed Event

```text
kind: background_terminal_settled
source_type: run | job
source_id: run:<run_id> | job:<job_id>
idempotency_key: terminal_settlement:<project_id>:<auto_scope_id>:<source_type>:<source_id>
```

Recommended payload:

```json
{
  "state": "completed",
  "run_id": "run_...",
  "job_id": null,
  "scope_objective": "继续任务",
  "workboard_counts": {
    "running": 0,
    "todo": 0,
    "needs_manager": 0,
    "completed": 3,
    "ready_to_start": 0,
    "blocked_for_user": 0,
    "deferred": 0
  }
}
```

## Trigger Conditions

Emit `background_terminal_settled` only when all of these are true:

- `notify_background_task_terminal(...)` has run terminal settlement;
- auto is still enabled and has an owner session;
- settlement result state is `completed`;
- there is no startable frontier;
- there is no unhandled manager-actionable blocker;
- no terminal summary wake already exists for the same project, auto scope, and
  run/job source.

Do not emit this wake when:

- `workboard_actionable` was already enqueued;
- auto is disabled;
- the source run/job id is missing and no stable idempotency key can be built;
- the terminal state is only a dependency timeout/interruption that should wait
  for explicit user/runtime handling.

## Manager Prompt Contract

For `background_terminal_settled`, the wake prompt should explicitly say:

```text
A background run/job reached terminal state and the workboard has no startable
frontier or manager blocker. Inspect the result/project state, summarize
completion, and create another card only if the active directive clearly
requires more work.
```

Keep the normal workboard-first instruction:

```text
Call get_background_workboard first.
```

But the prompt should clarify that an empty workboard is expected in this event
and is not by itself a reason to invent work.

## Guardrails

- This is not a `workboard_actionable` wake.
- Do not use the workboard semantic fingerprint for this event.
- Use terminal run/job idempotency, not revision idempotency.
- Do not mark `card_run_reviewed` completed items as generally actionable.
- After the summary wake finishes, normal settlement rules apply. If no new
  frontier or blocker appears, no further wake should be enqueued.
- In continuous mode, auto may remain enabled with state `completed`.
- In once mode, this summary wake gives the existing once-complete path a final
  wake turn to settle and exit cleanly.

## Implementation Sketch

1. Extend `ManagerAutoService.notify_background_task_terminal(...)`.
2. Run existing `evaluate_workboard_and_maybe_signal(..., from_turn_settlement=True)` first.
3. If it enqueues normal `workboard_actionable`, do nothing else.
4. If it settles to `completed`, enqueue `background_terminal_settled` with the
   idempotency key above.
5. Update `last_wake_id` after successful enqueue.
6. Extend `ManagerWakeProcessor._wake_prompt(...)` for the new kind.
7. Add backend tests for continuous and once mode.

The implementation should not append a synthetic completed workboard item and
should not change `BackgroundWorkboardService._completed_item_is_actionable`.

## Regression Tests

Add tests for:

- Final accepted run with no downstream card enqueues exactly one
  `background_terminal_settled` wake.
- Repeated terminal notification for the same run does not enqueue another
  terminal summary wake.
- Terminal settlement that produces a real `ready_to_start` frontier enqueues
  `workboard_actionable`, not `background_terminal_settled`.
- Terminal settlement with a manager blocker enqueues `workboard_actionable`,
  not `background_terminal_settled`.
- Continuous auto remains enabled and moves to `completed` after the summary
  wake if no new work is created.
- Auto-once reaches `auto_once_complete` after the terminal summary wake turn.
- `card_run_reviewed` completed receipt remains non-actionable by default.

## Acceptance Criteria

1. OAA-2-style final KEGG accept produces a Manager summary wake.
2. The summary wake is idempotent per terminal run/job and auto scope.
3. Completed receipts do not become generic wake fuel.
4. Empty workboard after terminal summary does not self-chain.
5. Auto-once can exit cleanly after the final summary wake.
6. Continuous auto can remain enabled with `state=completed` and visible final
   chat feedback.
