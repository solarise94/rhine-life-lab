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

## Runtime Profiles and OmicVerse MCP

The current UI calls the selector `Python runtime`, but the long-term backend concept should be `RuntimeProfile`.
A plain Python runtime is only an interpreter and package environment. An OmicVerse runtime is a domain execution profile: Python plus an omics dependency stack, tutorials/recipes, and optional MCP tools.

Runtime profile fields should eventually include:

```json
{
  "id": "omicverse",
  "label": "OmicVerse v2 (miniforge)",
  "conda_prefix": "/home/solarise/miniforge3/envs/omicverse",
  "python": "/home/solarise/miniforge3/envs/omicverse/bin/python",
  "rscript": "/home/solarise/miniforge3/envs/omicverse/bin/Rscript",
  "capabilities": ["python", "omics", "single_cell", "spatial", "mcp"],
  "dependency_policy": "report_missing_do_not_install",
  "mcp_servers": {
    "omicverse": {
      "command": "/home/solarise/miniforge3/envs/omicverse/bin/python",
      "args": ["-m", "omicverse.mcp", "--phase", "P0"]
    }
  }
}
```

The user-facing selector can remain simple while the backend treats selected environments as profiles:

- `system`: plain system Python, no domain MCP.
- `omicverse`: OmicVerse dependency environment with OmicVerse MCP when available.
- `cell2location`, `scanpy`, or future runtimes: task-specific profiles with their own capabilities.

`ExecutionSpec.runtime` can then tell the executor what capability set it has:

```json
{
  "runtime": {
    "profile": "omicverse",
    "python_runtime": "omicverse",
    "capabilities": ["omics", "mcp"],
    "mcp_servers": ["omicverse"],
    "dependency_policy": "report_missing_do_not_install",
    "dependency_tool": "runs/run_xxx/report_dependency_issue.py"
  }
}
```

### OmicVerse MCP Integration Options

There are two viable integration paths.

Path A: RuntimeProfile-driven MCP exposure.

- Backend detects that the selected runtime has `omicverse.mcp`.
- Backend writes MCP server metadata into `execution_spec.json` and adapter contract.
- Wrappers that support MCP translate that metadata into provider-specific config.
- Non-MCP wrappers still see the runtime profile and can fall back to local Python imports or helper scripts.

Path B: Wrapper-owned MCP injection.

- The executor wrapper receives the selected runtime and directly creates the MCP config expected by its agent CLI.
- For example, a Pi/Claude/Codex wrapper can write a run-local MCP config pointing at:

```json
{
  "mcpServers": {
    "omicverse": {
      "command": "/home/solarise/miniforge3/envs/omicverse/bin/python",
      "args": ["-m", "omicverse.mcp", "--phase", "P0"]
    }
  }
}
```

- The wrapper then launches the agent with the relevant MCP config argument or environment variable.
- This can be faster to implement because it does not require all backend models to understand MCP up front.

Both paths should keep the same safety rules:

- MCP tools must not install packages or modify global conda environments.
- MCP tools must not mutate `graph/`, `.git/`, or upstream input assets.
- Outputs still go through `results/{card_id}/{run_id}/`.
- Code evidence still goes through `scripts/generated/{run_id}/`.
- Final acceptance still depends on manifest validation and reviewer checks.
- Missing MCP support or missing packages should be reported through `report_dependency_issue.py`.

The recommended near-term implementation is Path B for speed: add OmicVerse MCP config generation inside the agent wrapper when the selected runtime profile is `omicverse`. The recommended long-term implementation is Path A so `ExecutionSpec` becomes the common protocol across all wrappers.

## Wrapper Optimizer

The executor layer should be treated as a wrapper optimizer, not as the analysis agent itself.
The real analysis agent can be Pi CLI, Codex, Claude Code, OpenCode, or another external runtime. Blueprint's executor/card layer should prepare the best possible working conditions for that agent.

```text
Blueprint card
  -> wrapper optimizer
  -> pi/codex/claude/opencode
```

The wrapper optimizer owns:

- prompt compilation;
- execution spec generation;
- runtime profile selection;
- skill bundle selection;
- local tool provisioning;
- MCP server injection;
- context compression;
- dependency reporting helpers;
- filesystem guard setup;
- manifest and audit contract setup.

The wrapper optimizer should not replace the agent's reasoning. It should reduce avoidable reasoning overhead by giving the agent:

- a concise task goal;
- exact inputs and outputs;
- the selected runtime profile;
- relevant domain skills;
- reusable scripts and method hints;
- available MCP servers;
- local reporting and manifest tools;
- clear write boundaries.

The final wrapper output can be represented as a `WrapperPlan`:

```json
{
  "provider": "pi",
  "prompt": "runs/run_xxx/executor_prompt.md",
  "execution_spec": "runs/run_xxx/execution_spec.json",
  "env": {
    "BLUEPRINT_DEPENDENCY_REPORT_TOOL": "runs/run_xxx/report_dependency_issue.py",
    "BLUEPRINT_MANIFEST_CANDIDATE_PATH": "runs/run_xxx/manifest.candidate.json"
  },
  "skills": ["bulk_rna", "pca", "omicverse_usage"],
  "mcp": {
    "omicverse": {
      "command": "/home/solarise/miniforge3/envs/omicverse/bin/python",
      "args": ["-m", "omicverse.mcp", "--phase", "P0"]
    }
  },
  "guard": {
    "mode": "bwrap_filesystem_guard",
    "writable": [
      "runs/run_xxx",
      "results/card_pca/run_xxx",
      "scripts/generated/run_xxx"
    ]
  }
}
```

## Filesystem Guard

The existing soft sandbox should not be removed when agent CLI permissions or MCP support improve. Its role should be narrowed and renamed mentally from "sandbox" to "filesystem guard".

Agent CLI permission layers and Blueprint filesystem guard solve different problems:

```text
Agent CLI permission sandbox:
  controls tool-call permission and user confirmation inside a specific CLI.

Blueprint filesystem guard:
  controls where the process can actually write, regardless of how the write is triggered.
```

Reasons to keep the filesystem guard:

- different CLIs have different permission semantics;
- Python/R subprocesses and libraries can write files without going through an agent tool abstraction;
- MCP tools may have their own file behavior;
- Reviewer and audit need one backend-owned write boundary;
- parallel card execution needs run/result/script isolation;
- `graph/`, `.git/`, upstream inputs, and other run directories must stay protected even if the agent prompt is ignored.

The intended guard layer is not strong tenant isolation. It is a project write-boundary and audit consistency layer:

- host root can be read-only for runtime compatibility;
- current `runs/{run_id}/` is writable;
- current `results/{card_id}/{run_id}/` is writable;
- current `scripts/generated/{run_id}/` is writable;
- `graph/` and `.git/` are masked or read-protected;
- unrelated run directories are hidden when practical;
- environment variables are cleared and then explicitly reintroduced.

The recommended default remains:

```json
{
  "guard": {
    "mode": "bwrap_filesystem_guard",
    "host_root": "readonly",
    "network": "host",
    "clearenv": true,
    "writable": [
      "runs/{run_id}/",
      "results/{card_id}/{run_id}/",
      "scripts/generated/{run_id}/"
    ],
    "masked": ["graph/", ".git/"]
  }
}
```

Agent CLI sandboxing can still be used on top of this. The stack should be:

```text
Wrapper optimizer:
  skills, tools, MCP, runtime, prompt, context.

Agent CLI permission layer:
  interactive tool permissions and user confirmation.

Blueprint filesystem guard:
  backend-owned write boundary.

Reviewer and manifest validation:
  result quality and audit acceptance.
```

Only disable the filesystem guard in explicitly degraded development modes, and record that state in `sandbox_plan.json` / future `wrapper_plan.json`.

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

## Script Language Preference

Script language choice is a workflow preference, not a hard executor policy.

The UI should expose a project-level soft preference:

- `auto`: no fixed preference; Manager should ask when Python vs R materially changes the planned card.
- `prefer_python`: prefer Python scripts when practical.
- `prefer_r`: prefer R scripts when practical.
- `prefer_mixed`: choose Python or R per card based on reliability, runtime availability, and reproducibility.

The preference travels through chat context to the Manager. When the Manager creates or updates analysis cards, it should inject the preference into `card.executor_context.instruction_blocks`, for example:

```json
{
  "executor_context": {
    "instruction_blocks": [
      "Soft script preference: prefer R scripts when practical. This is not a hard constraint; use Python when it is more reliable or better supported for this task."
    ]
  }
}
```

The executor receives this as guidance in the task packet and prompt. It may still choose a different language if the selected runtime lacks dependencies, if a curated script/tool exists in the other language, or if the alternative is more reproducible.

## Migration Plan

1. Add an `ExecutionSpec` model and schema.
2. Generate `execution_spec.json` from `TaskPacket`.
3. Shorten `executor_prompt.md` so it points to `execution_spec.json` instead of expanding the full packet.
4. Update agent wrappers to prefer `execution_spec.json`.
5. Keep `task_packet.json` unchanged for validators, reviewer, audit, and backward compatibility.
6. Add reusable script asset references to `ExecutionSpec` after script asset promotion is implemented.
7. Upgrade `python_runtime` into `RuntimeProfile` metadata while keeping the current UI selector simple.
8. Add wrapper-level OmicVerse MCP injection for selected OmicVerse profiles.
9. Move MCP metadata into `ExecutionSpec` once wrapper behavior is stable.
10. Introduce `WrapperPlan` as the provider-specific launch artifact.
11. Keep bwrap as the default filesystem guard while allowing agent CLI permission layers above it.
