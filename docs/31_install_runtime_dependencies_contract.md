# Install Runtime Dependencies Contract

Status: contract and product boundary.

Date: 2026-05-30

## Purpose

`install_runtime_dependencies` is the Manager-facing tool for runtime package installation.

It is not limited to one narrow repair workflow. Manager may use it whenever runtime package installation is genuinely needed and the package list is explicit. The point of this tool is to keep environment installation work out of the normal manager chat turn and out of executor self-repair loops, especially when dependency solving, downloading, or compilation would otherwise waste context and create noisy foreground retries.

This tool is therefore:

- allowed for dependency repair after a blocked card run;
- allowed for deliberate environment preparation before a run;
- allowed for user-requested runtime package installation;
- not meant for vague exploratory environment debugging;
- not a substitute for system-level setup outside the selected runtime.

## Product Boundary

The tool should remain narrow and boring:

- it installs explicit Python or R packages into an already selected non-system runtime;
- it runs as real background work;
- it hands status back through project-state events and Manager wake handling;
- it does not become a foreground polling workflow;
- it does not become a generic shell command runner or environment editor.

The tool should not require Manager to manually perform package installation by emitting long shell transcripts in chat. That path wastes context and makes failure handling worse, especially for compiled packages and slow solvers.

## Manager-Facing Inputs

Keep the Manager-facing schema small:

- `ecosystem`
- `runtime`
- `packages`
- `timeout_seconds`

### `ecosystem`

Allowed values:

- `python`
- `R`

Backend may normalize case, but the contract is still only these two ecosystems.

### `runtime`

Required. This must be a selected non-system runtime name.

Examples:

- `omicverse`
- `rnaseq`
- `R_env`

Rules:

- `__system__` must be rejected;
- empty runtime must be rejected;
- runtime selection should already exist before installation starts.

This tool installs into a runtime that is already part of the project execution model. It should not invent or create runtimes on the fly.

### `packages`

Required. This must be an explicit package list.

Rules:

- at least one package;
- bounded maximum list size;
- package names should be normalized and deduplicated;
- no vague requests like "whatever is missing" or "fix the environment".

Manager is allowed to use the tool outside strict repair flows, but the package request must still be concrete.

Manager should provide ecosystem-native package names, not solver-specific package specs.

Examples:

- Python: `scanpy`, `numpy`, `pydeseq2`
- R: `DESeq2`, `WGCNA`, `svglite`

Manager should not be responsible for adding conda-specific prefixes such as `r-` or guessing channel-specific distribution names.

### Installer Selection

Manager must not provide a package manager selector. Installer choice is a backend policy, not a tool argument.

The backend is responsible for preferring:

1. `mamba`
2. `conda`
3. `micromamba`

when those solvers are available.

### `timeout_seconds`

Optional. Keep the current bounded timeout model.

Contract:

- backend clamps or validates to a safe range;
- manager should not assume exact timing semantics;
- this exists as an execution budget hint, not a precision scheduling interface.

## Solver Preference Order

Default solver preference should favor dependency resolution quality and reproducibility over minimal tooling. This preference is a backend execution rule, not a Manager-facing selector list.

### Python

Default path:

1. conda-family solver path, with backend preference `mamba -> conda -> micromamba`

Rationale:

- `mamba` is the preferred solver when available;
- `conda` is the standard fallback when `mamba` is unavailable;
- `micromamba` is an acceptable backend fallback when present;
- `pip` is not Manager-selectable through this tool. If conda-family resolution cannot find the package, Manager should surface the failed package name and ask for manual environment preparation or a corrected distribution name.

Backend should default Python installs to the conda-family path.

### R

Default path:

1. conda-family solver path, with backend preference `mamba -> conda -> micromamba`

Rationale:

- conda-family installation provides pre-built binaries for many R packages and avoids local compilation;
- CRAN/Bioconductor source installation is slower, noisier, and more failure-prone inside conda R environments;
- CRAN/Bioconductor source installation is not Manager-selectable through this tool. If needed later, it must be a backend-owned fallback with the runtime `bin` directory prepended to `PATH` so conda compiler wrappers are visible.

Backend should default R installs to the conda-family path for conda R runtimes.

## Preflight Expectations

The install flow should perform lightweight preflight checks before running the real background job, and error messages should guide Manager clearly.

Expected checks:

1. verify that the requested runtime is selected and non-system;
2. verify that the target runtime can resolve the corresponding Python or R executable;
3. for conda-style installation, detect whether a conda-family solver is available;
4. prefer `mamba` when present;
5. if only `conda` exists, continue with `conda`;
6. if neither `mamba` nor `conda` exists, continue with `micromamba` when available;
7. if no conda-family solver exists for a requested conda-style install, return a clear error that recommends installing a conda solver;
8. if `mamba` is absent but `conda` or `micromamba` exists, the message may recommend `mamba` as a performance improvement, but this should be advisory rather than a hard failure.

The backend must resolve and operate against the actual runtime path, not just an environment name. In particular, it should not rely on `conda activate <env-name>` semantics, because the runtime may not be registered under that name in the active base installation. The robust path is to resolve the target environment directory first and then run installation commands against that path directly, for example with `-p <env_path>` or the equivalent resolved executable path.

This preflight should not turn into a full environment doctor. It only needs to protect the main install path from obvious misconfiguration.

## Package Name Resolution

Package name resolution should be owned by the backend, not by Manager prompt logic and not by a growing handwritten mapping table.

### Contract

- Manager sends ecosystem-native package names.
- Backend resolves concrete installable package specs for the backend-selected conda-family installer.
- Backend should prefer repository-backed search and normalization over hardcoded name maps.
- Only when backend resolution and obvious fallback paths both fail should Manager be asked to search for a different distribution name.

### Conda-Family Resolution

For conda-style installation, backend should resolve package names by searching the available conda repositories rather than assuming a fixed handwritten mapping table.

Recommended approach:

1. determine the ecosystem;
2. resolve the actual target runtime path;
3. use the available conda-family solver or search command to look up candidate packages;
4. install the resolved candidate package spec against the resolved runtime path.

Possible search mechanisms:

- `mamba repoquery search`
- `conda search --json`

The exact command may vary, but the important behavior is:

- package resolution uses the real repository index;
- resolution is based on the selected ecosystem and channel path;
- installation uses the resolved environment path rather than an environment name.

### Python Resolution

For Python packages:

- first search the conda-family repositories using the package name as given;
- backend may normalize simple variants such as lowercase and `-` vs `_`;
- if a suitable conda-family package is found, install it through the conda-family path;
- if not found, return a structured failure with attempted candidates and manual fallback guidance.

### R Resolution

For R packages:

- Manager should still send the semantic R package name, for example `DESeq2` or `WGCNA`;
- backend should try conda-style candidate forms such as `r-<lowercase(name)>` and other repository-backed matches;
- backend may use CRAN or Bioconductor registry information as a fallback hint source, but Manager should not have to guess the conda package name itself;
- if the package is found in the conda-family repositories, install the resolved distribution package;
- if not found, backend should indicate whether a manual `cran` or `bioconductor` preparation path may be sensible, without asking Manager to retry this tool with a package-manager selector.

This keeps the package-name burden out of the LLM and out of user chat.

### No Handwritten Mapping Table As The Main Strategy

Do not make a long handwritten package alias table the primary resolution mechanism.

Reasons:

- channel inventories change over time;
- many failures are repository-availability questions, not naming-rule questions;
- handwritten tables drift and create opaque behavior;
- repository-backed search is a more truthful source of installability.

Small exception tables may still exist for repeated edge cases, but they should be a narrow fallback, not the core design.

### Escalation When Resolution Fails

If backend cannot resolve a conda-family package name, it should first return structured information that allows a controlled fallback instead of immediately asking Manager to search the web.

Preferred sequence:

1. try conda-family resolution;
2. if not found, return a structured error with attempted candidate names and any obvious fallback path;
3. let Manager try the controlled fallback when appropriate;
4. only if neither repository-backed resolution nor controlled fallback is sufficient should Manager be told to search for the real distribution name.

This prevents noisy "guess and retry" loops.

## Background Task Protocol

This tool should follow the same hard turn-boundary contract as card execution.

Successful start response should include:

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

Manager-side behavior:

- after a successful start, report the `job_id` and end the turn;
- do not call `get_runtime_dependency_install_status` in the same turn;
- do not inspect project state in the same turn just to wait for the install;
- wait for project-state events or dependency-install wake events.

This tool should be treated as real background work, not a conversational loop.

## State Synchronization Model

Normal state synchronization should reuse the same layered model as card runs:

- project-state events drive UI refresh;
- dependency-install wake events resume auto Manager at terminal states;
- Manager provides the user-facing completion or failure explanation;
- explicit status fetch remains available for user-requested checks and recovery only.

Frontend should not maintain routine interval polling for running dependency jobs.

## Error Model

The current `ok: false` plus free-text message is not enough for stable Manager recovery behavior. The tool should return structured error codes for common failure classes.

Recommended initial error codes:

- `runtime_not_selected`
- `system_runtime_not_allowed`
- `runtime_executable_not_found`
- `conda_solver_not_available`
- `package_not_found_in_conda_channels`
- `package_name_resolution_required`
- `fallback_available`
- `dependency_install_timeout`
- `dependency_install_start_failed`
- `dependency_install_failed`
- `dependency_install_compilation_failed`
- `external_source_install_not_supported`
- `github_source_install_not_supported`

These codes do not need to cover every edge case. They only need to distinguish:

- corrected package names or user-provided environment preparation choices;
- missing host/runtime prerequisites;
- likely manual environment repair cases.

When resolution fails, backend should include enough structured context for Manager to choose the next step without guessing.

Useful fields include:

- `requested_package`
- `attempted_candidates`
- `manager`
- `runtime`
- `fallback_available`
- `message`

## What This Tool Is Not

This tool should not:

- edit runtime env vars directly;
- create or register runtimes;
- install system packages;
- compile arbitrary native libraries outside the selected runtime model;
- run arbitrary shell remediation in chat;
- become a generic "fix everything" environment command.

If a required dependency is actually a system tool, external database, driver, compiler, or broken host installation, Manager should surface that clearly to the user instead of retrying package installation blindly.

## Unsupported Source-Install Scenarios In P0

P0 should not try to absorb arbitrary source-install workflows into `install_runtime_dependencies`.

In particular, do not treat these as normal supported dependency-install paths in v1:

- `devtools::install_github()`
- `remotes::install_github()`
- Git repository installs
- tarball or URL-based source installs
- ad hoc local source package installs

These cases are qualitatively different from normal registry-backed installation because they may require:

- repository URL or owner/repo identification;
- branch, tag, or commit resolution;
- Git/network availability beyond standard package registries;
- heavy source compilation;
- extra system prerequisites;
- weaker reproducibility and harder failure classification.

That complexity should not be hidden inside the normal package-install tool contract.

### Expected P0 Behavior

If Manager requests an R package or Python package that effectively requires a GitHub/devtools/remotes/source-install path, backend should not improvise that workflow automatically.

Instead, backend should return a structured failure such as:

- `external_source_install_not_supported`
- `github_source_install_not_supported`

with a message that explains:

- the current tool supports registry-backed installation only;
- the requested dependency appears to require a source-install workflow;
- this should be handled as a separate, higher-risk environment preparation path.

### Manager Guidance For Unsupported Source Installs

When this happens, Manager should:

- avoid repeated retry loops through the normal dependency installer;
- explain that the dependency is not available through the standard supported install paths;
- tell the user that GitHub/source installation is currently outside the normal runtime dependency contract;
- only proceed through a separate explicit environment-preparation workflow if the product later adds one.

### Why This Stays Deferred

Supporting source installs well would require a larger contract, including questions such as:

- how to express repository source information;
- how to pin branches, tags, or commits;
- how to classify Git/network failures separately from build failures;
- how to verify reproducibility and runtime usability afterward.

That is a separate product surface, not a small extension of the current dependency installer.

## Relationship To Existing Plans

This contract is compatible with [docs/27_environment_resolver_mamba_r_packages_plan.md](/home/solarise/blueprint_re_v3/docs/27_environment_resolver_mamba_r_packages_plan.md), but it is broader in product scope.

Doc 27 focuses on:

- R package installation in conda R environments;
- mamba/conda support;
- background-task protocol;
- project-state event and wake integration.

This document adds the product boundary:

- the tool is not only a repair tool;
- Python and R installation should use the backend-selected conda-family path;
- Manager should use the tool to avoid wasting foreground context on environment installation work;
- the user-facing contract should stay aligned with the existing background task model used for card execution.

## Proposed Manager Guidance

Manager guidance should be updated to express the following:

- Use `install_runtime_dependencies` when explicit Python or R packages need to be added to an already selected non-system runtime.
- Do not choose or pass a package manager. Backend should choose the best available conda-family solver automatically.
- Provide ecosystem-native package names; do not try to guess solver-specific package names such as `r-...`.
- If a conda-family install fails because the package is not available in the selected channels, Manager should not retry with a package manager argument. It should surface the attempted candidates and ask for manual environment preparation or a corrected distribution name.
- Only ask for external lookup of the real package or distribution name after backend resolution and obvious controlled fallback paths are exhausted.
- After the tool returns a `job_id`, stop the turn and wait for project-state events or wake events instead of polling.
- If installation fails because the runtime executable or conda solver is missing, explain the prerequisite clearly instead of retrying blindly.

## P0 Implementation Direction

P0 for this contract should focus on:

1. align Manager prompt and tool description with the background-task contract;
2. enforce the same hard async boundary used by card runs;
3. make Python default to the conda-family path;
4. keep R defaulting to the conda-family path for conda R runtimes;
5. improve structured error returns for missing runtime/solver and common install failures;
6. keep project-state event and wake-driven synchronization as the normal path.

## Deferred Items

The following can stay deferred unless they prove necessary:

- full post-install import/load verification for every requested package;
- namespace remapping tables for unusual R package names;
- a dedicated stop/cancel tool for dependency jobs;
- richer environment diagnosis beyond runtime/solver existence and install failure classification.
