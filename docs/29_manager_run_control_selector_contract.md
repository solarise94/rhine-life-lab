# Manager Run-Control Selector Contract

## Purpose

Manager run-control tools operate on two related but different entities:

- `card_id`: the durable planning node.
- `run_id`: one concrete execution attempt for a card.

The relationship is one-to-many: one card can have many historical runs, while each run belongs to exactly one card.

For the Manager-facing tool surface, `card_id` is the primary selector. This matches the user interaction model: users think in terms of cards, not individual run attempts. `run_id` may remain useful in lower-level services, logs, and internal APIs, but Manager should not need to route normal run-control actions through `run_id`.

## Selector Rules

### `start_card_run`

Manager should pass only:

- `card_id`

Reason: starting a card creates a new run. Runtime and worker configuration must already be attached to the card by `configure_card_execution`.

### `rerun_card`

Manager should pass only:

- `card_id`

Reason: rerun creates a fresh execution attempt for the existing card. It should reuse the card's saved execution configuration and current input asset bindings.

### `stop_card_run`

Manager should pass only:

- `card_id`

Reason: the user asks to stop a card's current work, not a specific run attempt.

The product does not intentionally support multiple active runs for the same card. If the backend ever finds more than one active run for the same `card_id`, `stop_card_run(card_id=...)` should stop all active runs for that card. This makes the exceptional state fail closed instead of leaving hidden background work running.

The response should report which run ids were stopped.

### `review_card_run`

Manager should pass only:

- `card_id`

Reason: review/finalization should apply to the latest run for that card. Reviewing old runs is not a supported Manager workflow because old runs are superseded by reruns and are only useful as history.

The backend should resolve the latest run for the card. If there is no run, it should return a clear error.

Manager-facing `review_card_run` should not expose an `accept` decision field. The accept/reject decision belongs to the reviewer path and graph consistency checks, not to Manager's general tool surface. Manager may request review/finalization for a run, but it should not be able to bypass reviewer judgment by choosing `accept=true`.

## Why `card_id` Is Authoritative For Manager

`run_id` is technically more precise, but it is not the right primary abstraction for Manager. The product object the user sees and manipulates is the card. A card may have:

- an old accepted run,
- a failed run,
- a newly queued or running run,
- a rerun created after dependency repair.

Manager should operate on the card and let the backend resolve the relevant run:

- start/rerun creates a new run for the card;
- stop stops the card's active run or runs;
- review finalizes the card's latest run.

This keeps conversation state and tool calls aligned with the UI.

## Internal `run_id` Use

Lower-level services and debugging tools may still use `run_id` for exact history lookup, log retrieval, executor bookkeeping, and compatibility APIs.

If any internal/API path accepts both `run_id` and `card_id`, it must still verify that the run belongs to the card and reject mismatches. That consistency check remains important below the Manager-facing tool layer.

## Runtime Configuration Boundary

Run-control tools should not accept runtime override fields such as:

- `worker_type`
- `profile_id`
- `python_runtime`
- `r_runtime`

Those belong to `configure_card_execution`. Keeping runtime binding out of `start_card_run` and `rerun_card` makes execution reproducible from card state and prevents Manager from creating hidden one-off runtime differences.

Manager-facing routes and tools should not offer an alternate runtime override path. If a caller needs to change runtime, Python/R binding, tool policy, skills, MCP servers, or instruction blocks, it must update the card through `configure_card_execution` before starting or rerunning.

Direct lower-level services may keep compatibility parameters while the codebase is migrated, but Manager and frontend product flows should use the card execution configuration as the single source of truth.

## Rerun Input Contract

`rerun_card` means rerunning the same card with the same input bindings and execution configuration.

It should not automatically rewrite `card.inputs[].asset_id`.

If rerun detects missing, stale, superseded, candidate, or otherwise invalid input assets, it should reject the rerun with a clear dependency-chain error. The error should tell Manager to use the dependency repair flow rather than silently changing inputs.

Dependency repair is a separate workflow:

- inspect dependency attention;
- update card inputs to valid current assets or expected upstream outputs;
- start the card normally with `start_card_run(card_id)`.

This separation keeps `rerun_card` stable and predictable. A rerun is an execution retry, not an implicit dependency rewrite.

## Rerun History Retention

Rerunning by `card_id` should not delete the card. The card is the durable plan node; rerun creates a new execution attempt for that same node.

Old runs should be retained as compact history and logs, but old outputs should stop driving the active dependency graph after a newer accepted run supersedes them. Recommended behavior:

- keep old run logs and failure messages for audit/debugging;
- hide or collapse old runs in the default UI;
- keep previous accepted outputs valid until a newer run is accepted, so a failed rerun does not break downstream cards;
- after a newer run is accepted, mark superseded old outputs as inactive for dependency resolution while preserving their logs.

Deleting the card on rerun would lose planning identity and makes downstream dependency repair harder. Cleanup should target old run history and superseded assets, not the card itself.

## Compatibility Note

Lower-level service APIs may continue accepting runtime overrides for direct internal or API use. The restriction here is for the Manager-facing tool surface.
