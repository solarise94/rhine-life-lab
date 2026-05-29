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
def _r_dependency_command(self, runtime: str, packages: list[str], manager: str | None) -> tuple[list[str], str]:
    manager_name = self._dependency_manager_label("R", manager)
    conda_base, env_path = CommandTemplateWorkerAdapter._resolve_conda_runtime(runtime, self.project_service.settings)
    if manager_name == "conda":
        if not env_path.exists():
            raise ManagerPlanningError(f"R runtime not found: {runtime}")
        conda_bin = self._resolve_conda_solver(conda_base)
        # CRAN package name → conda package name: lowercase, prefix with "r-"
        conda_packages = [f"r-{pkg.lower()}" for pkg in packages]
        return [
            str(conda_bin), "install", "-y", "-p", str(env_path),
            "-c", "conda-forge",
            *conda_packages,
        ], str(env_path)
    # existing CRAN / Bioconductor path
    rscript = CommandTemplateWorkerAdapter._resolve_rscript_runtime(runtime, self.project_service.settings)
    if rscript is None or not rscript.exists():
        raise ManagerPlanningError(f"R runtime not found: {runtime}")
    ...
```

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

### 4. Fix exit-code-0 false positive

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

For conda installs this is not needed — conda returns a nonzero exit code on solver or install failure.

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

### 6. Package name mapping edge cases

CRAN → conda package name mapping:

- `svglite` → `r-svglite`
- `DESeq2` → `r-deseq2`
- `data.table` → `r-data.table`
- `BiocManager` → `r-biocmanager`

The convention is `r-{tolower(name)}`. This works for the vast majority of CRAN and Bioconductor packages on conda-forge.

For packages where the conda name does not follow this convention (rare), the manager can fall back to `manager: "bioconductor"` or `manager: "cran"` for source install.

## Affected Files

| File | Change |
|---|---|
| `backend/app/services/manager_blueprint_tools.py` | `_dependency_manager_label`, `_r_dependency_command`, `_resolve_conda_solver`, success heuristic |
| `manager-agent/src/server.js` | tool description update for `manager` parameter |

## Verification

```bash
# After backend changes
PYTHONPATH=backend .venv/backend/bin/python -m pytest backend/tests/ --tb=short -q

# After manager-agent changes
node --check manager-agent/src/server.js
```

Manual test: have the manager call `install_runtime_dependencies` with `ecosystem: "R"`, `runtime: "R_env"`, `packages: ["svglite"]`, `manager: "mamba"` and verify the conda install succeeds and the package is loadable in the next card run.
