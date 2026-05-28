# OAA Dependency Attention Review And Remediation Plan

## Background

This document records the review conclusion from the OAA demo project after the user replaced the root input count matrix and asked Manager to repair the downstream workflow.

The expected behavior was:

1. `rna_prep` reruns on the new uploaded raw count matrix.
2. `rna_pca` and `rna_deg` rerun from the new `rna_prep` outputs.
3. `rna_enrich` and `rna_tf` rerun from the new `rna_deg` outputs.
4. `rna_enrich_viz`, `rna_immune_v2`, and `rna_html_report` are repaired or rerun as needed.
5. Cards whose dependency chain is fully repaired should return from ATTENTION to normal.

Observed behavior:

- `rna_enrich` and `rna_tf` returned to normal.
- `rna_prep`, `rna_pca`, and `rna_deg` still showed dependency ATTENTION.
- `rna_html_report` behavior was confusing: a first repair run failed on duplicate output paths, a later repair run succeeded, but the auto/tool activity was not visible enough in chat.

The important conclusion is that the ATTENTION service was not the primary source of the bug. It correctly reported inconsistency in the project graph. The underlying problem is that accepted/reviewed runs can leave real output assets in `candidate` state or leave card output contracts bound to placeholders.

Latest audit conclusion:

- This is not a frontend cache issue. `/api/projects/oaa/work-order` currently returns `dependency_attention_count=27`.
- This is not a current main-path dependency edge break. The repaired main chain was rerun and most downstream cards reached `accepted`.
- The remaining user-visible ATTENTION on accepted cards is caused by persisted graph inconsistency: accepted cards point their outputs at real assets whose `Asset.status` is still `candidate`.
- The project detail endpoint does not currently attach `dependency_attention_count` to `cards[]`; the work-order/card-detail paths do. This can make some frontend surfaces look less consistent, but it is not the root cause of the remaining ATTENTION.

## Review Conclusion

### 1. `rna_prep`, `rna_pca`, and `rna_deg` remained in ATTENTION because output assets stayed candidate

Current OAA facts:

- `run_47116f4661a4` (`rna_prep`) is `reviewed`, but its output assets are still `candidate`.
- `run_192d2b74a4d2` (`rna_pca`) is `reviewed`, but its output assets are still `candidate`.
- `run_a90a702df34d` (`rna_deg`) is `reviewed`, but its output assets are still `candidate`.
- The cards themselves are `accepted`.
- Their `card.outputs[].status` values claim `valid` or `materialized`.
- The canonical `graph.assets[]` entries disagree and remain `candidate`.

Their cards are `accepted`, and their `card.outputs[]` claim valid/materialized outputs. The real `Asset.status` values disagree.

`DependencyAttentionService` correctly reports:

```text
output_asset_not_valid: accepted card output points to candidate asset
```

Therefore, the visible ATTENTION is a correct symptom of graph inconsistency, not a stale UI flag that failed to clear.

### 2. `rna_enrich`, `rna_tf`, and later downstream cards returned to normal where their real output assets are valid

Current OAA facts:

- `run_7c1cc89cd66a` (`rna_enrich`) output assets are `valid`.
- `run_653beae40c56` (`rna_tf`) output assets are `valid`.
- `run_bbf48df84cf9` (`rna_immune_v2`) output assets are `valid`.
- `run_4541cc7b5265` (`rna_enrich_viz`) output assets are `valid`.
- `run_466b5ea867b2` (`rna_html_report`) output assets are `valid`.

Those cards have no dependency attention, which confirms that the derived ATTENTION calculation can clear naturally when the graph becomes consistent.

Important nuance: the current `DependencyAttentionService` treats `candidate` as an allowed input status. Therefore a downstream accepted card can consume candidate upstream assets without receiving an input issue. This is intentional per the current product decision: candidate input itself does not raise ATTENTION; accepted card output pointing to candidate does raise ATTENTION because accepted output and candidate asset state conflict.

### 3. `rna_html_report` was rerun; the confusing part was visibility and an intermediate failed repair run

Current OAA facts:

- `rna_html_report` was started as `run_9e24960bd436` and failed because the manifest contained duplicate output paths.
- It was then started again as `run_466b5ea867b2` and reached `reviewed`.
- The current `rna_html_report` card is `accepted`, its output `asset_run_466b5ea867b2_report_html_1` is `valid`, and it has no dependency attention.
- An earlier successful run, `run_0b9e3a459aeb`, generated real assets but had output binding ambiguity because the card output contracts still used placeholders such as `asset_html`, `asset_planned_asset`, and `asset_planned_asset_2`.
- Manager later claimed the outputs were rebound to real assets, but the graph state did not consistently reflect that claim at the time of review.

This is a second instance of the same broader problem: Manager/tool responses can say a run was accepted or bindings were fixed while the persisted graph still contains placeholder outputs or candidate assets.

For the latest audited state, `rna_html_report` itself is not the remaining problem. The current problem is the earlier foundational cards whose accepted outputs still point to candidate assets.

### 4. Cancelled historical branches still produce attention noise

Current OAA facts:

- `rna_immune` is `cancelled` but still reports dependency attention from old lineage/outdated inputs.
- `rna_enrich_viz_v2` is `cancelled` but still reports dependency attention from superseded old enrichment inputs.

This is not part of the active repaired path. It happens because `DependencyAttentionService.analyze_project()` still analyzes inputs for every card, including cancelled cards. It only scopes output checks to accepted cards.

Recommended product behavior: user-facing ATTENTION should skip inactive cards (`cancelled`, `rejected`, `superseded`) by default. A diagnostic/debug mode can still include inactive-card issues if needed.

### 5. Auto mode manager actions are too opaque in chat

Manual chat uses streaming events, so `tool_start` / `tool_end` timeline entries appear in the UI.

Auto wake handling currently calls the non-streaming `manager_service.chat()` path and appends only a final manager text message. Tool calls may execute, but their timeline is not persisted to the chat session.

This makes auto mode look like nothing happened, or like Manager jumped to a conclusion without showing the intermediate tool calls.

### 6. Running-state pre-review and wake ordering audit

Concern: the Reviewer pre-check is a performance optimization. If review could finish while the executor run is still `running`, it might enqueue auto wake events twice or let Manager update dependency state while the original run is still writing outputs.

Current code audit:

- The main `WorkerService._execute_run()` path does not run Reviewer concurrently with the executor process.
- The executor subprocess is awaited first with `process.wait(...)`.
- The stdout reader is joined with `reader.join(timeout=2)`.
- Filesystem audit, blocking dependency checks, and manifest validation run after the process exits.
- Only after those steps does the service append `review_started`, set run/card status to `reviewing`, and call `executor_validation_service.validate_run(...)`.
- Therefore, the main acceptance path is ordered as `running -> reviewing -> reviewed/failed`; a normal Reviewer acceptance should not happen before the executor has exited.

The actual ordering risk is not normal Reviewer pre-review. It is structured executor events during stdout pumping:

- `_handle_structured_executor_event()` handles `BP_EVENT issue_report` while the process may still be running.
- If `needs_manager=true`, it calls `_set_run_attention(...)` and enqueues `card_needs_manager`.
- `ManagerWakeProcessor` may then wake Manager while the run is still active.

This conflicts with the earlier auto wake design in `docs/18_manager_auto_mode_wake_hook_plan.md`, which says the first implementation should wake after the run has failed/stopped, not while the executor process is still running.

Risks:

- Manager can receive a wake for a run that is still `running`.
- Manager may inspect a graph whose run output state is incomplete.
- Manager may make card/input/output changes while the executor is still writing.
- A later terminal wake (`card_run_reviewed`, `card_run_failed`, `manifest_validation_failed`, etc.) can still arrive for the same run, causing two manager turns for one lifecycle.
- Dependency attention or output binding decisions made from the early wake can be stale by the time the terminal state is persisted.

The current idempotency keys prevent exact duplicate wake events of the same kind, but they do not collapse semantically related events such as:

```text
run:<run_id>:needs_manager:<hash>
run:<run_id>:reviewed
```

Those are different lifecycle events and can both wake Manager.

Additional auto-state issue:

- `ManagerWakeProcessor` clears `active_run_id` for `card_run_reviewed`, `card_run_failed`, `card_run_cancelled`, `manifest_validation_failed`, and `executor_validation_failed`.
- It does not currently clear `active_run_id` for `runtime_dependency_missing`.
- `WorkerService` persists runtime dependency missing as a terminal failed run, so the auto state can incorrectly keep a stale `active_run_id`.

## Likely Root Causes

### A. Run review is not fully idempotent

`WorkerService._finalize_run_review()` materializes run assets as `candidate` first, then promotes them to `valid` only if output mapping passes.

If a run is already reviewed and Manager calls `review_card_run` again, the same assets can be reset to `candidate`. If the second review path hits an output mapping ambiguity, the run may remain reviewed while assets stay candidate.

Concrete failure point:

```python
if existing:
    existing.status = status
```

Because `_finalize_run_review()` initially passes `status="candidate"` into `_materialize_run_assets()`, this unconditional upsert can demote an existing `valid` asset back to `candidate`.

The system must treat reviewed runs carefully:

- Re-reviewing an already accepted/reviewed run should not demote valid assets to candidate.
- Re-reviewing should be idempotent when graph state is already accepted.
- If remapping fails, the API response must make that failure explicit and must not claim acceptance.

### B. Output mapping is fragile when manifest assets omit `asset_id`

Some manifests list created outputs by `role` and `path` only, with `asset_id: null`.

The current mapping logic has a role fallback, but expected outputs also include system outputs (`run_summary`, `run_preview`) that can duplicate card-declared output roles. This can make planned id resolution ambiguous.

The system should map produced assets using a stable priority:

1. Exact manifest `asset_id` if present and resolvable.
2. Manifest `asset_id` as `planned_asset_id`.
3. Unique produced asset by `metadata.role`, scoped to the current run.
4. Unique produced asset by normalized output path.

Duplicate system outputs must not override user/card output contracts for the same role.

### C. API success can drift from persisted graph truth

A mutating tool response can report success even when the persisted graph remains inconsistent.

Examples from the incident:

- Card reported accepted while real output assets remained `candidate`.
- Manager message claimed output assets were rebound while `card.outputs[]` still contained placeholder IDs.

Every accept/review/update path should verify persisted graph truth after saving, especially for output asset status and output contract binding.

Two concrete review-path drift points must be fixed:

- `_finalize_run_review()` currently writes `run.status = "reviewed"` in the common tail after accept, needs-review downgrade, and reject branches. A run whose card lands in `needs_review` because output mapping failed should not be indistinguishable from a fully reviewed accepted run.
- `review_run()` returns the original `accept` argument instead of returning the final `result["accepted"]` from `_finalize_run_review()`. If `_finalize_run_review()` downgrades acceptance because mapping failed, the tool/API can still report accepted.
- `review_run()` also appends the `manager_review` event message from the original `accept` argument. The message `"Manager 已接受运行结果。"` must be based on final `result["accepted"]`; otherwise the event stream can claim acceptance even when output mapping downgraded the run to `needs_review`.

Use the existing run status model consistently:

- `success`: executor and manifest succeeded, but review acceptance is not complete. This is the correct persisted run status when output mapping is ambiguous and the card is `needs_review`.
- `reviewed`: review has completed and the persisted graph reflects the final review outcome. For accepted review, this requires card `accepted` and valid bound outputs. For explicit reject, card `rejected` and produced assets remain `candidate`.
- Do not add a new run status just for card `needs_review` in this remediation unless the broader run status model is updated at the same time.

### D. Auto wake processing does not persist tool timeline

`ManagerWakeProcessor` uses non-streaming chat execution and stores only final text/thinking. It does not capture the tool events emitted by the manager sidecar.

This is separate from dependency correctness, but it makes operational debugging difficult and creates a poor user experience.

### E. Running-time `needs_manager` wake can race terminal run state

Structured executor events can enqueue `card_needs_manager` while the subprocess is still running. This is a legitimate future capability if the system supports true pause/resume or interactive execution, but the current implementation does not stop or pause the executor before waking Manager.

For the current auto design, this should be treated as an ordering bug:

- running-time `needs_manager` should update run/card progress and attention locally;
- it should not enqueue an auto wake until the run is terminal or explicitly paused/stopped;
- if an early wake is required, the worker must first transition the run to a blocking state and ensure the executor is no longer mutating project outputs.

## Remediation Plan

### P0: Make run review a single graph transaction

Implement `_finalize_run_review()` as one ordered transaction. Do not fix materialization, mapping, and consistency checks as independent partial patches.

Recommended order:

1. Load run, card, graph, task packet, manifest, and review context under the project lock.
2. Build the produced asset set for `run_id`.
3. If assets for this `run_id` already exist, reuse them and never demote an existing `valid` asset to `candidate`.
4. For newly discovered produced assets, materialize them as `candidate`.
5. Resolve output mappings deterministically.
6. If mapping is ambiguous:
   - keep produced assets as `candidate`;
   - set `card.status = "needs_review"`;
   - set `run.status = "success"` to mean execution and manifest succeeded but review acceptance is incomplete;
   - set `run.finished_at`;
   - return `accepted=False`;
   - do not emit or surface "Reviewer accepted" wording;
   - include unmapped planned IDs and suggested binding candidates.
7. If the caller explicitly rejects:
   - keep produced assets as `candidate`;
   - set `card.status = "rejected"`;
   - set `run.status = "reviewed"` because review is complete;
   - return `accepted=False`.
8. If accepting:
   - replace planned/placeholder `card.outputs[].asset_id` with real `asset_run_*` IDs;
   - promote produced output assets to `valid`;
   - attach claims/report items;
   - supersede previous outputs;
   - clear `run.needs_manager_attention`;
   - run the acceptance consistency helper;
   - save graph/cards;
   - return `accepted=True`.

Acceptance has a strict contract:

- `run.status == "reviewed"`
- `card.status == "accepted"`
- every accepted `card.outputs[].asset_id` exists in `graph.assets`
- each accepted output asset has `status == "valid"`
- `card.outputs[].status` agrees with the real asset status

Add and reuse a helper such as:

```python
def _assert_acceptance_graph_consistent(card: Card, graph: GraphState, run_id: str) -> None:
    ...
```

Call it before saving an accepted review result. If it fails, the transaction must not persist an accepted card.

### P0: Resolve output mapping deterministically

Output mapping is part of the review transaction. The mapping rules should be centralized so `_detect_unmapped_outputs()` and `_sync_card_outputs()` cannot diverge.

Mapping priority:

1. Exact manifest `asset_id` if present and resolvable.
2. Manifest `asset_id` as `planned_asset_id`.
3. Unique produced asset by `metadata.role`, scoped to the current run.
4. Unique produced asset by normalized output path.

Rules:

- Build expected mappings without letting auto-added system outputs override card-declared outputs.
- Treat placeholders like `asset_planned_asset`, `asset_planned_asset_2`, and other generated planned IDs as planned IDs, not as real assets.
- If role is duplicated, fall back to normalized path.
- If mapping remains ambiguous, return `accepted=False` and leave the card in `needs_review`.
- The returned result should explain unmapped planned IDs and candidate real assets.

### P0: Make `review_run()` reflect final review truth

After `_finalize_run_review()` returns:

- return `result["accepted"]`, not the original `accept` argument;
- append `manager_review` event text based on `result["accepted"]`;
- commit the reviewed git stage only when the final review outcome is actually reviewed/accepted or explicitly rejected;
- if the run is `success` + card `needs_review`, commit or label it as "execution succeeded, review pending" rather than "reviewed".

This keeps API response, event stream, git stage label, run status, card status, and asset statuses aligned.

### P0: Make reviewer auto-accept reflect final review truth

The reviewer auto-accept path has the same failure mode as manual `review_run()`.

Current path:

```python
result = self._finalize_run_review(project_id, run_id, accept=True, source="reviewer")
```

Required behavior after `_finalize_run_review()` returns:

- use the returned `result`;
- if `result["accepted"]` is true:
  - append `reviewer_acceptance`;
  - commit the reviewed git stage;
  - enqueue `card_run_reviewed`;
  - use acceptance wording such as `"Reviewer 已验收并接受运行结果。"` only in this branch.
- if `result["accepted"]` is false because mapping was downgraded to `needs_review`:
  - append a reviewer/manual-review-required event, not `reviewer_acceptance`;
  - do not enqueue `card_run_reviewed`;
  - do not commit the stage as `"reviewed"`;
  - leave the run/card in the persisted state chosen by `_finalize_run_review()` (`run.status == "success"`, `card.status == "needs_review"` for mapping ambiguity);
  - enqueue only a terminal/blocking wake kind that accurately describes the state, if auto mode needs to continue.

This mirrors the manual `review_run()` fix and prevents reviewer auto-accept from claiming success after `_finalize_run_review()` has downgraded acceptance.

### P0: Reuse graph consistency diagnostics

Use `_assert_acceptance_graph_consistent()` as the single source of truth for accepted review paths first.

After the review path is fixed, reuse the same diagnostic for other mutating operations that can produce accepted cards:

- reviewer auto-accept path;
- manual `review_run`;
- `update_card` when status becomes accepted.

For review acceptance paths, fail hard. For ordinary `update_card`, return warnings unless the tool is explicitly claiming final acceptance.

### P0: Guard `update_card(status="accepted")`

`update_card` can currently merge arbitrary card payloads and persist `status="accepted"` without going through run review.

Use a targeted guard in the manager tool write path:

- after normalizing and validating the candidate card;
- before saving it;
- if the resulting card status is `accepted`;
- call the same acceptance consistency helper against the current graph.

If the helper fails:

- reject the update with a `ManagerPlanningError`;
- tell Manager to run/review/rebind outputs instead of directly setting accepted;
- do not persist partial card changes.

Do not put this in a Pydantic `Card` model validator. The validator cannot reliably inspect `graph.assets`, and it would also run when loading historical/intermediate states that are intentionally not accepted-consistent.

### P1: Suppress inactive-card attention from default user-facing summaries

Update `DependencyAttentionService` itself so default analysis ignores inactive cards:

- `cancelled`
- `rejected`
- `superseded`

Recommended implementation:

```python
INACTIVE_CARD_STATUSES = {"cancelled", "rejected", "superseded"}

def _analyze_card_inputs(...):
    if card.status in self.INACTIVE_CARD_STATUSES:
        return
```

`_analyze_card_outputs()` already returns unless `card.status == "accepted"`, so the missing gap is input analysis.

Rationale:

- Inactive cards are historical or abandoned branches.
- Their stale inputs are expected and should not inflate the active project ATTENTION count.
- `analyze_project()` should be clean by default so all callers get the same user-facing behavior without remembering to filter.
- Full diagnostics can later expose inactive-card issues behind an explicit `include_inactive=true` or debug path.

This specifically removes OAA noise from cancelled `rna_immune` and `rna_enrich_viz_v2` without weakening checks on accepted cards.

Do not use this as a workaround for accepted cards with candidate outputs. Those must remain visible.

Important boundary:

- Skip analysis when the current card being analyzed is inactive.
- Do not suppress issues for an active card that consumes an asset produced by an inactive upstream card.
- `input_producer_card_inactive` from `docs/23_dependency_attention_derived_diagnostics_plan.md` remains valid for active downstream cards.

### P1: Add Manager guidance for needs_review output binding

When a run lands in `needs_review` because output mapping is ambiguous, Manager should not manually claim success after a blind `update_card`.

Recommended tool flow:

1. `get_card_detail`
2. `find_assets` scoped by `producer_card_id` or run ID
3. explicit `update_card` outputs to real assets
4. `review_card_run`
5. `inspect_dependency_attention`

If `review_card_run` returns `accepted=False`, Manager must report the unresolved mapping instead of saying the card is accepted.

### P1: Gate running-time manager wakes behind terminal state

Change handling for `BP_EVENT issue_report` with `needs_manager=true`:

- Keep writing `executor_issue` and `run_blocked_on_manager` run events.
- Keep setting `needs_manager_attention` and progress text.
- Do not enqueue `card_needs_manager` while the subprocess is still running.
- For v1, do not implement paused/blocking run wake. The run model has no paused status today.
- Defer Manager wake until post-process handling persists a terminal run state such as `failed`, `success`, `cancelled`, or `reviewed`.

For v1, the simplest rule is:

```text
Only terminal/blocking post-process states enqueue auto wake events.
No auto wake from stdout pump while process.poll() is None.
```

This preserves the performance benefit of streaming progress and early issue capture without allowing Manager to mutate the graph while outputs are still being produced.

This rule intentionally overrides the older `docs/18_manager_auto_mode_wake_hook_plan.md` wording that allowed `BP_EVENT issue_report needs_manager=true` to enqueue `card_needs_manager` directly.

### P1: Clear auto active run for every terminal run wake

Update `ManagerWakeProcessor.clear_active_run` to include all terminal run wake kinds:

- `card_run_reviewed`
- `card_run_failed`
- `card_run_cancelled`
- `manifest_validation_failed`
- `executor_validation_failed`
- `runtime_dependency_missing`
- `run_filesystem_audit_failed`
- `card_needs_manager` only if a future implementation emits it after a terminal run state, not from stdout pump

The safer implementation is to classify by event semantics instead of a hardcoded partial list:

```python
TERMINAL_RUN_WAKE_KINDS = {...}
clear_active_run = wake_event.run_id is not None and wake_event.kind in TERMINAL_RUN_WAKE_KINDS
```

Also add an assertion or test helper that every `WorkerService` terminal wake kind is represented in this set.

### P2: Persist auto mode tool timeline

Make auto wake actions visible in the chat UI.

Preferred design:

- Add a backend streaming collection path for `ManagerWakeProcessor`.
- Call the manager sidecar `/chat-stream` equivalent internally.
- Convert stream events to `ChatSessionMessageTimelineItem`.
- Append one manager message containing:
  - wake notice
  - thinking blocks
  - tool start/end items
  - final response text

Minimal alternative:

- Extend non-streaming `/chat` response metadata to include compact tool events.
- Have `ManagerWakeProcessor._manager_response_message()` convert those tool events into timeline items.

Preferred design is better because it reuses the same event semantics as manual chat.

Testing note: keep detailed Auto UI tests pending until the implementation chooses the streaming collection path or the non-streaming compact-tool-event path.

### P2: Improve project repair visibility

When dependency attention remains after a repair run, Manager should distinguish:

- upstream data still invalid
- output asset still candidate
- output binding still placeholder
- downstream card not rerun yet

This can be done through clearer `inspect_dependency_attention` issue messages and Manager prompt guidance.

## Test Plan

### WorkerService review tests

Add or update tests around `_finalize_run_review()` and `review_run()`:

1. Accepting a successful run promotes created assets from `candidate` to `valid`.
2. Accepted card output contracts are rebound from planned IDs to real `asset_run_*` IDs.
3. Manifest outputs with no `asset_id` map by unique role.
4. Duplicate `run_summary` / `run_preview` expected outputs do not break mapping.
5. Re-reviewing an already reviewed run does not demote valid assets.
6. `_materialize_run_assets()` upsert preserves an existing `valid` status when a reviewed run is inspected/re-reviewed.
7. If mapping fails, API returns `accepted=False` and card becomes `needs_review`.
8. If mapping fails, `review_run()` returns the final `accepted=False`, not the original requested accept flag.
9. If mapping fails, `review_run()` appends a manager review event whose message reflects final `accepted=False`; it must not say `"Manager 已接受运行结果。"`.
10. If mapping fails, the run is persisted as `success` with card `needs_review`, not as fully accepted/reviewed success.
11. If mapping fails, no "reviewed/accepted" wake event or success wording is emitted.
12. Reviewer validation starts only after the executor subprocess has exited and manifest validation has passed.
13. Reviewer auto-accept path uses `_finalize_run_review()` result. If the result is `accepted=False`, it does not append `"Reviewer 已验收并接受运行结果"` and does not enqueue `card_run_reviewed`.
14. Accepting a run clears `run.needs_manager_attention`.

### DependencyAttentionService tests

1. Default `DependencyAttentionService.analyze_project()` excludes inactive-card input issues by short-circuiting inactive cards in `_analyze_card_inputs`.
2. An active card that consumes an asset produced by an inactive upstream card still reports `input_producer_card_inactive`.
3. Accepted card output pointing to candidate asset still reports `output_asset_not_valid`.

### Wake and auto-state tests

1. `BP_EVENT issue_report needs_manager=true` during stdout pump does not enqueue an auto wake while the process is still running.
2. If a run later fails due to that issue, exactly one terminal wake is enqueued for the run outcome.
3. `runtime_dependency_missing` and `run_filesystem_audit_failed` clear `manager_auto.active_run_id`.

Concrete running-time wake assertion:

1. Arrange a project with an active run whose status is `running`.
2. Call `_handle_structured_executor_event()` with a `BP_EVENT` `issue_report` payload where `needs_manager=true`.
3. Assert the run event log includes `executor_issue` and `run_blocked_on_manager`.
4. Assert `chat/manager_wake_events.jsonl` does not contain a `card_needs_manager` event for that run.
5. If a future implementation deliberately allows that wake, assert the wake carries the correct `run_id` and that the persisted run status is no longer `running` before the wake is enqueued.

### Integration tests

Create a behavior test that mirrors the OAA chain:

1. Root card reruns with a new upload.
2. Step 2 cards rerun and are accepted.
3. Step 3 cards consume Step 2 outputs and rerun.
4. `inspect_dependency_attention` returns no issues for repaired Step 1/2/3 cards.
5. If a card is accepted with candidate outputs, `inspect_dependency_attention` reports `output_asset_not_valid`.
6. Cancelled historical branches do not inflate the default work-order `dependency_attention_count`.
7. `/api/projects/{project_id}/work-order` and card detail return consistent attention counts for active cards.
8. Auto mode does not start a second Manager turn for a running executor before the terminal run event.
9. A terminal run event following an early issue report does not cause stale dependency repair decisions from partial run state.

Tests 8-9 require controlled timing. Use a mock executor process, a stubbed stdout pump, or direct structured-event injection rather than relying on wall-clock sleeps.

### Manager tool tests

Add manager flow tests:

1. `review_card_run` on an already reviewed run is idempotent.
2. Manager cannot successfully claim acceptance if output mapping failed.
3. `update_card` replacing placeholder outputs with real assets actually persists `card.outputs[].asset_id`.
4. `update_card(status="accepted")` rejects a card whose outputs point to missing/candidate/nonvalid assets.
5. After output rebinding, `inspect_dependency_attention` clears for that card if real assets are valid.

### Auto UI tests

Add a focused test for `ManagerWakeProcessor`:

Detailed tests depend on the chosen P2 implementation path.

Baseline tests:

1. Wake notice and final manager response remain visible.
2. Backend-appended auto messages do not overwrite user-visible session history.

If using the streaming collection path:

1. Given a wake event and streamed tool events, the appended chat session message includes tool timeline items.
2. Manual chat and auto chat render the same timeline item schema.

If using the non-streaming compact-tool-event path:

1. Compact tool metadata is converted into timeline items with stable IDs.
2. Missing compact tool metadata falls back to text/thinking-only messages without breaking the session.

## Manual OAA Recovery Checklist

Before declaring the OAA flow fixed, verify:

```text
rna_prep outputs -> real assets are valid
rna_pca outputs -> real assets are valid
rna_deg outputs -> real assets are valid
rna_enrich outputs -> real assets are valid
rna_tf outputs -> real assets are valid
rna_html_report either running or accepted with real valid outputs
inspect_dependency_attention for repaired cards returns zero issues
```

Also verify that the latest `rna_html_report` inputs point to the latest valid assets, not older runs.

## Non-Goals

- Do not make dependency attention persistent.
- Do not reintroduce chain-propagated stale status.
- Do not use `linked_assets` as current dependency truth.
- Do not hide candidate outputs by changing the attention service.
- Do not treat candidate output assets from accepted cards as normal.
