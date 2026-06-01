# Runtime Dependency Resolver Failure Reporting Review

Status: review analysis note.

Date: 2026-06-01

## Summary

OAA-2 exposed two related but separate problems in runtime dependency repair:

1. dependency installation jobs fail repeatedly for understandable resolver reasons;
2. those failures are persisted in backend state, but they are not surfaced to the frontend as clear user-facing failure reports.

The current implementation is not losing all failure data. It writes detailed job results to:

```text
chat/runtime_dependency_jobs.json
```

and exposes details through:

```text
GET /projects/{project_id}/runtime-dependency-jobs/{job_id}
```

The gap is product propagation: project events and task/workboard UI refreshes do not carry enough failure detail, and the frontend does not currently consume the job-detail endpoint for dependency failure display.

The resolver itself is also intentionally narrow. It currently behaves as a conda-family installer. It reports fallback families such as `pip`, `cran`, and `bioconductor`, but normal execution does not automatically enter those fallback paths.

## OAA-2 Evidence

`workspace/oaa-2/chat/runtime_dependency_jobs.json` contains 9 failed dependency jobs.

Observed pattern:

```text
6 x python / omicverse / pydeseq2
3 x R / R_env / limma-family package groups
```

Representative Python failure:

```json
{
  "status": "failed",
  "result": {
    "ok": false,
    "error_code": "package_not_found_in_conda_channels",
    "requested_package": "pydeseq2",
    "attempted_candidates": ["pydeseq2"],
    "fallback_available": ["pip"],
    "manager": "conda",
    "message": "Package pydeseq2 was not found in conda channels. Attempted: pydeseq2."
  }
}
```

Representative R failure:

```json
{
  "status": "failed",
  "result": {
    "ok": false,
    "error_code": "package_not_found_in_conda_channels",
    "requested_package": "limma",
    "attempted_candidates": ["r-limma", "bioconductor-limma"],
    "fallback_available": ["cran", "bioconductor"],
    "manager": "conda",
    "message": "Package limma was not found in conda channels. Attempted: r-limma, bioconductor-limma."
  }
}
```

So the backend knew:

- which package failed;
- which candidates were attempted;
- which installer family was used;
- which fallback families might be relevant.

The user-facing problem is that this information did not become a prominent frontend report.

## Why The Resolver Fails

### 1. Installer Policy Is Fixed To Conda

`ManagerBlueprintTools._dependency_manager_label(...)` currently returns:

```python
return "conda"
```

That means both Python and R dependency install requests use the conda-family path.

The code still contains pip/CRAN/Bioconductor command branches, but normal policy selection never reaches them.

Practical effect:

```text
fallback_available = ["pip"] does not mean pip was attempted.
fallback_available = ["cran", "bioconductor"] does not mean CRAN/Bioconductor was attempted.
```

Those fields are currently guidance, not execution.

### 2. Resolution Stops On First Unresolvable Package

For a package list, the backend resolves each package into a conda candidate before launching the install command.

For R:

```text
limma -> r-limma, bioconductor-limma
```

If `limma` is not found, the entire group fails before checking or installing the rest of the list.

This is safe, but it produces coarse failures for mixed package groups:

```text
limma, edgeR, ggplot2, pheatmap, clusterProfiler, ...
```

The result says "limma failed", but the actual repair plan should classify every package:

- conda-installable;
- conda-missing but pip/CRAN/Bioconductor-installable;
- source/install-script required;
- system dependency required;
- unknown.

### 3. Channel Search May Be Too Narrow Or Environment-Specific

The resolver checks package presence with conda-family search commands.

Current behavior depends on the solver and configured channels available to the runtime:

```text
mamba repoquery search
conda search --json
```

If the local conda configuration does not include the relevant channel set, the resolver reports "not found in conda channels" even if another channel or ecosystem registry has the package.

This is still a truthful result for the configured conda-family environment, but not a complete dependency-resolution answer.

### 4. Failed Requests Are Not Deduped

OAA-2 repeated the same `pydeseq2` failure 6 times.

This indicates there is no effective cooling key such as:

```text
project_id + ecosystem + runtime + normalized_packages + error_code
```

Without that, Manager can retry the same impossible conda install instead of escalating to:

- controlled fallback;
- package-name correction;
- user-facing manual preparation.

Manager-agent has retry hints telling Manager not to retry `package_not_found_in_conda_channels` with a manager argument, but those hints depend on the model following tool-result guidance. They are not enforced by backend job admission.

## Why It Is Not Reported Clearly To Frontend

### 1. Project Event Payload Is Too Shallow

`RuntimeDependencyJobService._emit_project_event(...)` emits `runtime_dependency_job_changed`.

The event payload includes:

```json
{
  "task_id": "...",
  "job_status": "failed",
  "runtime": "...",
  "packages": ["..."],
  "manager": "conda",
  "started_at": "...",
  "finished_at": "...",
  "ok": false
}
```

It does not include the useful failure fields:

- `error_code`;
- `message`;
- `requested_package`;
- `attempted_candidates`;
- `fallback_available`;
- `stdout_tail`;
- `stderr_tail`;
- `retry_hint`;
- `source.card_id`;
- `source.run_id`;
- normalized grouping or dedupe key.

So the event stream can tell the frontend "a dependency job changed", but not "why dependency resolution failed".

### 2. Frontend Treats Project Events As Refetch Triggers

`ProjectWorkspace` listens to `/projects/{project_id}/events`, but its event handler only schedules broad query refetches.

It does not branch on:

```text
reason == runtime_dependency_job_changed
job_status == failed
```

and it does not display a dependency-job toast, banner, card alert, or detail drawer.

So even if the event arrives, it mostly acts as cache invalidation.

### 3. Job Detail API Exists But Is Not Consumed

`frontend/lib/api.ts` defines:

```text
getRuntimeDependencyJob(projectId, jobId)
```

but there is no frontend component currently calling it.

That means the richer endpoint is effectively dormant from the user's perspective.

### 4. Workboard Has The Data, But It Is A Manager Surface

`BackgroundWorkboardService` derives `runtime_dependency_install_failed` items from `chat/runtime_dependency_jobs.json` and puts them in `needs_manager`.

That helps Manager wake/reasoning, but it is not the same as a frontend failure-reporting surface.

If the user is looking at the task board, card detail, or run controls, the failure may only appear indirectly as:

```text
runtime_dependency_repair_failed
runtime_dependency_repair_in_progress
```

These block reasons are not enough to explain:

- which package failed;
- what was tried;
- what fallback is possible;
- whether the next action is Manager retry, dependency agent repair, or manual runtime preparation.

### 5. Work Order Type Does Not Expose Runtime Dependency Blocker Details

Backend `FlowService` includes `runtime_dependency_blocker` details in work items, but frontend `WorkItem` type currently only exposes generic block arrays and dependency attention fields.

This creates another impedance mismatch:

```text
backend has blocker.result/error
frontend type/display has block_reasons
```

The UI can show that start is blocked, but not the precise dependency job failure.

## Manager Vs Dedicated Dependency Resolver Agent

A dedicated dependency resolver agent is likely the better product boundary, but it should not become a free-form shell installer.

### Why Manager Alone Is Not Ideal

Manager is good at orchestration:

- read workboard;
- decide whether a card should run;
- call dependency install;
- stop at async boundary;
- explain final state to user.

Manager is not the best place for package ecosystem reasoning:

- conda package naming;
- pip vs conda compatibility;
- CRAN vs Bioconductor classification;
- source install risk;
- system library inference;
- duplicate failure cooling;
- multi-package repair planning.

Putting all of that in the Manager prompt makes behavior probabilistic and retry-prone.

### Better Boundary: Dependency Resolution Agent Or Service

Introduce a resolver layer that produces a structured plan before installation.

It can be implemented as either:

1. a deterministic backend service with optional registry probes;
2. a constrained "dependency resolver agent" that can inspect resolver outputs and propose a plan;
3. both, where deterministic service handles known cases and the agent handles ambiguous cases.

The key contract should be:

```text
Resolver decides what should be attempted.
Backend installer executes only approved, structured install actions.
Manager orchestrates and reports.
Frontend displays job state and failure reasons.
```

The resolver agent should not run arbitrary shell commands. It should return a plan such as:

```json
{
  "runtime": "R_env",
  "ecosystem": "R",
  "packages": [
    {
      "name": "limma",
      "classification": "bioconductor",
      "conda_candidates": ["bioconductor-limma"],
      "fallback": "bioconductor",
      "risk": "medium"
    }
  ],
  "recommended_actions": [
    {
      "kind": "install",
      "installer": "conda",
      "packages": ["bioconductor-limma"]
    },
    {
      "kind": "manual_preparation_required",
      "reason": "package_not_found_in_configured_channels"
    }
  ]
}
```

Backend then decides which actions are allowed automatically.

## Recommended Fix Direction

### 1. Make Dependency Job Failure A First-Class UI Event

Enrich `runtime_dependency_job_changed` terminal events with:

- `error_code`;
- `message`;
- `requested_package`;
- `attempted_candidates`;
- `fallback_available`;
- `retry_hint`;
- `card_id`;
- `run_id`;
- `dedupe_key`.

Frontend should react directly to failed dependency jobs:

- show a persistent project notice or task-card alert;
- allow expanding job detail;
- link to the affected card/run;
- distinguish "still installing" from "terminal failed".

### 2. Expose Runtime Dependency Blockers In WorkOrder Types

Frontend `WorkItem` should include a typed runtime dependency blocker:

```ts
runtime_dependency_blocker?: {
  job_id: string;
  status: string;
  runtime: string;
  packages: string[];
  result?: {
    error_code?: string;
    message?: string;
    requested_package?: string;
    attempted_candidates?: string[];
    fallback_available?: string[];
  };
  error?: string;
}
```

Then card detail can show the exact failure instead of only:

```text
runtime_dependency_repair_failed
```

### 3. Add Failure Deduping / Cooling

Before submitting a dependency install job, check recent terminal failures for the same logical request.

Suggested key:

```text
ecosystem + runtime + normalized package set + error_code + requested_package
```

If the same failure exists, return a non-background response:

```json
{
  "ok": false,
  "background": false,
  "error_code": "duplicate_dependency_resolution_failure",
  "prior_job_id": "depjob_...",
  "message": "The same dependency request already failed for this runtime.",
  "retry_hint": "Do not retry conda install; choose fallback or ask user for manual runtime preparation."
}
```

This directly addresses the OAA-2 repeated `pydeseq2` failures.

### 4. Separate Resolution From Installation

Current install flow combines:

```text
validate payload -> resolve package names -> install
```

Split it into:

```text
resolve_runtime_dependencies -> install_runtime_dependencies
```

`resolve_runtime_dependencies` should be allowed to return:

- fully resolvable conda plan;
- partial plan;
- fallback plan;
- manual preparation required;
- source install unsupported;
- ambiguous package name.

`install_runtime_dependencies` should execute only a concrete approved plan.

This reduces failed background jobs caused by predictable resolver misses.

### 5. Keep Manager As Orchestrator

Manager should still decide when dependency repair is needed and when to ask the user.

But package ecosystem decisions should move out of Manager prompt logic and into a structured resolver layer.

Recommended Manager behavior after resolver failure:

```text
1. read structured failure;
2. do not retry identical install;
3. if controlled fallback exists and policy allows it, request fallback plan;
4. otherwise tell user exactly what runtime package/manual setup is needed.
```

## Acceptance Criteria For Future Fix

1. A failed dependency job emits a project event containing `error_code`, `message`, `requested_package`, `attempted_candidates`, and `fallback_available`.
2. Frontend shows a clear dependency failure report without requiring the user to ask Manager.
3. A card blocked by a failed dependency repair shows the exact failed job and package in card detail.
4. Repeating the same impossible conda request does not create another background job.
5. Manager receives a structured "do not retry, choose fallback/manual preparation" response for duplicate terminal failures.
6. Resolver can classify a package list into installable, fallback-required, and manual-required groups before launching a background install.
7. The installer remains backend-controlled and does not become arbitrary shell execution by Manager or an agent.

## Conclusion

The current failure mode is not primarily missing persistence. It is missing failure propagation and resolution policy.

For OAA-2, the backend already knew why the jobs failed, but the frontend did not have a first-class dependency-failure surface. At the same time, the resolver was narrow enough that repeated conda misses became repeated background jobs.

The right direction is to add a structured dependency resolution layer, make terminal dependency failures first-class frontend events, and keep actual installation execution inside backend-controlled tools.
