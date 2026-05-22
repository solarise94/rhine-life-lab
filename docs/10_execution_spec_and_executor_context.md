# ExecutionSpec and Executor Context Plan

## Problem

Executor agents currently receive a generated prompt plus `task_packet.json` and `adapter_contract.json`.
This is better than sending the full project blueprint, but `task_packet.json` is still an internal backend contract. It mixes execution intent with audit policy, manifest validation details, paths, runtime bindings, and reviewer-facing structure.

For LLM agents this can waste thinking time:

- the agent reads backend protocol instead of a concise task brief;
- manifest and sandbox details compete with the actual analysis objective;
- the agent may spend time reasoning about platform internals;
- future reusable scripts and runtime resolver results need a cleaner executor-facing surface.

The goal is to keep backend validation strict while making the agent's first input small and action-oriented.

## Current State

For each run, the backend writes:

- `runs/{run_id}/task_packet.json`: full internal task packet used by backend validation, reviewer, wrappers, and audit.
- `runs/{run_id}/executor_prompt.md`: generated prompt that expands selected task packet details.
- `runs/{run_id}/executor_brief.md`: human-readable task summary.
- `runs/{run_id}/adapter_contract.json`: manifest schema, path contract, allowed tools, and adapter metadata.
- `runs/{run_id}/report_dependency_issue.py`: local helper for reporting missing runtime dependencies.
- `runs/{run_id}/dependency_issue.json`: created by the helper when required dependencies are missing.

The executor does not receive the full card list or full graph by default. It receives current-card inputs, expected outputs, allowed paths, forbidden paths, runtime context, and reporting contract.

## Proposed Layering

Introduce `execution_spec.json` as the executor's primary entry point:

```text
execution_spec.json      Agent-facing concise task instructions.
task_packet.json         Backend-facing complete contract and audit input.
adapter_contract.json    Exact manifest/schema/path contract.
```

The executor prompt should become short:

```text
Read execution_spec.json first.
Use task_packet.json only when execution_spec is insufficient.
Use adapter_contract.json for exact manifest/schema details.
Do not inspect graph/ or unrelated project state.
Do not install packages; report missing dependencies with report_dependency_issue.py.
```

## ExecutionSpec Shape

`execution_spec.json` should contain only the information needed to start work:

```json
{
  "schema_version": "execution_spec.v1",
  "run_id": "run_xxx",
  "project_id": "oaa",
  "card": {
    "id": "card_pca",
    "title": "PCA 主成分分析",
    "objective": "对 bulk RNA count matrix 做 PCA，输出 PCA 图和摘要"
  },
  "inputs": [
    {
      "role": "count_matrix",
      "asset_id": "counts_v1",
      "path": "data/counts.tsv",
      "format": "tsv",
      "required": true
    }
  ],
  "outputs": [
    {
      "role": "run_preview",
      "path": "results/card_pca/run_xxx/run_preview.svg",
      "format": "svg"
    },
    {
      "role": "run_summary",
      "path": "results/card_pca/run_xxx/run_summary.md",
      "format": "markdown"
    }
  ],
  "runtime": {
    "python_runtime": "omicverse",
    "dependency_policy": "report_missing_do_not_install",
    "dependency_tool": "runs/run_xxx/report_dependency_issue.py"
  },
  "recommended_methods": [
    "Use DESeq2 varianceStabilizingTransformation when available.",
    "Use prcomp for PCA.",
    "Color points by sample group when metadata provides a group column."
  ],
  "reusable_scripts": [
    {
      "asset_id": "script_bulk_rna_pca_v1",
      "path": "scripts/library/bulk_rna_pca_v1.R",
      "language": "R",
      "entrypoint": "run_pca",
      "requirements": ["DESeq2", "ggplot2"]
    }
  ],
  "constraints": [
    "Write run-specific code under scripts/generated/run_xxx/.",
    "Write outputs only to declared output paths.",
    "Do not modify graph/, .git/, input assets, or reusable script assets."
  ],
  "fallback_contracts": {
    "task_packet": "runs/run_xxx/task_packet.json",
    "adapter_contract": "runs/run_xxx/adapter_contract.json",
    "manifest_candidate": "runs/run_xxx/manifest.candidate.json"
  }
}
```

## TaskPacket Role

`task_packet.json` remains the source of truth for backend and reviewer logic. It should continue to contain:

- exact input asset metadata;
- expected outputs;
- allowed, readonly, and forbidden paths;
- execution policy;
- runtime bindings;
- reporting contract;
- audit and validation data.

The agent should read it only for exact details that are absent from `execution_spec.json`.

## Script Assets

Run-generated code and reusable scripts should be separated:

- `scripts/generated/{run_id}/...`: run-local code artifacts for audit and reproducibility.
- `scripts/library/...`: reusable script assets that may be referenced by future cards.

Executors should not directly overwrite `scripts/library/`. A generated script can be promoted later through review into a reusable script asset.

Reusable script assets should declare:

- language;
- entrypoint;
- expected input schema;
- expected output schema;
- runtime requirements;
- version/hash;
- intended card/task types.

`ExecutionSpec` can reference these script assets concisely, while `task_packet.json` and the asset graph retain the full metadata.

## Dependency Reporting

Missing runtime dependencies are not execution failures to hide or solve ad hoc. They are card-level blockers.

The executor should call:

```bash
python runs/{run_id}/report_dependency_issue.py \
  --ecosystem R \
  --package clusterProfiler \
  --package enrichplot \
  --manager Bioconductor \
  --message "Required enrichment packages are unavailable."
```

The helper writes:

- `runs/{run_id}/dependency_issue.json`;
- `runs/{run_id}/manager_brief.json`;
- `BP_EVENT issue_report` on stdout.

Blocking dependency issues use exit code `3`. The backend treats the run as failed with manager attention and records a `runtime_dependency_missing` event.

## Migration Plan

1. Add an `ExecutionSpec` model and schema.
2. Generate `execution_spec.json` from `TaskPacket`.
3. Shorten `executor_prompt.md` so it points to `execution_spec.json` instead of expanding the full packet.
4. Update agent wrappers to prefer `execution_spec.json`.
5. Keep `task_packet.json` unchanged for validators, reviewer, audit, and backward compatibility.
6. Add reusable script asset references to `ExecutionSpec` after script asset promotion is implemented.

