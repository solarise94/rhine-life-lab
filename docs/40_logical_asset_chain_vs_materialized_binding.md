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

### Short-Term Rule

Do not rely on Manager to rewrite downstream card inputs to concrete run asset ids.

If downstream cards have logical inputs, preserve them. Use resolver output in launch and diagnostics.

### Medium-Term Change

Stop mutating `card.outputs[].asset_id` from logical planned id to concrete run asset id on accept.

Instead, preserve:

```text
card.outputs[].asset_id = asset_deg_table
```

and update a materialization binding:

```text
asset_deg_table.current_asset_id = asset_run_dbe18836fec2_deg_table_1
```

If a dedicated binding table is too large a schema change, the transition can still use materialized asset metadata as a backing store, but code should expose a first-class API that behaves like a binding table.

### Long-Term Change

Introduce explicit fields or records:

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

## Migration Considerations

Existing projects already contain cards whose outputs were rewritten to concrete ids.

Migration should:

1. For each materialized asset with `metadata.planned_asset_id`, recover the logical id.
2. For each accepted card output that points to a materialized asset, rewrite or expose the logical id as the contract id.
3. Preserve the concrete id as the current materialization.
4. Avoid changing downstream inputs that already use logical ids.
5. For downstream inputs already rewritten to concrete ids, optionally convert back to logical ids when the concrete asset has an unambiguous `planned_asset_id`.

The last step should be explicit and auditable because concrete bindings may be intentional in some workflows.

## Follow-Up Tests

Add tests for:

- Accepting a run does not change the logical output id.
- Accepting a run updates current materialization for that logical output.
- Downstream planned card input remains logical after upstream accept.
- Workboard marks downstream card ready when logical input has valid materialization.
- Task packet resolves logical input to concrete path.
- Upstream rerun updates materialization without rewriting downstream logical input.
- Accepted downstream card receives attention when it was run against an older concrete materialization.
- Flow reports logical dependency missing separately from concrete materialization missing.
- Dependency Attention reports stale/invalid concrete bindings without claiming the logical DAG is missing.

## Boundary With Existing Alias Fix

The current `planned_asset_id` alias is still useful as compatibility data.

But it should be treated as a bridge, not the permanent model. The permanent model should make the logical chain first-class and make concrete file binding explicit.

