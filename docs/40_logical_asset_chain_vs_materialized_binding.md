# Logical Asset Chain Vs Materialized Binding

Status: design analysis note.

Date: 2026-06-01

## Summary

Running and accepting cards can still cause dependency-chain drift because the current data model mixes two different concepts in one field:

```text
card.outputs[].asset_id
```

Today this field can mean either:

1. logical planned output id, such as `asset_deg_table`;
2. concrete materialized run output id, such as `asset_run_dbe18836fec2_deg_table_1`.

This mixing is the root source of dependency-chain breakage. Manager can often repair the break by rebinding downstream inputs, but that is remediation after the chain identity has already drifted.

The better long-term model is:

```text
Dependency DAG uses logical expected outputs and logical expected inputs.
Execution launch resolves those logical ids to concrete materialized files.
Dependency Attention checks whether the concrete binding changed or became invalid.
```

## Current Behavior

### Accept Mutates Output Identity

`WorkerService._finalize_run_review(...)` accepts a run by resolving card output slots to produced assets and then binding the card output to the concrete run asset.

The critical behavior is:

```python
out.asset_id = real_asset.asset_id
out.status = real_asset.status
```

Before accept:

```text
card_differential_expression.outputs[deg_table].asset_id = asset_deg_table
```

After accept:

```text
card_differential_expression.outputs[deg_table].asset_id = asset_run_dbe18836fec2_deg_table_1
```

That means the card's output contract changes identity from the planned id to the materialized id.

### Alias Backfill Partially Repairs It

The current implementation backfills:

```text
Asset.metadata["planned_asset_id"]
```

on the materialized asset.

Example:

```json
{
  "asset_id": "asset_run_dbe18836fec2_deg_table_1",
  "metadata": {
    "role": "deg_table",
    "planned_asset_id": "asset_deg_table"
  }
}
```

This lets alias-aware readers recover the old planned id and map it to the new materialized file.

### Input Resolution Has Already Been Partially Added

The current `InputResolutionService` can resolve a requested logical id to a concrete asset using:

- direct materialized asset id;
- `Asset.metadata["planned_asset_id"]`;
- producer card and output role;
- latest valid candidate by run order.

`FlowService.get_work_order(...)` and `WorkerService._task_packet(...)` use this service, so launch-time input resolution is already partially alias-aware.

This means the system no longer always breaks immediately, but it is still compensating for identity drift rather than avoiding it.

## Why This Is Still A Bug

The dependency chain should be stable across runs.

If a downstream card was planned against:

```text
asset_deg_table
```

then the dependency DAG should continue to say:

```text
DEG Visualization input -> asset_deg_table
Differential Expression output -> asset_deg_table
```

Accepting a run should only say:

```text
asset_deg_table is currently materialized by asset_run_dbe18836fec2_deg_table_1
```

It should not rewrite the producer output identity itself.

When the producer output id is rewritten, different backend surfaces must guess whether a given id is:

- a logical planned output id;
- a concrete materialized asset id;
- an alias to a prior planned id;
- a stale concrete output from an older run.

That ambiguity causes:

- `FlowService` and workboard readiness to need alias recovery;
- `DependencyAttentionService` to mix chain validation and file freshness checks;
- Manager to see missing or outdated inputs and repair them manually;
- downstream cards to sometimes bind to concrete files when they should stay bound to logical outputs.

## Desired Model

Separate the model into two layers.

### Layer 1: Logical Dependency Contract

The DAG should be defined only by expected inputs and expected outputs.

Example:

```text
Card A output:
  role = deg_table
  planned_asset_id = asset_deg_table

Card B input:
  requested_asset_id = asset_deg_table
```

This layer is stable. It should survive:

- first run;
- rerun;
- accept;
- superseding previous materializations;
- downstream cards not yet run;
- downstream accepted cards that need attention after an upstream rerun.

### Layer 2: Materialized Binding

Concrete files live in a binding layer:

```text
asset_deg_table -> asset_run_dbe18836fec2_deg_table_1
```

The binding stores:

- logical planned asset id;
- current concrete asset id;
- role;
- producer card id;
- producer run id;
- status;
- path;
- superseded concrete asset ids when relevant.

The current implementation approximates this through materialized `Asset.metadata["planned_asset_id"]`, but that metadata is not a first-class contract.

## Expected Responsibilities

### Flow / Workboard

Flow and workboard should reason primarily over the logical chain.

They should answer:

- Does every requested logical input have a producer?
- Is the producer accepted or otherwise available?
- Does the logical input have a current launchable materialization?
- Is the card startable based on logical dependencies plus resolved concrete bindings?

They should not require downstream cards to rewrite inputs from logical ids to concrete ids.

### Launch / Task Packet

Launch should be the point where logical inputs become concrete file paths.

Task packet should include both:

```json
{
  "requested_asset_id": "asset_deg_table",
  "resolved_asset_id": "asset_run_dbe18836fec2_deg_table_1",
  "resolved_path": "results/.../deg_table.tsv",
  "resolved_by": "current_materialization",
  "producer_card_id": "card_differential_expression",
  "producer_role": "deg_table"
}
```

The executor should read only the resolved path, but diagnostics should retain the requested logical id.

### Dependency Attention

Dependency Attention should not decide whether the logical chain exists. Flow should own that.

Attention should answer:

- Is the concrete file currently bound to this logical input still valid?
- Has the producer's current materialization changed since this downstream card last ran?
- Is the downstream accepted result based on an old concrete asset?
- Does the concrete asset lineage contain invalid or superseded inputs?

This cleanly distinguishes:

```text
logical dependency missing
```

from:

```text
logical dependency exists, but current concrete binding is stale or invalid
```

## Concrete OAA-2 Shape

Current OAA-2 accepted Diff output:

```text
card_differential_expression.outputs[deg_table].asset_id
  = asset_run_dbe18836fec2_deg_table_1
```

The materialized asset contains:

```json
{
  "metadata": {
    "role": "deg_table",
    "planned_asset_id": "asset_deg_table"
  }
}
```

Downstream cards were originally planned against:

```text
asset_deg_table
```

Manager later repaired them to concrete:

```text
asset_run_dbe18836fec2_deg_table_1
```

That repair made OAA-2 proceed, but it also converted the downstream logical dependency into a concrete binding. After a future upstream rerun, Dependency Attention must detect that this concrete binding is outdated and Manager must repair again.

Under the desired model, downstream cards would stay bound to:

```text
asset_deg_table
```

and the resolver would launch them against the latest valid concrete materialization.

## Recommended Direction

### Implementation Principle

Do not rely on Manager to rewrite downstream card inputs to concrete run asset ids.

If downstream cards have logical inputs, preserve them. Use resolver output in launch and diagnostics.

The first implementation should not require a new database table. Use a compatibility binding map in `GraphState.metadata` and wrap it behind a service API. Once all readers use that API, the backing store can move to a dedicated table or schema field without changing call sites.

Recommended backing shape for the first pass:

```json
{
  "asset_materializations": {
    "asset_deg_table": {
      "planned_asset_id": "asset_deg_table",
      "current_asset_id": "asset_run_dbe18836fec2_deg_table_1",
      "producer_card_id": "card_differential_expression",
      "producer_role": "deg_table",
      "producer_run_id": "run_dbe18836fec2",
      "status": "valid",
      "path": "results/.../deg_table.tsv",
      "updated_at": "2026-06-01T00:00:00Z",
      "superseded_asset_ids": []
    }
  }
}
```

`Asset.metadata["planned_asset_id"]` remains as compatibility data on concrete assets, but it should no longer be the primary API.

### New Service Boundary

Add a small binding service, for example `AssetMaterializationService`, with these responsibilities:

- `current_for_logical(graph, planned_asset_id) -> AssetMaterialization | None`;
- `set_current(graph, planned_asset_id, concrete_asset, producer_card_id, producer_role, producer_run_id)`;
- `supersede_previous(graph, planned_asset_id, new_asset_id) -> list[str]`;
- `resolve_logical_output(graph, cards, card_id, role_or_planned_asset_id) -> Asset | None`;
- `bootstrap_from_aliases(graph, cards)` for legacy projects without an explicit map.

Method boundaries:

- `current_for_logical(...)` is the input-side resolver primitive. It takes a planned/logical asset id and returns the current concrete materialization.
- `resolve_logical_output(...)` is the output-side helper. It may resolve by `(card_id, role)` or by planned asset id and is used by acceptance validation, output attention checks, and compatibility bootstrap.

The service should read in this order:

1. explicit `graph.metadata["asset_materializations"]`;
2. concrete `Asset.metadata["planned_asset_id"]` aliases;
3. legacy accepted `card.outputs[].asset_id` that already points to a concrete asset.

It should write only the explicit binding map plus compatibility aliases on concrete assets.

All write methods must run under `project_service.lock_for(project_id)`. The accept path already operates inside the project lock; the binding service should reuse that lock boundary and should not perform an independent read-modify-write outside it. If two runs for the same logical output are accepted, the later committed accept under the project lock wins and the previous concrete asset is moved into `superseded_asset_ids`.

## Concrete Modification Plan

### Phase 1: Preserve Logical Output Identity On Accept

Stop mutating `card.outputs[].asset_id` from logical planned id to concrete run asset id on accept.

Instead, preserve:

```text
card.outputs[].asset_id = asset_deg_table
```

and update a materialization binding:

```text
asset_deg_table.current_asset_id = asset_run_dbe18836fec2_deg_table_1
```

Specific changes:

- In `WorkerService._finalize_run_review(...)`, remove both simulation and commit mutations of `out.asset_id = real_asset.asset_id`.
- Keep `out.asset_id` as the planned/logical id.
- Use status option A: `out.status` tracks the current concrete materialization status. After accept, `out.status == current_materialization_asset.status`, usually `valid`.
- After promoting the concrete asset to `valid`, call the binding service to set `planned_asset_id -> real_asset.asset_id`.
- Continue backfilling `real_asset.metadata["planned_asset_id"] = planned_asset_id` for compatibility.
- Keep `_attach_assets_to_card(card, created_assets)`. Concrete run assets must continue to be written to `card.linked_assets`, because UI and audit views need a direct concrete file list.
- Update `_validate_acceptance_graph_consistent(...)`: an accepted logical output is valid when it has a current materialization whose concrete asset exists and is `valid`; it must not require `output.asset_id` itself to exist in `graph.assets`. Validation should still require `output.status == current_materialization_asset.status`.
- Update `_sync_card_outputs(...)` or mark it legacy-only. If retained, it must sync materialization bindings and statuses, not rewrite output ids.
- Update `_current_output_assets(...)` and `_supersede_previous_outputs(...)` to use current materializations for declared output roles. Previous concrete assets should be superseded by binding history, not by comparing `card.outputs[].asset_id` directly.

Important invariant after Phase 1:

```text
card.outputs[].asset_id == logical planned id
card.linked_assets contains concrete run assets for UI/audit
graph.metadata.asset_materializations[logical id].current_asset_id == concrete run asset
```

Phase 1 must dual-write the explicit binding and the legacy alias. Before Phase 2 lands, old resolver paths can still resolve through `Asset.metadata["planned_asset_id"]`. After Phase 2 lands, resolver paths must prefer `asset_materializations` and use the alias only as fallback. This avoids a transition window where accept preserves logical output ids but launch resolution cannot find the concrete asset.

### Phase 2: Make Input Resolution Binding-First

`InputResolutionService` already has the right output shape. It should become binding-first:

- Build `current_output_by_card_role` from the materialization service first.
- Resolve a requested logical asset id through `asset_materializations[requested_asset_id]`.
- Return `resolved_by="materialization_binding"` for explicit binding hits.
- Keep `resolved_by="planned_asset_alias"` only as a legacy fallback.
- Keep direct concrete asset ids valid for intentional concrete bindings, but classify them as `direct_asset_id`.
- Redefine `is_virtual`: it should be true only when the requested id is not a concrete asset id and no valid materialization binding exists. A logical id with a valid binding is a resolved logical input, not an unresolved virtual placeholder.
- Preserve `requested_asset_id` in `TaskPacketAsset` and `TaskPacketCardInput`; executors should continue using `asset_path` / resolved concrete path.

This keeps launch behavior stable while removing the need for Manager to patch downstream inputs.

### Phase 3: Split Flow Readiness From Materialization Readiness

`FlowService.get_work_order(...)` should report two separate concepts:

- logical chain status: whether each requested logical input has a producer card/output;
- materialization status: whether that logical input currently resolves to a launchable concrete asset.

Recommended response additions:

```json
{
  "logical_missing_asset_ids": [],
  "materialization_missing_asset_ids": ["asset_deg_table"],
  "nonlaunchable_materialized_asset_ids": [],
  "input_resolutions": [
    {
      "requested_asset_id": "asset_deg_table",
      "resolved_asset_id": "asset_run_dbe18836fec2_deg_table_1",
      "resolved_by": "materialization_binding",
      "producer_card_id": "card_differential_expression",
      "producer_role": "deg_table",
      "status": "valid"
    }
  ]
}
```

`can_start` should be false for missing materialization, but the blocker should say materialization is missing, not that the DAG edge is missing.

Workboard items should preserve the same distinction:

- `needs_manager` when the logical producer/output is missing or the DAG contract needs repair;
- a materialization blocker should not introduce a new lane unless necessary. Prefer `blocked_for_user` or `needs_manager` with `payload.block_reason="input_materialization_missing"` / `"input_materialization_not_valid"` first;
- `ready_to_start` when all logical inputs resolve to launchable concrete assets.

Current workboard lanes are `running`, `todo`, `needs_manager`, `completed`, `ready_to_start`, `blocked_for_user`, and `deferred`. The first implementation should align to this set and add structured `block_reason` / `dependency_kind` fields in payload rather than inventing `blocked_for_manager`.

### Phase 4: Re-scope Dependency Attention

Dependency Attention should stop treating logical output ids as missing concrete assets.

Input checks should use resolver output:

- If no logical producer exists, report `logical_dependency_missing`.
- If a logical producer exists but no current concrete binding exists, report `input_materialization_missing`.
- If the current concrete binding exists but is non-valid, report `input_materialization_not_valid`.
- If the downstream accepted run used an older concrete id than the current binding, report `input_asset_outdated`.
- If the resolved concrete asset lineage includes invalid/superseded roots, report `asset_lineage_invalid`.

Output checks should use binding resolution:

- Accepted card output with logical `asset_id` is valid when its current materialization exists and is `valid`.
- Missing binding for an accepted output should be `output_materialization_missing`, not `output_asset_not_valid`.
- Concrete output assets remain checked for status and lineage.

To make stale downstream detection reliable, persist input binding provenance at run output materialization time. The write point should be `_materialize_run_assets(...)`, using the task packet's frozen input resolutions from run creation/launch time. If the upstream binding changes after the downstream run starts, the downstream output metadata must keep the old frozen binding so Attention can compare it against the current binding later.

A lightweight first pass can store the frozen bindings on each concrete output asset:

```json
{
  "metadata": {
    "input_bindings": [
      {
        "label": "DEG table",
        "requested_asset_id": "asset_deg_table",
        "resolved_asset_id": "asset_run_dbe18836fec2_deg_table_1",
        "resolved_by": "materialization_binding"
      }
    ]
  }
}
```

The existing `Asset.depends_on` should continue storing concrete resolved ids for lineage traversal.

This creates two intentional graph views:

- Logical DAG: `card.outputs[].asset_id -> card.inputs[].asset_id`. Flow and workboard use this for producer lookup, ordering, cycle checks, and readiness.
- Concrete lineage: `Asset.depends_on`. Dependency Attention uses this for `asset_lineage_invalid`, result dependency inspection, and stale materialization checks.

### Phase 5: Timeline And Frontend Compatibility

`AssetTimelineService` should expose both logical planned assets and concrete materializations:

- logical output record: `asset_id=asset_deg_table`, `planned=true`, `materialized=false`, `current_asset_id=asset_run...`;
- concrete asset record: `asset_id=asset_run...`, `planned=false`, `materialized=true`, `planned_asset_id=asset_deg_table`;
- card edges should use logical ids;
- asset lineage edges should use concrete ids.

Frontend code that currently draws lines from `card.outputs[].asset_id` to `target.inputs[].asset_id` can continue to work once output ids stay logical. For result/file panels, use `linked_assets` or timeline `current_asset_id` to open concrete files. Do not assume `card.outputs[].asset_id` is fetchable through `/results/{asset_id}`.

Frontend rendering contract:

- Card output rows should show the logical contract id and role, for example `deg_table (asset_deg_table)`.
- If `current_asset_id` exists, the result action should open/download the concrete current asset.
- If no current materialization exists, disable or hide the result action and show a clear "not materialized yet" state.
- Clicking a logical id should resolve through the materialization API first. If resolution fails, show a logical-id-specific error instead of calling `/results/{logical_id}` directly.
- Concrete run assets in `linked_assets` remain available in audit/detail views.

### Phase 6: Manager Tooling Guardrails

Manager-facing tools should reinforce the split:

- `find_assets` may return concrete assets, but should include `planned_asset_id` and `current_for_logical` when available.
- card creation/update tools should prefer logical output ids in `inputs[].asset_id`.
- repair guidance should say "preserve logical input and rerun" when a current materialization exists.
- only use concrete input ids when the user or tool explicitly requests a fixed historical artifact.

Specific tool hooks:

- `create_card` / `update_card`: if an input `asset_id` is a concrete asset and that asset has an unambiguous `planned_asset_id`, return a warning or reject unless the payload explicitly marks it as a fixed historical artifact.
- `find_assets`: include `planned_asset_id`, `is_current_for_logical`, `current_for_logical_asset_id`, `producer_card_id`, and `producer_role` where known.
- dependency repair guidance should recommend logical ids first and reserve concrete ids for reproducibility or historical comparisons.

This prevents future repairs from reintroducing concrete ids into the logical DAG.

## Migration And Compatibility

### Bootstrap Without A Hard Migration

Existing projects already contain cards whose outputs were rewritten to concrete ids.

The first implementation can bootstrap bindings at read time:

1. For each concrete asset with `metadata.planned_asset_id`, recover the stable logical id.
2. For each accepted card output that points to a concrete asset, use the concrete asset metadata role and `planned_asset_id` to reconstruct the logical output binding.
3. Populate or expose `asset_materializations[planned_asset_id].current_asset_id`.
4. Do not immediately rewrite downstream inputs.
5. For downstream inputs already rewritten to concrete ids, report a compatibility warning when the concrete asset has an unambiguous `planned_asset_id`.

This avoids a risky forced migration while giving all new code a first-class binding view.

If a bootstrapped legacy project later accepts a new run, the explicit binding map must be written from that point forward and the concrete asset must still receive `metadata.planned_asset_id`. Bootstrap-at-read should never become the only source after a write has occurred.

### Optional Schema Hardening

```text
CardOutputSpec.planned_asset_id
CardOutputSpec.current_asset_id
```

or a separate project-level map:

```json
{
  "asset_materializations": {
    "asset_deg_table": {
      "current_asset_id": "asset_run_dbe18836fec2_deg_table_1",
      "producer_card_id": "card_differential_expression",
      "producer_role": "deg_table",
      "producer_run_id": "run_dbe18836fec2"
    }
  }
}
```

The separate map is cleaner because it keeps card contracts stable and treats materialization as runtime state.

Do not add both `CardOutputSpec.current_asset_id` and a project-level map in the same first implementation. That creates two writers for the same state. Prefer the project-level map first.

## Implementation Order

Recommended order:

1. Add materialization binding model/service and bootstrap reads from legacy aliases.
2. Update `InputResolutionService` to read explicit bindings first, with alias fallback.
3. Update `WorkerService._finalize_run_review(...)` to write bindings and stop rewriting output ids.
4. Update acceptance validation and output sync helpers to validate bindings.
5. Update flow/workboard readiness to distinguish logical missing from materialization missing.
6. Update dependency attention output/input checks to use binding-aware resolution.
7. Add run output `metadata.input_bindings` during materialization for stale downstream detection.
8. Update timeline/frontend file-opening paths to use concrete `linked_assets` / `current_asset_id`.
9. Add compatibility warnings or a manual cleanup tool for concrete downstream inputs.

Non-goals for the first pass:

- no automatic rewrite of all historical project JSON;
- no removal of `Asset.metadata["planned_asset_id"]`;
- no frontend assumption that logical output ids are downloadable result ids;
- no Manager auto-repair that rewrites logical inputs to concrete ids.

Implementation dependency note:

Phase 1 and Phase 2 are separable only because Phase 1 dual-writes explicit binding and legacy alias. If implementation cannot safely dual-write both, then Phase 1 and Phase 2 must ship in one patch.

## Risk Points To Check During Implementation

- `WorkerService._validate_acceptance_graph_consistent(...)` currently assumes every accepted `card.outputs[].asset_id` exists in `graph.assets`; this must change in the same patch as accept mutation removal.
- `DependencyAttentionService._analyze_card_outputs(...)` has the same concrete-output assumption and will otherwise emit false errors after the model is fixed.
- `AssetTimelineService.producer_maps(...)` currently registers both run output aliases and card outputs; duplicate detection must not treat a logical output and its current concrete materialization as duplicate producers.
- `AssetTimelineService.produced_assets_by_card(...)` currently mixes concrete run assets and card output ids; UI may need separate `produced_logical_asset_ids` and `materialized_asset_ids`.
- `WorkerService._materialize_run_assets(...)` currently stores `depends_on` as concrete input ids; keep that for lineage, but also persist requested/resolved input binding metadata.
- `project_file_service` and result endpoints should only resolve concrete assets. If a user opens a logical id, route through materialization binding first or return a clear logical-id error.
- Frontend `ConnectionLines` can stay logical, but file/result UI must not call result APIs with logical ids.
- Manager tools that list or patch cards must not convert logical inputs to concrete ids as a repair shortcut.
- `InputResolution.is_virtual` has downstream meaning in `FlowService`; update any `is_virtual and resolved_asset_id` checks when redefining it.
- Accept and binding writes must remain inside the project lock. Tests should cover same logical output accepted multiple times.

## OAA-2 Regression Review: Accept Did Not Wake Because Manager Stayed Running

Latest OAA-2 testing exposed a related regression:

```text
Card accept completed, but workboard produced no signal wake because manager never left running/settlement state.
```

Continuing to have auto enabled is not itself a bug. Continuous auto can remain enabled, and once mode should only exit through its normal completion path. The bug is that terminal settlement leaves the manager in `running`/`thinking`, so workboard signal evaluation is blocked and auto-once completion cannot progress.

This has three different layers. The automatic reviewer accept path does call `_notify_background_terminal(...)`, so the primary failure for automatic accept is not missing notification. The more likely root cause is that terminal settlement is short-circuited by stale auto runtime state before workboard actionability is evaluated.

### Finding 1: Terminal Callback Is Short-Circuited By Stale `running` State

The automatic reviewer path calls `_notify_background_terminal(project_id, run_id=run_id)` after successful reviewer acceptance. That reaches:

```text
ManagerAutoService.notify_background_task_terminal(...)
```

However, `notify_background_task_terminal(...)` only clears `active_run_id` / `active_job_id`. It does not move `state.state` out of `running` or `thinking`.

Immediately after that, it calls:

```text
evaluate_workboard_and_maybe_signal(project_id, owner_session_id)
```

But `evaluate_workboard_and_maybe_signal(...)` currently has this guard:

```python
if state.state in {"running", "thinking"} and not from_turn_settlement:
    return state
```

So an auto-owned run can finish, clear `active_run_id`, and still skip all workboard signal evaluation because `state.state` remains `running`. That explains why automatic accept can fail to enqueue a workboard wake.

Required fix:

- Treat background terminal notification as a settlement path, not as ordinary opportunistic evaluation.
- Either call `evaluate_workboard_and_maybe_signal(..., from_turn_settlement=True)` from `notify_background_task_terminal(...)`, or set `state_value="idle"` while clearing `active_run_id` / `active_job_id` before evaluating.
- Prefer the first option if we want terminal settlement to reuse the settlement requeue guard: it wakes only when a new startable frontier or manager blocker exists.
- The stale `running`/`thinking` guard should only suppress unsolicited polling while work is in flight; it must not suppress terminal settlement.

Cross-check with doc 39:

- Using `from_turn_settlement=True` is consistent with the wake-loop fix because completed receipts alone stay non-actionable.
- This path depends on binding-aware flow correctly deriving `has_startable_frontier=True` when an upstream accept makes a downstream card ready.
- If the logical/materialized chain is still broken, terminal settlement will run but the settlement guard will correctly refuse to enqueue because it cannot see a real startable frontier.

### Finding 2: Manual Accept Does Not Notify Auto Terminal Settlement

`WorkerService.review_run(...)` calls `_finalize_run_review(...)`, appends a review event, commits the run stage, and returns. It does not call `_notify_background_terminal(project_id, run_id=run_id)`.

By contrast, the automatic reviewer path calls `_notify_background_terminal(...)` after a successful reviewer acceptance, and failure/cancel paths also call it.

That means Manager/tool-driven accept can finish a run without calling:

```text
ManagerAutoService.notify_background_task_terminal(...)
```

So auto state is not forced to clear `active_run_id`, reevaluate workboard readiness, enqueue a new wake, or settle to completed/blocked.

Required fix:

- After `WorkerService.review_run(...)` commits the reviewed stage, call `_notify_background_terminal(project_id, run_id=run_id)` for all terminal review outcomes: accepted, explicit reject, mapping ambiguity, and consistency failure.
- The callback should be idempotent. If another path already notified the same terminal run, reevaluation should not enqueue duplicate wakes because wake idempotency is fingerprint-scoped.
- Keep this outside the project lock, same as other `_notify_background_terminal(...)` call sites, to avoid callback reentry into `ManagerAutoService` while holding the graph lock.

### Finding 3: Completed Receipt Alone Is No Longer Wake Fuel

Doc 39 intentionally changed completed workboard receipts to be non-actionable by default. Therefore an accepted run should only enqueue a wake when it creates one of these semantic frontier facts:

- a downstream card becomes `ready_to_start`;
- a new unresolved manager blocker appears;
- a dependency/materialization blocker requires explicit attention.

If accept only creates a `completed/card_run_reviewed` receipt, `BackgroundWorkboardService.signal_snapshot(...)` correctly returns no fingerprint and no wake.

This is expected after doc 39, but it makes doc 40 correctness more important: downstream readiness must come from the stable logical chain plus current materialization binding. If accept rewrites producer output ids or fails to expose a current materialization, the downstream `ready_to_start` projection may never appear, so no wake is generated.

Required fix:

- The accept terminal callback must always trigger `evaluate_workboard_and_maybe_signal(...)`.
- The signal should be emitted only when the binding-aware flow sees a real downstream frontier or blocker.
- The absence of a wake is valid only when `evaluate_workboard_and_maybe_signal(...)` updates auto state to `completed`, `blocked`, or `idle` according to the current workboard.

### Finding 4: Auto-Once Completion Is Blocked By The Same Stale-State Bug

`ManagerWakeProcessor` stops `mode=="once"` only after it processes a wake turn:

```text
wake -> run_to_session -> evaluate_workboard_and_maybe_signal(..., from_turn_settlement=True) -> stop(auto_once_complete)
```

If terminal settlement is skipped because manager is still `running`, the wake processor never gets a valid follow-up signal and once completion cannot progress. The visible symptom is "auto once did not exit", but the root bug is not that auto remains enabled; it is that the manager never reached an idle/settled state where signal evaluation can decide the next step.

Required fix:

- `ManagerAutoService.notify_background_task_terminal(...)` must clear the runtime boundary and allow workboard settlement to run.
- Do not treat `enabled=true` as a failure condition by itself.
- Only complete/stop `mode=="once"` through the existing once completion semantics after settlement has actually run.
- If no wake is enqueued because there is no actionable frontier, auto state should still reflect the settled workboard state (`idle`, `blocked`, or `completed`) instead of remaining `running`.

### Finding 5: Doc 40 Binding Fix Determines Whether Accept Creates A Frontier

For OAA-2, the expected accept sequence should be:

```text
accept Diff run
-> preserve logical output id asset_deg_table
-> update asset_materializations[asset_deg_table].current_asset_id
-> Flow resolves downstream logical input asset_deg_table to current concrete asset
-> Workboard derives ready_to_start for downstream planned cards
-> ManagerAutoService enqueues workboard_actionable
```

If any part still relies on producer `card.outputs[].asset_id` being the concrete asset, downstream readiness can disappear after accept. That is exactly the logical/materialized binding bug this document is meant to fix.

Required fix:

- Treat accept as a materialization update plus terminal settlement.
- Treat downstream readiness as resolver output, not as downstream input rewrites.
- Add a regression test where a downstream planned card has a logical input, upstream accept updates materialization, and workboard wake is generated.

### Regression Tests For This Bug

Add backend tests for:

- Automatic reviewer accept while auto state is `running` clears `active_run_id`, evaluates workboard, and does not get short-circuited by the `running` guard.
- `notify_background_task_terminal(...)` calls terminal settlement with `from_turn_settlement=True` or otherwise resets state before evaluation.
- Manager/manual `review_run(..., accept=True)` calls the background terminal callback and reevaluates auto.
- Reviewer auto-accept and manual accept share the same terminal settlement behavior.
- Accepting an upstream card with a downstream logical input creates a `ready_to_start` workboard item and enqueues `workboard_actionable`.
- Upstream accept followed by terminal settlement produces a binding-aware `ready_to_start` projection, `signal_snapshot.actionability.has_startable_frontier == true`, and the settlement guard enqueues a wake.
- Accepting a final card with no downstream frontier does not enqueue a wake, but auto state is settled out of `running`.
- Continuous auto may remain enabled after settlement; the assertion should check settled state, not forced stop.
- Auto-once exits only when the normal once completion path runs; the test should verify the stale `running` guard no longer prevents that path from being reached.
- A completed `card_run_reviewed` receipt alone does not wake unless a binding-aware downstream frontier or manager blocker exists.

## Follow-Up Tests

Add backend tests for:

- Accepting a run does not change the logical output id.
- Accepting a run updates current materialization for that logical output.
- Downstream planned card input remains logical after upstream accept.
- Workboard marks downstream card ready when logical input has valid materialization.
- Task packet resolves logical input to concrete path.
- Task packet preserves `requested_asset_id` and stores concrete `asset_id` / `asset_path`.
- Upstream rerun updates materialization without rewriting downstream logical input.
- Accepted downstream card receives attention when it was run against an older concrete materialization.
- Flow reports logical dependency missing separately from concrete materialization missing.
- Dependency Attention reports stale/invalid concrete bindings without claiming the logical DAG is missing.
- Accepted logical output with valid current materialization does not emit `output_asset_not_valid`.
- Direct concrete input id still works and is classified as `direct_asset_id`.
- Legacy project with only `Asset.metadata.planned_asset_id` resolves through alias fallback.
- Concrete downstream input with unambiguous planned alias emits a compatibility warning, not an automatic rewrite.
- Superseding an upstream output marks the old concrete materialization superseded while preserving the logical output id.
- Multi-rerun chain: the same card is accepted three times, the logical output id stays stable, current materialization points to the latest concrete asset, and older concrete ids are tracked as superseded.
- Bootstrap then accept: a legacy project resolves bindings from `metadata.planned_asset_id`, then a new accept writes an explicit binding map and preserves aliases.

Add frontend/build checks for:

- card dependency lines still render from logical output id to logical input id;
- result/file links use concrete `linked_assets` or `current_asset_id`;
- logical output ids are displayed as contract ids, not clickable concrete result ids unless resolved.

## Boundary With Existing Alias Fix

The current `planned_asset_id` alias is still useful as compatibility data.

But it should be treated as a bridge, not the permanent model. The permanent model should make the logical chain first-class and make concrete file binding explicit.
