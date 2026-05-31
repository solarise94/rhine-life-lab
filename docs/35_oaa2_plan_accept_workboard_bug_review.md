# OAA-2 Plan Accept And Workboard Bug Review

Status: bug review note.

Date: 2026-05-31

## Summary

OAA-2 exposed two blocking issues in the plan/accept/workboard path.

The first issue is dependency identity drift: when an upstream card run is accepted, the backend replaces the card's planned output asset id with the concrete run output asset id. Downstream planned cards still reference the original planned asset ids, so their dependencies become missing even though the upstream result exists.

The second issue is missing workboard continuation: after a plan/proposal is accepted, the workboard is not re-evaluated through the Manager auto signaler, so Manager is not woken to continue consuming ready work or resolve new blockers.

These are separate bugs. The first corrupts the ready frontier. The second prevents the autonomous session from resuming when the frontier changes.

## Current OAA-2 Evidence

Current accepted upstream outputs include concrete run asset ids:

```text
asset_run_91a53b3c53a5_cleaned_matrix_1
asset_run_23e40a696a1c_sample_metadata_1
```

But downstream planned cards still reference the original planned ids:

```text
asset_cleaned_matrix
asset_sample_metadata
```

The current work order reports downstream cards as not startable:

```text
block_reasons = ["missing_required_assets"]
```

The dependency attention analysis also reports the same planned cards as having missing inputs:

```text
dependency_attention_count = 20
kind = input_asset_missing
asset_id = asset_cleaned_matrix / asset_sample_metadata
```

The current workboard snapshot has actionable items:

```text
completed = 4
has_actionable = true
```

But the project graph currently has no active manager auto state:

```json
"manager_auto": null
```

So the workboard signaler has no enabled owner session to wake.

## Bug 1: Planned Asset Identity Is Lost On Run Accept

### Root Cause

`WorkerService._finalize_run_review` binds card outputs directly to concrete run assets during accept:

```python
out.asset_id = real_asset.asset_id
out.status = real_asset.status
```

This makes the accepted producer card point to the materialized run asset, but it removes the logical planned output id from the card contract.

Downstream cards were planned against the logical ids created during planning:

```text
asset_cleaned_matrix
asset_sample_metadata
```

Once the producer no longer advertises those ids as outputs, `AssetTimelineService.producer_maps` can no longer map those planned ids to their producer cards. `FlowService.get_work_order` then treats downstream inputs as missing because they are neither materialized assets nor known planned outputs:

```python
if asset_id not in asset_map and asset_id not in timeline["producer_by_asset"]
```

### Why This Is Wrong

Planned asset ids are dependency contract ids. Concrete run assets are materializations of that contract.

Accepting a run should not make downstream planned cards lose their source dependency. A planned downstream card has not run yet, so its dependency should remain valid as a logical dependency on the upstream output role or planned asset id.

### Expected Behavior

Accepting an upstream run should preserve one of these invariants:

- The planned output asset id remains the producer card's stable contract id, and the concrete run asset is linked as its materialization.
- Or the backend atomically rebases all downstream planned inputs from the planned id to the concrete asset id.
- Or the timeline/flow layer resolves planned ids through an alias/materialization map.

The first option is the cleanest long-term model: logical asset identity should stay stable; physical run outputs should be materialized evidence.

### Chosen Direction: Asset Metadata Alias

For the near-term fix, use an alias map based on existing asset metadata rather than adding a new `CardOutputSpec` field.

The runtime already has a compatible field:

```text
Asset.metadata["planned_asset_id"]
```

This is preferable to adding `CardOutputSpec.materialized_from` because it avoids a schema migration and keeps the change localized to the run-accept and timeline/read paths.

Suggested write behavior:

- when a planned output slot is bound to a concrete run asset, remember the old `output.asset_id`;
- if the concrete `Asset.metadata["planned_asset_id"]` is missing, set it to that old planned id before saving graph state;
- keep the existing visible behavior that `card.outputs[].asset_id` points at the materialized asset for accepted cards.

Suggested read behavior:

- `AssetTimelineService.producer_maps` should register both the concrete asset id and the planned alias id to the same producer card;
- `FlowService.get_work_order` can keep its current `missing_required_assets` semantics because `timeline["producer_by_asset"]` will now include the alias.

The metadata read path should be defensive:

```python
metadata = asset.metadata if isinstance(asset.metadata, dict) else {}
planned_asset_id = metadata.get("planned_asset_id")
```

It should not assume that metadata is present, non-empty, or well typed.

### Existing OAA-2 Data Repair

The metadata alias fix only protects future accepts. OAA-2 already has accepted run assets whose metadata contains `role` but no `planned_asset_id`.

Do not add long-term runtime fallback logic only to support this historical state. Prefer a one-time repair after the code fix:

- locate accepted producer cards whose downstream planned cards still reference old planned output ids;
- identify the matching concrete output asset by matching the producer card's pre-accept planned asset id against accepted run assets created by that card;
- write `planned_asset_id` into the concrete asset metadata;
- re-run workboard/frontier evaluation.

The repair script should not rely only on output role. It should recover the planned id from card history, proposal patch, or another pre-accept snapshot when available, then match that planned id to the concrete accepted run asset for the same producer card. Role can be a fallback only when the producer card has a single unambiguous output for that role.

This keeps historical recovery separate from normal runtime behavior.

### Important Boundary

This bug is not merely a Dependency ATTENTION issue. Even if attention is hidden, the ready frontier is still broken because `FlowService` sees `missing_required_assets`.

## Bug 2: Planned Cards Are Incorrectly Included In Dependency ATTENTION

### Root Cause

`DependencyAttentionService._analyze_card_inputs` currently skips only inactive cards:

```python
if card.status in self.INACTIVE_CARD_STATUSES:
    return
```

That means `planned` cards are analyzed as if they were already part of the executed result graph. When their expected upstream ids are not materialized, the service reports:

```text
input_asset_missing
input_asset_outdated
asset_lineage_invalid
```

### Why This Is Wrong

Dependency ATTENTION is a repair/diagnostic surface for already-executed or stale result chains. It should not treat a not-yet-run planned card as an invalid result.

For planned cards, dependency readiness belongs to the work order/frontier layer:

```text
can_start
block_reasons
ready_to_start
waiting_capacity
```

ATTENTION should not tell Manager to repair a planned card merely because its logical upstream output has not been materialized yet.

### Expected Behavior

By default, Dependency ATTENTION should skip planned/proposed cards for input diagnostics.

Suggested rule:

```text
Only analyze input dependency attention for accepted, failed, stale, superseded, needs_review, or previously-run cards.
Do not analyze plain planned/proposed cards unless an explicit diagnostic mode asks for planning validation.
```

The work order can still expose planned-card blockers through `block_reasons`, but those blockers should not become ATTENTION issues.

### Chosen Direction: Explicit Input-Diagnostic Whitelist

Do not stretch the current `INACTIVE_CARD_STATUSES` name to include `planned` or `proposed`; those cards are not inactive, they are just not eligible for result-chain ATTENTION.

Prefer a whitelist such as:

```text
ATTENTION_INPUT_ELIGIBLE_STATUSES = {
  "accepted",
  "failed",
  "stale",
  "needs_review",
  "superseded",
}
```

The default path should skip cards outside that set.

Notes:

- `cancelled`, `rejected`, `proposed`, and `planned` are not eligible by default.
- `superseded` stays eligible by default. This preserves current diagnostic behavior over replaced result chains.
- Keep an internal `include_planned=False` parameter for future planning-validation tools, but do not expose it through the current Manager-facing ATTENTION API yet.

## Bug 3: Proposal Accept Does Not Re-Evaluate Workboard Or Wake Manager

### Root Cause

The proposal accept API currently applies the patch and marks the proposal accepted:

```text
get proposal
apply_patch
mark proposal accepted
return snapshot
```

It does not call:

```text
ManagerAutoService.evaluate_workboard_and_maybe_signal
ManagerAutoService.notify_turn_settled
```

So if accepting a plan creates new ready work, completed items, or manager-needed blockers, the workboard state changes but the Manager auto loop is not resumed.

### Current Additional Constraint

`ManagerAutoService.evaluate_workboard_and_maybe_signal` only emits a wake if the project has an enabled auto owner session:

```python
if not state.enabled or state.owner_session_id != session_id:
    return state
```

OAA-2 currently has:

```json
"manager_auto": null
```

So the signaler has no session to wake even though the workboard snapshot reports actionable items.

### Expected Behavior

There should be a bridge between proposal accept and workboard continuation, but only when the accept belongs to an explicitly authorized autonomous session.

Suggested rule:

```text
If proposal accept occurs inside an active /auto session:
  apply patch
  refresh workboard
  evaluate workboard
  if actionable and consume_workboard is allowed, enqueue workboard_actionable

If proposal accept occurs outside /auto:
  apply patch
  update UI/project state
  do not auto-wake Manager for autonomous work
```

This preserves the product boundary: accepting a plan in normal mode should not silently start autonomous execution, while accepting a plan inside `/auto` should allow the Manager to continue the authorized work session.

### Chosen Direction: Explicit Frontend Session Parameter

Pass the active chat session id explicitly from the frontend when accepting a proposal.

Suggested API shape:

```text
acceptProposal(projectId, proposalId, sessionId?)
```

The backend should treat `session_id` as optional:

- if no `session_id` is provided, preserve the current non-auto behavior;
- if a `session_id` is provided, only evaluate/signal workboard when it matches the active auto owner session and `consume_workboard` is true.

Do not infer the session from auth, cookies, or a global user context. The relevant identity is the Manager auto owner session, not merely the current browser user.

Suggested backend guard:

```python
state = manager_auto_service.get_state(project_id)
if (
    session_id
    and state.enabled
    and state.owner_session_id == session_id
    and state.consume_workboard
):
    manager_auto_service.evaluate_workboard_and_maybe_signal(project_id, session_id)
```

This should reuse the existing guard inside `evaluate_workboard_and_maybe_signal`; the outer guard just avoids unnecessary workboard snapshot computation for non-auto accepts.

## Likely Fix Plan

### P0: Stop False ATTENTION On Planned Cards

Change Dependency ATTENTION so plain planned/proposed cards do not produce input dependency repair issues.

Add tests:

- planned downstream card referencing a planned upstream output should not produce `input_asset_missing`.
- accepted downstream card referencing a superseded/missing upstream asset should still produce ATTENTION.

### P0: Preserve Planned Output Identity Through Accept

Run acceptance must not make downstream planned inputs lose their source.

Chosen implementation:

- use `Asset.metadata["planned_asset_id"]` as the alias source;
- backfill that metadata at accept time if it is missing;
- register planned aliases in `AssetTimelineService.producer_maps`;
- keep downstream card contracts unchanged;
- do not add a new `CardOutputSpec` field in this repair.

Add tests:

- accepting a run with a planned output id writes `planned_asset_id` into the concrete asset metadata;
- `AssetTimelineService.producer_maps` maps both concrete and planned ids to the same producer;
- `FlowService.get_work_order` treats downstream planned inputs as resolvable through the alias;
- the previous test that asserted planned ids disappear from outputs should be flipped into "materialized output visible, planned alias still resolvable."

Additional alias regression tests are useful if the implementation is already touching review state:

- first run rejected, second run accepted for the same planned output id resolves the alias to the accepted materialization;
- accepted run followed by rerun keeps the planned alias pointed at the latest accepted output, not the stale candidate.

After code is fixed, run a one-time OAA-2 repair to write missing `planned_asset_id` metadata for already accepted assets.

### P0: Workboard Re-Evaluate After Proposal Accept In Auto Session

Extend proposal accept handling to carry the current session id and auto context.

Frontend touch points:

- `frontend/lib/api.ts`: extend `acceptProposal(projectId, proposalId, sessionId?)`;
- `frontend/components/manager-chat/ManagerChatPanel.tsx`: pass the current chat `sessionId` when accepting a proposal.

After successful patch apply:

- if the accepting session owns active auto and has `consume_workboard`, call `evaluate_workboard_and_maybe_signal`;
- otherwise emit only normal project/UI state refresh.

Add tests:

- accepting a proposal in active auto creates ready work and enqueues `workboard_actionable`;
- accepting a proposal outside auto does not enqueue a wake;
- accepting a proposal with only blocked work sets auto state to blocked if inside auto.

### Repair Order

Use this order:

1. Fix planned-card ATTENTION eligibility.
2. Fix planned asset alias read/write.
3. Run the OAA-2 one-time metadata repair.
4. Wire proposal accept to auto-session workboard evaluation.

The order matters. Workboard signaling after proposal accept is only useful once the ready frontier is correct; otherwise Manager wakes up only to see the same `missing_required_assets` blockers.

## Non-Goals

This note does not propose changing Manager prompt behavior directly.

The broken state is backend-derived:

- `FlowService` reports missing required assets.
- `DependencyAttentionService` reports planned-card issues.
- `accept_proposal` does not trigger workboard signaling.

Prompt changes cannot reliably repair these conditions.

## Review Conclusion

The OAA-2 stall is caused by backend state-transition bugs, not by Manager hesitation.

The dependency model currently conflates:

- planned output contract ids;
- concrete run output asset ids;
- dependency attention repair diagnostics;
- workboard readiness.

The next repair should restore those boundaries:

- planned cards use frontier/readiness, not ATTENTION;
- accepted runs materialize outputs without breaking logical dependencies;
- proposal accept inside `/auto` re-enters the workboard signal loop.
