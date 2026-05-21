# Controlled Executor Runtime Design

## Purpose

Blueprint executors should be able to use real local scientific software while still being prevented from mutating project state directly.

For RNA-seq and similar bioinformatics work, a fully sealed sandbox is too restrictive. Executors often need to:

- read and execute the host conda/R/Python environment
- inspect package help, manuals, and web documentation
- call external biological APIs or download task-specific annotations when explicitly needed
- write intermediate code, logs, plots, and caches during analysis

At the same time, executors must not be trusted to update Blueprint graph state. They generate evidence; the backend validates and materializes it.

## Design Goal

Use a controlled runtime rather than a hard sandbox.

The runtime should provide:

- read access to system tools and shared scientific environments
- read-only access to project inputs and stable project files
- write access only to the current run workspace and declared output locations
- network access controlled by task policy and recorded in run evidence
- post-run filesystem audit as a final guardrail
- backend-only mutation of graph, cards, assets, claims, and reports

## Trust Boundary

Executor responsibilities:

- read `runs/<run_id>/task_packet.json`
- consume declared input assets
- generate reproducible analysis code
- write outputs under declared run/result paths
- write `manifest.json`
- report progress and findings through `BP_EVENT` and/or `manager_brief.json`

Backend responsibilities:

- create task packet and run directories
- define allowed, readonly, and forbidden paths
- run permission approval checks
- launch the executor in the controlled runtime
- audit filesystem changes
- validate manifest and output files
- run deterministic and reviewer validation
- update `graph/` only after review succeeds

The executor must never directly mutate:

- `graph/`
- `.git/`
- upstream input assets
- other runs' result directories
- existing valid assets

## Filesystem Policy

For a project root such as:

```text
workspace/oaa/
```

the runtime should treat the project as read-only by default.

Writable paths for run `run_x` on card `rna_pca`:

```text
workspace/oaa/runs/run_x/
workspace/oaa/results/rna_pca/run_x/
workspace/oaa/scripts/generated/run_x/
```

Readonly project paths:

```text
workspace/oaa/configs/
workspace/oaa/data/
workspace/oaa/results/<upstream_card>/<upstream_run>/
```

Forbidden project paths:

```text
workspace/oaa/graph/
workspace/oaa/.git/
workspace/oaa/artifact_store/
```

Private runtime scratch paths should be placed under the run directory:

```text
workspace/oaa/runs/run_x/tmp/
workspace/oaa/runs/run_x/cache/
```

Recommended environment bindings:

```text
TMPDIR=runs/run_x/tmp
XDG_CACHE_HOME=runs/run_x/cache
R_USER_CACHE_DIR=runs/run_x/cache/R
MPLCONFIGDIR=runs/run_x/cache/matplotlib
R_PROFILE_USER=runs/run_x/.Rprofile
```

The runtime should also route accidental default outputs, such as R's `Rplots.pdf`, into the current run directory.

## System Environment Access

The executor may read and execute shared system environments:

```text
/bin
/usr
/lib
/lib64
/opt
/home/solarise/miniconda3
```

These locations should be mounted or exposed as read-only wherever possible. The executor may run tools from these environments but should not install packages into them during a task.

If a task needs package installation, it should use a run-local or project-managed environment path approved by policy, not mutate the global conda/R installation.

## Network Policy

Network access should not be globally denied. It should be classified and recorded.

Suggested policy levels:

```text
deny
allow_docs
allow_package_metadata
allow_bio_api
allow_data_download
allow_all
```

Default recommendation:

- real agent executors: `allow_docs` or `allow_bio_api` depending on task type
- local/demo executors: `prompt`
- high-risk data download or package install: requires runtime approval

Examples:

- PCA/DEG: documentation lookup is acceptable; new data download is usually not needed.
- KEGG/GO/Ensembl annotation: biological API access may be part of the task.
- GEO/SRA retrieval: data download should be explicitly approved and recorded.

Every network-enabled run should record:

- declared network policy
- approved permission decisions
- relevant URLs/domains when available
- downloaded files and their output paths

## Runtime Implementation Options

### Phase 1: Backend Audit Plus Environment Hygiene

This is the current lowest-friction layer.

Implement:

- strict task packet `allowed_paths`, `readonly_paths`, and `forbidden_paths`
- project-level executor serialization while using project-wide filesystem snapshots
- run-local temp/cache environment variables
- post-run filesystem audit
- manifest validation and reviewer validation

This catches accidental writes and most contract violations, but it does not prevent writes before they happen.

### Phase 2: Soft Sandbox Wrapper

Use a wrapper around the external agent command.

Preferred candidates:

- `bubblewrap`
- `nsjail`

Target behavior:

- host system and scientific environments are readable/executable
- project root is read-only
- only current run/result/generated-script paths are writable
- temp/cache paths resolve into the run directory
- forbidden paths are hidden or read-only
- network follows task policy

This is the best fit for reusing the host conda/R stack without building full containers.

### Phase 3: Rootless Container Runtime

Use rootless Podman or Docker for tasks that need more reproducibility.

Target behavior:

- a curated bioinformatics image provides R/Python/conda packages
- input assets are mounted read-only
- run/result/generated-script directories are mounted read-write
- global project state remains unavailable or read-only
- network and package install are controlled by runtime policy

This is stronger and more reproducible, but image maintenance becomes part of operations.

### Phase 4: Strong Isolation

Use gVisor, Kata Containers, or Firecracker for high-risk multi-tenant execution.

This is only justified if Blueprint runs untrusted third-party code or needs strong tenant isolation. It is likely too heavy for the first bioinformatics executor iteration.

## Review and Acceptance Flow

The runtime does not decide whether a result is accepted.

Acceptance remains:

1. executor writes outputs and manifest
2. backend audits filesystem changes
3. backend validates manifest against task packet
4. deterministic validator checks code artifacts and output files
5. reviewer checks code and evidence read-only
6. backend materializes assets and claims into `graph/`

This separation is intentional. Executors may produce evidence, but only the backend mutates project state.

## Open Questions

- Should `warn` from executor validation auto-accept, or require human confirmation?
- Which network policy should be the default for real agent executors?
- Do we want domain allowlists for documentation versus biological APIs?
- Should package installation be fully disallowed during runs, or allowed into a run-local environment?
- Should the runtime snapshot audit ignore backend-created graph changes from other non-executor operations, or should execution be project-exclusive during run review?
- How should long downloads be represented in the manifest and report?

## Initial Implementation Plan

1. Keep current post-run audit and manifest validation.
2. Make temp/cache bindings explicit in the command worker environment.
3. Add a runtime policy object to `TaskPacket.executor_context`.
4. Add network policy and approved domains to runtime approvals.
5. Prototype a `bubblewrap` wrapper for one command worker.
6. Run PCA/DEG through the wrapper with host conda/R read-only and run-local writes.
7. Compare audit output before and after wrapper enforcement.
8. Decide whether to generalize to `pi`, `opencode`, `codex`, and `claude_code` adapters.

