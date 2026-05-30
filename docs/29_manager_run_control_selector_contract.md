# Manager Run-Control Selector Contract

## Purpose

Manager run-control tools operate on two related but different entities:

- `card_id`: the durable planning node.
- `run_id`: one concrete execution attempt for a card.

The relationship is one-to-many: one card can have many historical runs, while each run belongs to exactly one card.

For the Manager-facing tool surface, `card_id` is the primary selector. This matches the user interaction model: users think in terms of cards, not individual run attempts. `run_id` may remain useful in lower-level services, logs, and internal APIs, but Manager should not need to route normal run-control actions through `run_id`.

## Card Write Tool Split

Manager-facing card edits should be split by side effect.

### `revise_card_plan`

This replaces the broad `update_card` concept for execution-relevant edits.

Manager may change only fields that affect dependency wiring or execution planning:

- `step`
- `inputs`
- `outputs`

Side effect:

- reset the card to `planned` when the current card is not already in a transient execution state;
- clear stale progress notes;
- preserve old runs, logs, and assets as history.

If the card is already `planned`, `revise_card_plan` should only apply the field changes and clear stale `progress_note`; it should not perform any extra reset behavior.

After `revise_card_plan`, execution should proceed with:

```text
start_card_run(card_id)
```

`revise_card_plan` should not accept display-only fields such as `title` or `summary`.

### `annotate_card`

This is the display/notes tool. It must not change execution semantics.

Manager may change:

- `title`
- `summary`
- `manager_review` or note text

`manager_review` is the existing display/review text field on the card model. In `annotate_card`, it is treated as a note only; it must not finalize a run or imply asset acceptance.

Side effect:

- do not change `card.status`;
- do not change `inputs`;
- do not change `outputs`;
- do not trigger dependency repair;
- do not reset the card to `planned`.

### Output Filename Boundary

Manager must not be allowed to edit output filenames or system-derived output fields.

Disallowed Manager inputs include:

- output filename/path;
- output `asset_id`;
- output `label`;
- output `status`;
- `preferred_format`;
- `accepted_formats`.

For outputs, Manager may only describe the semantic contract:

- `role`
- `artifact_class`
- `description`

If a user asks to rename a generated output file, Manager should explain that generated asset filenames are controlled by the executor and asset system. The user can download the result and rename the local copy if they need a custom filename.

## Run-Control Tools

### `start_card_run`

Manager should pass only:

- `card_id`

Reason: starting a card creates a new run. The backend should use the card's saved execution configuration when present; otherwise it should use system defaults.

Manager should not call `configure_card_execution` before every run. Use it only when the card needs non-default runtime, tool policy, skills, MCP servers, script bindings, or extra executor instructions.

### `rerun_card`

Manager should pass only:

- `card_id`

Reason: rerun creates a fresh execution attempt for the existing card. It should reuse the card's saved execution configuration when present; otherwise it should use system defaults. It should reuse the current input asset bindings.

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

Those belong to `configure_card_execution` only when a card needs to override system defaults. Keeping runtime binding out of `start_card_run` and `rerun_card` makes execution reproducible from card state and prevents Manager from creating hidden one-off runtime differences.

Manager-facing routes and tools should not offer an alternate runtime override path. If a caller needs to change runtime, Python/R binding, tool policy, skills, MCP servers, script asset bindings, or instruction blocks, it must update the card through `configure_card_execution` before starting or rerunning.

Default path:

```text
create/revise card -> start_card_run(card_id)
```

Override path:

```text
configure_card_execution(card_id/card_ids, non-default execution settings)
-> start_card_run(card_id)
```

Manager should prefer the default path unless there is a concrete reason to override execution configuration.

Direct lower-level services may keep compatibility parameters while the codebase is migrated, but Manager and frontend product flows should use the card execution configuration as the single source of truth.

## Rerun Input Contract

`rerun_card` means rerunning the same card with the same input bindings and execution configuration.

It should not automatically rewrite `card.inputs[].asset_id`.

If rerun detects missing, stale, superseded, candidate, or otherwise invalid input assets, it should reject the rerun with a clear dependency-chain error. The error should tell Manager to use the dependency repair flow rather than silently changing inputs.

Dependency repair is a separate workflow:

- inspect dependency attention;
- revise card inputs to valid current assets or expected upstream outputs;
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

## Implementation Plan

Implement the surface changes in this order so intermediate states remain understandable.

### P0: Split Card Writes

1. Add Manager-facing `revise_card_plan`.
   - Accept only `card_id`, `step`, `inputs`, and `outputs`.
   - Use `extra="forbid"` on the payload model.
   - Reuse the existing card validation and output-contract derivation logic.
   - On successful semantic change, reset non-transient cards to `planned`.
   - Clear stale `progress_note`.
   - Preserve historical runs, logs, assets, and accepted outputs until normal review/supersede logic changes them.
   - Return dependency-attention mutation hints, including `dependency_attention_check_recommended`, when input/output changes may affect downstream cards.

2. Add Manager-facing `annotate_card`.
   - Accept only `card_id`, optional `title`, optional `summary`, and optional note/`manager_review`.
   - Use `extra="forbid"` on the payload model.
   - Never change `card.status`.
   - Never change `inputs` or `outputs`.
   - Never trigger dependency attention repair behavior.

3. Remove broad `update_card` from the Manager sidecar tool list.
   - After `revise_card_plan` and `annotate_card` are implemented, remove the old Manager-facing `update_card` tool instead of preserving it as a parallel product path.
   - A backend compatibility route may exist temporarily for direct API compatibility, but the sidecar must not expose it.
   - The sidecar prompt should refer to `revise_card_plan` and `annotate_card`, not `update_card`.

### P0: Align Dependency Repair

1. Update dependency-attention repair guidance:

```text
input_asset_outdated -> revise_card_plan(inputs=[current_asset_id]) -> start_card_run(card_id)
```

2. Do not tell Manager to repair stale inputs by calling `rerun_card`.
3. `rerun_card` should remain a strict retry of the current saved card inputs and execution configuration.

### P0: Narrow Run-Control

1. `start_card_run`: Manager passes only `card_id`.
2. `rerun_card`: Manager passes only `card_id`.
3. `stop_card_run`: Manager passes only `card_id`.
   - If multiple active runs are found for the same card despite the product invariant, stop all of them.
   - Return the stopped run ids.
   - Implement this by querying all active runs for the card, not by resolving only one active run id.
4. `review_card_run`: Manager passes only `card_id`.
   - Resolve the latest run for that card.
   - Do not expose `accept`.
   - Treat the call as a finalize/accept attempt; the final `accepted` value is determined by manifest validation, reviewer/graph consistency, and finalization logic.
   - Do not allow repeated finalization of runs that are already finalized unless a future explicit recovery/admin path is added.

### P0: Rerun Input Preflight

`rerun_card` must validate the current saved card inputs before resetting state or launching a new run.

Reject rerun with a clear dependency-chain error when inputs are missing, stale/outdated, superseded, candidate, or otherwise invalid for a strict retry.

This preflight should reuse existing graph/dependency-attention logic where possible, but its product behavior is stricter than normal work-order startability: `rerun_card` is a retry of known-good saved inputs, not a dependency repair operation.

### P0: Protect Running Execution Configuration

`configure_card_execution` must not silently change execution context while a card is actively running or reviewing.

`configure_card_execution` is not a required pre-run step. It is the card-level override tool for non-default execution settings.

Target behavior:

- reject changes for cards that currently have any active run (`queued`, `launching`, `needs_approval`, `running`, or `reviewing`);
- return a clear error telling Manager to wait for the run to finish or stop the run first;
- keep runtime/tool-policy changes as future-run configuration only, never as a hidden mutation of an already-started execution.

For accepted cards, changing runtime/tool policy should not automatically invalidate accepted results in v1. If the user wants a new result under a new runtime policy, Manager should configure the card, then explicitly start/rerun according to the normal run-control contract.

### P1: Prompt And Compatibility Cleanup

1. Replace Manager prompt mentions of `update_card` with `revise_card_plan` or `annotate_card` based on intent.
2. Replace compact tool guidance and repair hints that still say `update_card -> rerun_card`.
3. Make `rerun_card` tool text explicit that it resets a startable historical card to `planned` before launching the new run.
4. Document `install_runtime_dependencies.timeout_seconds` as clamped to 30-1800 seconds, or reject out-of-range values if stricter validation is preferred.
5. Add `ConfigDict(extra="forbid")` to `cleanup_run_history` payload.
6. Make start/rerun async-boundary fields explicit:
   - either return `pending_approvals: []` and `rejected_approvals: []` on success;
   - or remove the obsolete approval-field check from the async-boundary detector.
7. Add tests for the deprecated backend-only `configure_card_execution.card_id` alias if the alias remains.
8. Update tests to assert rejected extra fields for the new Manager-facing schemas.
9. Mark backend compatibility routes/methods as deprecated if they remain.

### Discussion Items

The following items were identified during review but should be decided before implementation:

1. Review response semantics.
   - Current `ok` means "tool/API call succeeded", while `accepted` means "run result was accepted".
   - Decide whether to add clearer fields such as `review_completed`, `accepted`, `failure_reason`, and `message` so Manager cannot mistake `ok: true` for successful acceptance.

2. Stop response consistency.
   - `stop_card_run` should return `ok`, `stopped`, and all stopped run ids after the card-id-only transition.
   - This is less about wrapper style and more about making the exceptional multi-active-run case visible.

3. Direct API runtime override compatibility.
   - Lower-level `/cards/{card_id}/start-run` and `/cards/{card_id}/rerun` APIs may continue accepting runtime overrides temporarily.
   - Product flows and Manager tools must not use those overrides.

### P2: Legacy Surface Audit

Review legacy manager/planner paths that still mention broad card objects or writable `manager_review`, including:

- `backend/app/services/manager_planner.py`
- `backend/app/services/manager_tools.py`
- patch/rollback helpers that set `manager_review`

If these paths are still reachable from the product, either migrate them to the new split or mark them as legacy/internal-only.
