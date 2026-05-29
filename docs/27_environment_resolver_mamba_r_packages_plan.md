# Environment Resolver: Mamba for R Packages in Conda Environments

Status: implementation plan.

Date: 2026-05-29

## Problem

`install_runtime_dependencies` for R packages in a conda R environment fails silently:

1. Manager calls `install_runtime_dependencies(ecosystem="R", runtime="R_env", packages=["svglite"], manager="cran")`.
2. Backend resolves `Rscript` from the conda env and runs `Rscript --vanilla -e 'install.packages("svglite")'`.
3. CRAN sends the source tarball. Compilation requires `x86_64-conda-linux-gnu-c++` / `x86_64-conda-linux-gnu-cc`, which are part of conda's cross-compilation toolchain and are only available through `conda install`, not through CRAN source builds.
4. `install.packages()` emits compilation warnings/errors but the Rscript process itself returns exit code 0. The backend treats exit code 0 as success.
5. The job reports `ok: true`. The package is not actually installed. The next card run fails with "package 'svglite' not found".

The same problem affects any R package with compiled code (C/C++/Fortran) when installed via CRAN source into a conda R environment.

There is a second, independent product issue: dependency installation is a background task, but the Manager sidecar only enforces a hard async boundary for `start_card_run` / `rerun_card`. After `install_runtime_dependencies` returns a `job_id`, Manager can keep calling `get_runtime_dependency_install_status` or project inspection tools in the same turn, causing the same foreground polling loop that card runs already avoid.

Recent OAA-2 WGCNA diagnosis adds a third risk to keep under review: conda metadata may not always prove an R package is usable. The selected runtime was correctly injected as:

- `BLUEPRINT_RSCRIPT=/home/.../miniforge3/envs/R_env/bin/Rscript`
- `r_env=R_env`

But the target R runtime reported `requireNamespace("WGCNA") == FALSE`. At the same time, `conda list -p .../R_env` showed `cran-wgcna`, and `conda-meta/cran-wgcna-1.51-0.json` existed with `files: []`. This looks like an unusual broken package/channel case rather than the normal path. Keep it as a discussion item instead of making full R loadability verification a P0 requirement.

## Current R Install Path

`_r_dependency_command` (`manager_blueprint_tools.py:1234-1251`) supports two managers:

- **CRAN**: `Rscript -e 'install.packages(...)'` — source compilation.
- **Bioconductor**: `Rscript -e 'BiocManager::install(...)'` — source compilation.

Both compile from source. Neither works in conda R environments without the conda compiler toolchain.

`_dependency_manager_label` (`manager_blueprint_tools.py:1253-1258`) maps R managers:

```python
return "cran" if normalized == "cran" else "bioconductor"
```

Even if the manager passes `"conda"` or `"mamba"`, R ecosystem silently falls back to `"bioconductor"`. There is no conda install path for R packages.

## Proposed Change

Allow `manager: "conda"` or `manager: "mamba"` for R ecosystem. When selected, install R packages as conda packages (`r-{name}`) using mamba (preferred) or conda.

Unify runtime dependency installation with the existing card background-run model:

- `install_runtime_dependencies` starts background work and returns a boundary payload.
- Project-state events, not frontend polling, drive UI refresh.
- Dependency-install wake events resume auto Manager when the job reaches a terminal state.
- Manager is responsible for user-facing completion/failure messaging after the wake.
- `get_runtime_dependency_install_status` remains available for explicit user checks and recovery, not for normal foreground polling.

### Why conda/mamba instead of CRAN

- Conda-forge provides pre-compiled R packages with the correct compiler ABI for each conda environment.
- No local compilation needed. Install is fast and reliable.
- Dependency resolution is handled by the solver, not by R's `install.packages()`.
- The conda package name convention for R is `r-{lowercase_name}` (e.g., `svglite` → `r-svglite`, `DESeq2` → `r-deseq2`).

### Why prefer mamba over conda

- mamba is a drop-in replacement for conda with a much faster solver (libsolv vs. conda's classic solver).
- `micromamba` is even lighter and does not require a base environment.
- For batch installs of multiple R packages, solver speed matters.

## Implementation

### 1. Extend `_dependency_manager_label` for R conda

`manager_blueprint_tools.py:1253-1258`:

```python
@staticmethod
def _dependency_manager_label(ecosystem: str, manager: str | None) -> str:
    normalized = str(manager or "").strip().lower()
    if ecosystem == "python":
        return "conda" if normalized in {"conda", "mamba", "micromamba"} else "pip"
    # R ecosystem
    if normalized in {"conda", "mamba", "micromamba"}:
        return "conda"
    return "cran" if normalized == "cran" else "bioconductor"
```

### 2. Add conda branch to `_r_dependency_command`

`manager_blueprint_tools.py:1234-1251`:

Add a `manager_name == "conda"` branch before the existing CRAN/Bioconductor branches. The logic mirrors `_python_dependency_command`'s conda branch:

```python
def _r_dependency_command(self, runtime: str, packages: list[str], manager: str | None) -> tuple[list[str], str, Path]:
    manager_name = self._dependency_manager_label("R", manager)
    # Resolve through Rscript first so install and execution target the same env.
    rscript = CommandTemplateWorkerAdapter._resolve_rscript_runtime(runtime, self.project_service.settings)
    if rscript is None or not rscript.exists():
        raise ManagerPlanningError(f"R runtime not found: {runtime}")
    env_path = rscript.parent.parent
    conda_base = env_path.parent.parent if env_path.parent.name == "envs" else env_path
    if manager_name == "conda":
        conda_bin = self._resolve_conda_solver(conda_base)
        # CRAN package name → conda package name: lowercase, prefix with "r-"
        conda_packages = [f"r-{pkg.lower()}" for pkg in packages]
        return [
            str(conda_bin), "install", "-y", "-p", str(env_path),
            "-c", "conda-forge",
            *conda_packages,
        ], str(env_path), rscript
    # existing CRAN / Bioconductor path
    ...
```

The important constraint is not the exact return type; it is that the install command, the success check, and future card execution all use the same resolved R environment. Do not derive the install env from `settings.executor_conda_base` alone. In local OAA-2, `BLUEPRINT_EXECUTOR_CONDA_BASE` pointed at `miniconda3`, while `R_env` actually lived under `miniforge3`.

### 3. Add `_resolve_conda_solver` helper

Resolve the best available conda solver binary. Search order:

1. `{conda_base}/bin/mamba` — preferred, fast solver.
2. `{conda_base}/bin/conda` — fallback, always present in a conda base.
3. `micromamba` on `$PATH` — for micromamba-only setups.

```python
def _resolve_conda_solver(self, conda_base: Path) -> Path:
    for name in ("mamba", "conda"):
        candidate = conda_base / "bin" / name
        if candidate.exists():
            return candidate
    micromamba = shutil.which("micromamba")
    if micromamba:
        return Path(micromamba)
    raise ManagerPlanningError(f"No conda solver found at {conda_base}/bin/ (mamba or conda).")
```

This can be shared with `_python_dependency_command` so Python conda installs also benefit from mamba when available.

### 4. Fix exit-code-0 false positives

`_install_runtime_dependencies_sync` (`manager_blueprint_tools.py:729-793`) currently uses `result.returncode == 0` as the success check. For R CRAN/Bioconductor installs, `Rscript` returns 0 even when packages fail to compile.

Add a stderr heuristic for the R source-install path:

```python
ok = result.returncode == 0
# R's install.packages() returns exit 0 even on per-package compilation failure.
# Check stderr for compilation errors when the install was R source-based.
if ok and ecosystem == "R" and manager_name != "conda":
    stderr_lower = (result.stderr or "").lower()
    if "error" in stderr_lower and ("compilation" in stderr_lower or "cannot install" in stderr_lower):
        ok = False
```

For conda/mamba installs, rely on the solver exit code for v1. A full post-install `requireNamespace()` check can be added later if broken channel/package metadata becomes a repeated problem.

### 5. Update tool description and system prompt

`server.js` — `install_runtime_dependencies` tool description (line 1512):

```javascript
manager: Type.Optional(Type.String({
  description: "For python: pip or conda. For R: bioconductor, cran, or conda/mamba. "
    + "Use conda/mamba for R when the runtime is a conda environment and you need pre-compiled binaries. "
    + "Defaults to pip for python and bioconductor for R.",
})),
```

System prompt guidance (line 183): add a note about conda/mamba for R:

```
For R packages in a conda R environment, prefer manager "conda" or "mamba" to install
pre-compiled binaries from conda-forge. CRAN source installs require compilers that conda
environments may not have.
```

Also update the async-boundary prompt contract:

```
install_runtime_dependencies starts background environment work. After it returns a job_id,
do not poll the job or inspect the project in the same turn. Report the job_id and stop;
project-state events and dependency-install wake events will resume Manager when the job
finishes.
```

### 6. Reuse card background-task protocol for dependency jobs

`manager-agent/src/server.js` currently enforces `asyncBoundary` only for successful `start_card_run` / `rerun_card` payloads. Dependency install should use the same background-task protocol, not a separate model.

Backend `install_runtime_dependencies` response should include:

```json
{
  "ok": true,
  "background": true,
  "async_boundary": true,
  "do_not_poll": true,
  "wait_for_wake": true,
  "job_id": "depjob_..."
}
```

Manager-agent should generalize the existing run boundary detector:

- Recognize successful `start_card_run` / `rerun_card` payloads by `run_id`.
- Recognize successful `install_runtime_dependencies` payloads by `job_id`.
- Store a generic boundary object: `{ active, toolName, runId, jobId }`.
- Block later same-turn tools with a terminal `async_boundary_active` result.
- Preserve the current explicit-interrupt exception for card runs. Dependency jobs do not need a stop tool in v1.

This keeps runtime dependency installation aligned with card execution: both are real background work, not foreground polling tasks.

### 7. Use project-state events instead of frontend job polling

Frontend currently tracks pending dependency jobs from `tool_report` and polls `/runtime-dependency-jobs/{job_id}`. Replace normal polling with the same project-state event path used for card run updates:

- `RuntimeDependencyJobService` already emits `runtime_dependency_job_changed`.
- Ensure the event payload carries `job_id`, `job_status`, `runtime`, and `packages`.
- `ManagerChatPanel` should listen to project-state events and invalidate/refetch the same project/auto state caches used for card runs.
- The frontend may still call `/runtime-dependency-jobs/{job_id}` for explicit details or recovery, but it should not maintain a normal interval poll for running jobs.

User-facing completion/failure messages should come from Manager wake handling, not from frontend polling. This avoids duplicate "job completed" messages and keeps the assistant narrative in one place.

### 8. Persist dependency job state like card run state

`RuntimeDependencyJobService.jobs` is currently in-memory. Align it with card run durability:

- Persist dependency job records into project graph metadata or a project-local job file.
- Store at least: `job_id`, `project_id`, `status`, `payload`, `result`, `error`, `created_at`, `started_at`, `finished_at`.
- On backend restart, reload non-terminal jobs as `failed` or `unknown/interrupted` with a clear message; do not silently forget them.
- Project-state baseline should include enough auto state (`active_job_id`) for the UI to show that auto had a background dependency task attached.
- Wake emission remains the terminal handoff for Manager; if restart prevents a terminal wake, the persisted interrupted state should make recovery explicit.

This is the dependency-job equivalent of card run records under `graph/runs.json`: the UI and manager should not depend on process memory as the only source of truth.

### 9. Package name mapping edge cases

CRAN → conda package name mapping:

- `svglite` → `r-svglite`
- `DESeq2` → `r-deseq2`
- `data.table` → `r-data.table`
- `BiocManager` → `r-biocmanager`

The convention is `r-{tolower(name)}`. This works for the vast majority of CRAN and Bioconductor packages on conda-forge.

For packages where the conda name does not follow this convention, Manager can try a different manager/channel or report that the runtime needs manual repair.

WGCNA is a concrete edge case to keep in manual verification discussion: `conda list` can show `cran-wgcna`, while `requireNamespace("WGCNA")` is still false. Do not make this a P0 blocker unless it repeats across normal conda-forge/bioconda package paths.

## Affected Files

| File | Change |
|---|---|
| `backend/app/services/manager_blueprint_tools.py` | `_dependency_manager_label`, `_r_dependency_command`, `_resolve_conda_solver`, success heuristic, unified boundary payload |
| `backend/app/services/runtime_dependency_job_service.py` | persist dependency jobs; enrich project-state events |
| `frontend/components/manager-chat/ManagerChatPanel.tsx` | remove normal dependency-job interval polling; use project-state events and Manager wake messages |
| `manager-agent/src/server.js` | tool description update for `manager` parameter; dependency-job async boundary |

## Verification

```bash
# After backend changes
PYTHONPATH=backend .venv/backend/bin/python -m unittest discover -s backend/tests

# After manager-agent changes
cd manager-agent && node --check src/server.js
```

Targeted tests to add:

1. `install_runtime_dependencies` with `manager: "mamba"` for R resolves the same env path as `_resolve_rscript_runtime("R_env")`.
2. R source install returns `ok: false` when stderr indicates compilation/install failure even if subprocess return code is 0.
3. Backend response includes `async_boundary`, `do_not_poll`, and `wait_for_wake` for successful dependency jobs.
4. Manager sidecar activates async boundary after successful `install_runtime_dependencies` and blocks same-turn polling calls such as `get_runtime_dependency_install_status` / `inspect_project_summary`.
5. Project-state event for a dependency job updates auto/task state without frontend interval polling.
6. Dependency job state survives backend service restart or is marked interrupted explicitly.

Manual test: have the manager call `install_runtime_dependencies` with `ecosystem: "R"`, `runtime: "R_env"`, `packages: ["svglite"]`, `manager: "mamba"` and verify:

- the command targets the same env as `BLUEPRINT_RSCRIPT`;
- the backend returns the same background boundary fields as card runs;
- the frontend reflects running/completed state from project-state events rather than a job polling interval;
- Manager stops after reporting `job_id` and resumes only from dependency-install wake/project-state events.

## Deferred Discussion: R Loadability Check

The WGCNA observation suggests a possible future hardening: after any R package install, run the selected `Rscript` and verify `requireNamespace()` for all requested packages. This would catch metadata-only or broken package records.

Do not implement this as P0 yet unless it repeats:

- It adds extra subprocess work after every R install.
- Some package names may not match their load namespace exactly.
- Conda/mamba solver failure is normally represented by a nonzero return code.

If repeated failures occur, add a package-to-namespace override map and return structured `missing_after_install` details.
