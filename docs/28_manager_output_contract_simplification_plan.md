# Manager Output Contract Simplification Plan

> Status note: this document records the output-contract simplification work.
> The later tool-surface decisions in
> `docs/29_manager_run_control_selector_contract.md` supersede this document
> wherever it refers to a broad `update_card` tool. The current target split is:
> `revise_card_plan` for execution-relevant `step` / `inputs` / `outputs`
> changes, and `annotate_card` for display-only `title` / `summary` /
> note changes.

## Context

The OAA-2 KEGG/GO enrichment run exposed a contract-design problem:

- The executor correctly produced tabular enrichment results.
- The manifest validator correctly detected CSV/XLSX-like outputs as `table`.
- The card contract had previously declared those outputs as `document`.

The manifest check was not too strict. It was the first place where an upstream output contract modeling mistake became visible.

The deeper issue is that Manager currently has too much freedom when writing output contracts. It can choose both `artifact_class` and file-level format hints. File-level format selection should not be a Manager responsibility.

## Decision

Manager-facing output contracts should expose semantic artifact classes:

- `document`
- `table`
- `figure`
- `model`
- `archive`
- `binary`

Manager should not choose low-level file formats for ordinary card outputs.

The backend should derive accepted formats and default path extensions from `artifact_class`.

## Target Contract Model

### Manager-Facing Contract

Manager tools should accept this shape:

```json
{
  "role": "kegg_enrich",
  "artifact_class": "table",
  "description": "Pathway enrichment result table."
}
```

Manager does not provide:

- `accepted_formats`
- `preferred_format`

Those fields are system-generated from `artifact_class`.

### Backend-Derived Formats

The backend derives runtime formats:

| artifact_class | runtime formats | default extension |
| --- | --- | --- |
| `table` | `csv`, `tsv`, `xlsx` | `csv` |
| `figure` | `png`, `svg`, `pdf` | `png` |
| `document` | `md`, `html`, `txt` | `md` |
| `model` | `pkl`, `joblib`, `rds`, `pt`, `onnx` | `pkl` |
| `archive` | `zip`, `tar.gz` | `zip` |
| `binary` | no strict extension preference | `bin` |

The default extension is only for generated `path_hint`. It is not Manager intent.

## Scope Boundary

### Exposed To Manager

- `document`
- `table`
- `figure`
- `model`
- `archive`
- `binary`

The distinction Manager owns is semantic, not file-extension-level.

Recommended meanings:

- `table`: analytical tables, matrices, enrichment results, score tables.
- `figure`: plots, diagrams, visual summaries.
- `document`: reports, narratives, summaries, Markdown/HTML text deliverables.
- `model`: trained model artifacts or serialized model objects.
- `archive`: bundled result directories or multi-file deliverables.
- `binary`: last-resort opaque artifact when no more specific class applies.

## Validation Rules

### Card Create/Update

When `create_card` or `update_card` receives outputs:

1. Require `role` and `artifact_class`.
2. Accept only the supported semantic artifact classes.
3. Derive runtime formats and default format metadata from `artifact_class`.

There is no contradictory-contract state in the normal write path because Manager does not own format fields.

### Task Packet Generation

When a run is created, task packet generation:

1. Derive runtime accepted formats from each output's `artifact_class`.
2. Generate `path_hint` from the derived default extension.
3. Build `expected_outputs` from the normalized runtime contract.

This is normal task-packet construction, not a separate preflight contract check. Run start should not ask Manager to provide or repair file extensions.

### Manifest Validation

Manifest validation should remain strict:

- Output role must be declared.
- File must exist under allowed paths.
- Detected artifact class must match the expected semantic class.
- Detected format should be one of the backend-derived runtime formats.

This strictness catches real contract drift and executor mistakes.

## ATTENTION Behavior

ATTENTION should focus on real graph state, not impossible contract combinations.

Relevant cases:

- accepted card output points to a missing, candidate, rejected, or archived asset;
- downstream card still references an old input asset;
- asset lineage includes invalid upstream assets;
- a previous failed/candidate run produced files that were not promoted.

If a previous failed/candidate run produced files, the candidate asset or manifest path should be shown as evidence, but not automatically promoted. Manager can decide whether to rerun or inspect the candidate file.

## What Should Be Automated

Safe automation:

- Derive runtime formats from `artifact_class`.
- Generate `path_hint` extension from `artifact_class`.
- Keep manifest validation strict after execution.

Not safe to automate:

- Changing business semantics such as whether an output should be a report or a result table.
- Promoting candidate outputs after a contract change.
- Rebinding downstream inputs to old files without explicit dependency repair logic.
- Guessing a specific file extension from business semantics.

## Manager Prompt Changes

Manager prompt and tool descriptions should say:

- For `outputs[]`, choose one semantic artifact class: `document`, `table`, `figure`, `model`, `archive`, or `binary`.
- Do not provide file extensions or format lists.
- Use `table` for CSV/XLSX/TSV-like analytical results such as DEG tables, KEGG enrichment results, GO enrichment results, score matrices, module assignments, or feature rankings.
- Use `figure` for plots and visualizations.
- Use `document` for reports, narratives, summaries, and HTML/Markdown deliverables.
- Use `model` for trained model files or serialized model objects.
- Use `archive` for bundled multi-file deliverables.
- Use `binary` only as a fallback for opaque artifacts.

## Manager Card Tool Field Contract

The Manager card-writing tools should expose a narrow, semantic input surface. Fields that are derived by the graph, executor, reviewer, or UI should not be writable through the normal Manager tool schema.

### Top-Level Card Fields

| field | Manager input? | Meaning | Automation / derivation |
| --- | --- | --- | --- |
| `card_id` | create: no; update: required selector | Stable card identity. | System generates it, for example from timestamp plus a short slug. Manager should not provide it when creating a card. |
| `card_type` | no | Card kind. | System sets `module`. Do not expose this in Manager card-writing tools unless additional card types become real product concepts. |
| `title` | `annotate_card` only | Human-readable card title. | Display-only edit; must not trigger rerun or planned reset. |
| `status` | no | Workflow state. | Create defaults to `planned`. Running/review/rejection/acceptance/stale/cancelled transitions are owned by dedicated tools or system state changes, not card-writing payloads. |
| `step` | `revise_card_plan` only | Timeline/order grouping. | Execution-relevant edit; reset card to `planned` when changed outside transient execution states. |
| `summary` | `annotate_card` only | What this card will do or has done. | Display-only edit; must not trigger rerun or planned reset. |
| `why` | no | Deprecated rationale field. | Remove from Manager write surface because it overlaps with `summary`. Existing stored values can remain read-only until migrated away. |
| `inputs` | `revise_card_plan` only | Declared asset dependencies. | Manager should choose from selectable assets returned by asset search/detail/ATTENTION, not invent arbitrary input refs. Selectable assets include materialized valid assets and planned upstream outputs. Changing inputs resets the card to `planned`. |
| `outputs` | `revise_card_plan` only | Declared semantic outputs. | Backend derives runtime formats and `path_hint`. Changing outputs resets the card to `planned`. |
| `key_findings` | no | Short conclusions from completed work. | Populate from executor/reviewer/result-summary flow after execution. Do not expose in Manager card-writing tools. |
| `manager_review` | `annotate_card` only | Display note / assessment text. | Must not finalize a run, change assets, or change card status. Review finalization remains owned by reviewer/run review paths. |
| `next_actions` | no | Deprecated follow-up text field. | Remove from Manager write surface; use normal chat response or future task-specific structures instead. |
| `linked_modules` | no | Deprecated module grouping reference. | Remove from Manager write surface. If UI/template compatibility still needs it, maintain it outside normal card-writing tools. |
| `linked_runs` | no | Run history references. | System-maintained read-only field. Do not expose in Manager write tools. |
| `linked_assets` | no | Deprecated asset link field. | Remove from Manager write surface and delete from the active card contract. Dependency truth lives in `inputs`, `outputs`, graph assets, and runs. |
| `progress_note` | no | Deprecated short status note. | Remove from Manager write surface; live status should come from card/run state, events, ATTENTION, and chat messages. |
| `executor_context` | no | Execution policy/runtime details. | Do not expose in card-writing tools. Populate from executor/runtime configuration and dedicated execution-configuration tools. |
| `aggregate_status` | no | Group rollup state. | System-derived from child/card state. Do not expose in Manager tools. |
| `technical_refs` | no | Internal graph/patch references. | System-owned audit/debug metadata. Do not expose in Manager tools. |

### Input Object

Manager-facing `inputs[]` should become asset selection, not free-form object writing.

A selectable input asset can be either:

- a materialized valid asset already present in `graph.assets`;
- a planned upstream output declared by another card's `outputs[].asset_id`.

This is necessary for planning a DAG before every upstream run has completed.

Preferred create/update shape:

```json
[
  "asset_run_xxx_deg_table_1"
]
```

| field | Manager input? | Meaning | Automation / derivation |
| --- | --- | --- | --- |
| `asset_id` | yes, selected from known assets | Exact upstream asset id or planned upstream output id. | Manager should obtain it from `find_assets`, card detail, or ATTENTION `current_asset_id`. Backend should not require the asset to already be materialized; dependency ATTENTION reports missing/candidate/outdated state later. |
| `label` | no | Human-readable dependency name. | Derive from selected asset role/title/path/producer card. Do not expose in Manager schema. |
| `status` | no | Snapshot/status hint. | Derived from graph asset status. Do not ask Manager to write it. |

Input repair rule:

- If ATTENTION says `input_asset_outdated`, revise `inputs[].asset_id` first, then start the card normally.
- If the intended replacement is ambiguous, report ATTENTION instead of guessing.

### Output Object

Manager-facing `outputs[]` should be:

```json
{
  "role": "kegg_enrich",
  "artifact_class": "table",
  "description": "Pathway enrichment result table."
}
```

| field | Manager input? | Meaning | Automation / derivation |
| --- | --- | --- | --- |
| `role` | yes | Stable machine role for manifest matching and downstream references. | Backend normalizes to snake_case. |
| `label` | no | Human-readable output display name. | Generate from `role` with simple casing/separator cleanup and duplicate suffixing. Do not expose in Manager schema. |
| `artifact_class` | yes | Semantic output class. | Must be one of `document/table/figure/model/archive/binary`. |
| `required` | no | Whether missing this output should fail manifest validation. | Always system-set to `true` for card-declared outputs. Do not expose in Manager schema. Optional/supporting files should not be declared as card outputs. |
| `description` | optional | Clarifies what the executor should produce. | Passed into task packet. |
| `asset_id` | no | Planned output asset id for downstream wiring. | System generates from card identity and role, and preserves it across updates when the role is unchanged. Do not expose in Manager schema. |
| `status` | no in normal planning | Planned/valid/candidate state. | Derived from run/review/asset state. |
| `accepted_formats` | no | Runtime file formats accepted for this class. | System-derived from `artifact_class`. Do not expose in Manager schema. |
| `preferred_format` | no | Default extension preference. | System-derived from `artifact_class`. Do not expose in Manager schema. |

Output class rule:

- `table`: analytical result tables and matrices.
- `figure`: plots and visual summaries.
- `document`: reports and narrative summaries.
- `model`: trained/serialized model artifacts.
- `archive`: bundled multi-file deliverables.
- `binary`: opaque fallback when no more specific class applies.

Output label display rule:

- Start from `role`.
- Replace `_`/`-` with spaces.
- Apply simple title casing or uppercase normalization consistently.
- If labels would repeat inside the same card, append a short numeric suffix.
- Do not use LLM rewriting, domain abbreviation dictionaries, timestamps, run ids, or file extensions for labels.

### Refined Tool Schema Shape

`create_card` should not ask Manager for `card_id`, `card_type`, or `why`.

Initial planning should also not expose `key_findings`, `manager_review`, `next_actions`, `linked_modules`, `linked_runs`, `linked_assets`, `progress_note`, `executor_context`, `aggregate_status`, or `technical_refs`.

Final manager-facing create shape:

```ts
const CreateCardInput = Type.Object({
  title: Type.String(),
  summary: Type.String(),
  step: Type.Optional(Type.Number()),
  inputs: Type.Optional(Type.Array(ManagerCardInput)),
  outputs: Type.Optional(Type.Array(ManagerCardOutput)),
});
```

Superseded broad update shape:

```ts
const UpdateCardInput = Type.Object({
  card_id: Type.String(),
  title: Type.Optional(Type.String()),
  summary: Type.Optional(Type.String()),
  step: Type.Optional(Type.Number()),
  inputs: Type.Optional(Type.Array(ManagerCardInput)),
  outputs: Type.Optional(Type.Array(ManagerCardOutput)),
});
```

Current target split:

```ts
const ReviseCardPlanInput = Type.Object({
  card_id: Type.String(),
  step: Type.Optional(Type.Number()),
  inputs: Type.Optional(Type.Array(ManagerCardInput)),
  outputs: Type.Optional(Type.Array(ManagerCardOutput)),
});

const AnnotateCardInput = Type.Object({
  card_id: Type.String(),
  title: Type.Optional(Type.String()),
  summary: Type.Optional(Type.String()),
  note: Type.Optional(Type.String()),
});
```

For plan revision, `card_id` is a selector. Display wording fixes belong to `annotate_card`, not the execution-plan revision tool.

`create_card.outputs` and `update_card.outputs` should not be `Record<string, any>`. They should be explicit arrays of output objects:

```ts
const ArtifactClass = Type.Union([
  Type.Literal("document"),
  Type.Literal("table"),
  Type.Literal("figure"),
  Type.Literal("model"),
  Type.Literal("archive"),
  Type.Literal("binary"),
]);

const ManagerCardInput = Type.Object({
  asset_id: Type.String(),
});

const ManagerCardOutput = Type.Object({
  role: Type.String(),
  artifact_class: ArtifactClass,
  description: Type.Optional(Type.String()),
});
```

The schema should not contain `accepted_formats`, `preferred_format`, `status`, `aggregate_status`, `technical_refs`, or arbitrary extra output keys.

### Remove `plan_card_write`

The standalone `plan_card_write` manager tool should be removed.

Its validation responsibilities should move inside `create_card` and `update_card`:

- normalize the narrow manager-facing payload;
- generate missing system fields such as `card_id`, output `asset_id`, output labels, output runtime formats, and default statuses;
- validate selected input assets against materialized assets and planned upstream outputs;
- assign or preserve output asset ids;
- check duplicate output roles and duplicate output asset ids;
- check dependency cycles;
- compute or validate `step` where needed;
- reject invalid writes before saving.

This keeps one write path. Manager should not choose between a dry-run tool and a real write tool.

### Structured Validation Errors

`create_card` and `update_card` should return actionable structured errors when validation fails. Do not return only `ok: false` plus free-form strings.

Recommended shape:

```json
{
  "ok": false,
  "error_type": "card_write_validation_failed",
  "action": "create",
  "errors": [
    {
      "code": "input_asset_not_selectable",
      "field": "inputs[0].asset_id",
      "asset_id": "deg02_deg_table",
      "message": "Input asset is not a known materialized asset or planned upstream output.",
      "blocking": true,
      "repair": {
        "tool": "find_assets",
        "query": "deg02 deg table",
        "allowed_next_actions": ["choose_existing_asset", "create_upstream_card"]
      }
    }
  ]
}
```

Initial error codes:

- `empty_title`
- `empty_summary`
- `input_asset_not_selectable`
- `output_role_empty`
- `output_role_duplicate`
- `invalid_artifact_class`
- `duplicate_output_asset_id`
- `dependency_cycle`
- `update_card_not_found`
- `accepted_card_consistency_failed`

Error objects should include concrete ids where possible:

- `card_id`
- `asset_id`
- `role`
- `field`
- `cycle_card_ids`

Repair hints should name the next useful tool when clear:

- `find_assets` for unresolved input assets;
- `inspect_dependency_attention` for dependency repair;
- `get_card_detail` before precise update edits;
- `create_card` when the missing input should be modeled as a planned upstream output.

Validation failure must not write graph state.

## Implementation Plan

### P0: Backend Normalization

Add a central helper, for example `OutputContractPolicy`, that:

- exposes the Manager-allowed artifact classes,
- maps classes to default formats,
- generates runtime formats,
- generates default extensions for `path_hint`.

Use it from:

- `ManagerBlueprintTools._normalize_card_payload`
- any direct patch/card creation path that writes `CardOutputSpec`
- worker run-start task packet generation

### P0: Manager Tool Surface

Update Manager tool schemas/descriptions:

- constrain `artifact_class` to the supported semantic class enum,
- remove `accepted_formats`,
- remove `preferred_format`,
- add bioinformatics examples for enrichment tables and plots.

### P1: Tests

Add tests for:

- `table` outputs get default `csv/tsv/xlsx`.
- `figure` outputs get default `png/svg/pdf`.
- `document` outputs get default `md/html/txt`.
- `model`, `archive`, and `binary` outputs get system-derived runtime formats.
- Manager tool schemas do not expose `accepted_formats` or `preferred_format`.
- `kegg_enrich` and `go_enrich` examples use `table`.
- task packet generation derives expected output formats from `artifact_class`.

### P2: Legacy Migration

Add a narrow migration or repair utility for existing cards:

- fill missing runtime format metadata from class,
- keep existing `artifact_class` unchanged,
- do not infer or rewrite business semantics.

## Expected Outcome

Manager decides only the semantic output type.

The system owns file-format defaults and path generation.

Contract format fields are system-owned, so Manager cannot create file-format contradictions while writing cards.
