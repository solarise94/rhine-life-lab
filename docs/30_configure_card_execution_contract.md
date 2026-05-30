# Configure Card Execution Contract

## Purpose

`configure_card_execution` is a small Manager-facing tool for card-level execution configuration.

It should only adjust:

- Python runtime selection;
- R runtime selection;
- attached skills;
- attached MCP servers.

It should not be a general permission editor, shell policy editor, environment editor, or prompt/instruction escape hatch. The normal execution baseline should be boring and system-owned:

- network access is allowed by default;
- Python execution is allowed by default;
- R/Rscript execution is allowed by default;
- shell execution is allowed by default.

Those are platform capabilities, not per-card choices Manager should tune. Manager should use this tool only when a card needs a non-default Python/R runtime or skill/MCP attachment.

## Tool Policy Boundary

Current policy fields are:

- `network`
- `python`
- `rscript`
- `shell`
- `git_write`

Target Manager-facing behavior:

- Do not expose `network`.
- Do not expose `python`.
- Do not expose `rscript`.
- Do not expose `shell`.
- Do not expose `git_write`.
- Keep the backend policy model if useful internally, but make these baseline abilities system defaults rather than Manager-selected fields.

Rationale:

- `network` should not be a Manager-side toggle for ordinary analysis. The executor needs model access, package metadata, and scientific databases often enough that the default should be explicit platform allowance.
- `python` and `rscript` should express available execution capabilities, not force a card into a single language. Manager can still prefer Python or R through instructions/runtime selection, but should not disable the other path.
- `shell` is part of practical executor operation. Disabling it per card tends to create brittle failures rather than a useful product-level safety boundary.

## `git_write`

`git_write` is different from Python/R/network/shell, but it should still not be exposed through `configure_card_execution`.

Likely intent:

- allow an executor to modify repository-tracked source files;
- allow commits or persistent curated script updates;
- support coding-agent style work where the executor is editing the product/project code, not just producing run artifacts.

For ordinary card execution, `git_write` should remain disabled. Card runs should write run-local files, generated scripts, manifests, and result assets under the run/result paths. They should not mutate tracked project code as part of normal analysis.

Manager-facing recommendation:

- Do not expose `git_write` in `configure_card_execution` for normal cards.
- Keep it as backend/internal/admin-only until there is a concrete workflow that needs executor-authored tracked code changes.
- If exposed later, it should be a separate explicit tool or privileged mode with a clear user-facing warning, not a casual boolean inside the normal card execution configuration.

## Runtime Bindings

Current runtime binding fields are:

- `conda_env`
- `r_env`
- `container_image`
- `working_dir`
- `env`

### `conda_env`

Keep this Manager-facing.

This selects the Python/CLI runtime. It is useful when the card requires a known environment such as `rnaseq`, `omicverse`, or another configured conda environment.

Manager should set it only when the system default is insufficient or a dependency issue clearly points to a specific runtime.

### `r_env`

Keep this Manager-facing.

This selects the R runtime. It is useful when R packages are installed in a separate environment such as `R_env`.

Manager should not treat `r_env` as "only use R"; it is a runtime binding. Language preference should remain soft guidance in the execution instructions.

### `container_image`

Do not expose this Manager-facing.

`RuntimeBindings.container_image` exists in the backend model, but there is currently no container executor implementation wired to it. The command worker and bwrap path do not launch runs from this field; the value is only preserved when executor contexts are merged.

Treat it as a reserved backend compatibility field, not a working product feature.

If container execution is added later, it should be modeled through a named executor profile or runtime profile. Manager should not provide arbitrary image names through `configure_card_execution`.

### `working_dir`

Backend-derived default should be enough.

Target default should be the current run directory, not the project root.

The executor already receives structured paths such as:

- project root;
- run directory;
- result directory;
- manifest path;
- task packet path.

Manager should not need to set `working_dir` for normal card execution. Exposing it creates a path-drift risk: two cards can look equivalent but run relative to different directories.

Current code note:

- `RuntimeBindings.working_dir` currently defaults to `"."`.
- `CommandTemplateWorkerAdapter` currently launches subprocesses with `cwd=project_root`.
- `BLUEPRINT_RUNTIME_WORKING_DIR` is currently only an environment variable, not the actual subprocess cwd.

Target behavior:

- backend derives the effective working directory as `runs/{run_id}/`;
- subprocesses launch with actual `cwd=runs/{run_id}/`;
- `BLUEPRINT_RUNTIME_WORKING_DIR` also points to `runs/{run_id}/`;
- executors can still access project root through `BLUEPRINT_PROJECT_ROOT`;
- existing project assets are accessed through explicit asset references, not by browsing and writing around project root;
- output/result paths remain explicit through `BLUEPRINT_RESULT_DIR` and the task packet;
- newly generated intermediate files and scratch files default to the run root;
- Manager does not provide this field.

### Run Root As The Execution Boundary

Card execution should treat the run root as its working boundary.

Expected access model:

- The executor may inspect project metadata and selected input assets.
- Input assets are selected by `asset_id`; the system resolves each asset to its stored file path and presents it as an explicit read-only input.
- The executor should not discover inputs by wandering through project root directories.
- New files created during execution should live under `runs/{run_id}/` by default.
- Final declared outputs should be copied or written to the run/result-owned output paths declared in the task packet.
- `project_root` remains available as a stable anchor for resolving explicit project-relative paths, not as the executor's writable workspace.

This is a product-level execution contract. `bwrap` and filesystem audit should remain defense-in-depth fallback mechanisms, not the primary way the product keeps run files organized.

Benefits:

- relative paths naturally land in the run directory instead of project root;
- rerun and cleanup can remove old run-local scratch files, logs, temporary files, and generated state cleanly;
- stale run files are less likely to pollute later executions;
- accidental writes outside the run/result/script areas become exceptional rather than normal executor behavior;
- the task packet and asset graph remain the source of truth for which prior assets were used.

Target Manager-facing behavior:

- Do not expose `working_dir` in `configure_card_execution`.
- Keep backend compatibility only if existing internal code needs it.
- If a future workflow genuinely needs a different working directory, model that as a named executor profile or backend-derived setting rather than free-form Manager input.

### `env`

`env` is the largest remaining runtime escape hatch.

Current behavior injects `runtime_bindings.env` into the executor process environment. In the bwrap path, those keys are also explicitly passed through into the sandbox.

Risks:

- accidental secret injection;
- PATH/proxy/library path drift;
- hidden one-off behavior that is not visible from the card plan;
- hard-to-reproduce runs if Manager invents environment variables.

Target Manager-facing behavior:

- Do not expose free-form `env` in `configure_card_execution`.
- Environment variables needed by executors should come from system config, runtime profiles, or backend-generated bindings.
- Manager should not generate arbitrary `env` values.

Possible exception:

- A future structured field may allow a small allowlist of non-secret, domain-specific variables, but that should be explicit and validated, not a free-form record.

## Target Manager-Facing Schema

The normal Manager-facing tool should narrow to:

```text
configure_card_execution(
  card_ids,
  skills?,
  mcp_servers?,
  runtime_bindings?: {
    conda_env?,
    r_env?
  }
)
```

Do not expose `instruction_blocks` in this Manager-facing tool.

Execution guidance should come from the card plan, system defaults, selected skills/MCP bindings, and runtime profiles. If the product later needs a dedicated instruction/note tool, it should be designed separately instead of turning `configure_card_execution` into a free-form prompt patcher.

## Backend Compatibility

Backend models may keep the broader fields temporarily:

- `ExecutorToolPolicy`
- `RuntimeBindings.container_image`
- `RuntimeBindings.working_dir`
- `RuntimeBindings.env`
- `ExecutorContext.instruction_blocks`

But the Manager sidecar should stop exposing them first. That keeps the product-facing contract small while preserving lower-level compatibility for existing direct APIs, tests, and executor adapters.

## Implementation Notes

P0 changes:

- Remove `tool_policy` from the Manager sidecar `configure_card_execution` schema.
- Remove `runtime_bindings.working_dir` from the Manager sidecar schema.
- Remove `runtime_bindings.env` from the Manager sidecar schema.
- Remove `instruction_blocks` from the Manager sidecar schema.
- Keep `runtime_bindings.conda_env` and `runtime_bindings.r_env`.
- Keep `skills` and `mcp_servers`.
- Update tool descriptions and prompt text so Manager understands this is only for Python/R runtime selection plus skill/MCP attachment.

P1 backend alignment:

- Change default `ExecutorToolPolicy.network` from `prompt` to `allow`.
- Ensure generated default executor contexts set `network="allow"`, `python=True`, `rscript=True`, and `shell=True`.
- Change backend-derived working directory semantics from project-root `"."` to the run directory.
- Launch executor subprocesses with `cwd=run_dir`, including the bwrap `--chdir` path.
- Set `BLUEPRINT_RUNTIME_WORKING_DIR` to the run directory.
- Keep `git_write=False` by default.
- Decide whether backend should reject Manager-originated `tool_policy`, `working_dir`, `env`, and `instruction_blocks`, or merely hide them from the sidecar while preserving direct API compatibility.

## Open Questions

1. Should there be an admin-only path for `git_write`, or should executor git writes remain unsupported for now?
2. Should runtime `env` be replaced by named runtime profiles before removing direct Manager access?
