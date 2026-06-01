# Runtime Dependency Resolver Failure Reporting Review

Status: refined implementation plan.

Date: 2026-06-01

Last refined: 2026-06-02

Repository note: as of this refinement, this is the highest numbered review document under `docs/`. There is no `docs/42_*` review document yet.

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

## Refined Implementation Plan

The fix should be split into two delivery layers:

1. **P0 reporting and retry control**: make existing failed jobs visible and stop duplicate impossible retries.
2. **P1 resolver/installer separation**: add a structured resolution plan before any background installation job is created.

P0 is required for OAA-2 stability. P1 is the longer-term product boundary that prevents conda-only misses from becoming repeated failed jobs.

### P0.1 Normalize Runtime Dependency Failure Details

Add a small backend helper so every consumer receives the same failure shape.

Suggested location:

```text
backend/app/services/runtime_dependency_state_service.py
```

Suggested helper:

```python
def runtime_dependency_failure_details(job: Mapping[str, Any] | Any) -> dict[str, Any]:
    ...
```

Do not import `RuntimeDependencyJobService` into `runtime_dependency_state_service.py`. The helper should accept either a persisted job dictionary or a duck-typed in-memory job object to avoid service-layer circular imports.

It should derive:

- `job_id`;
- `task_id`;
- `status`;
- `runtime`;
- `resolved_runtime`;
- `ecosystem`;
- `manager`;
- `packages`;
- `card_id`;
- `run_id`;
- `session_id`;
- `ok`;
- `error_code`;
- `message`;
- `requested_package`;
- `attempted_candidates`;
- `fallback_available`;
- `stdout_tail`;
- `stderr_tail`;
- `dedupe_key`;
- `retry_hint`;
- `created_at`;
- `started_at`;
- `finished_at`.

`retry_hint` should be deterministic, not prompt-only. Recommended mapping:

```text
package_not_found_in_conda_channels
  -> do_not_retry_same_conda_request; choose fallback/manual preparation

github_source_install_not_supported / external_source_install_not_supported
  -> do_not_retry_installer; use explicit environment-preparation workflow

dependency_install_timeout
  -> retry_allowed_after_runtime_check

dependency_install_start_failed
  -> manual_runtime_preparation_required; inspect runtime path and environment existence

dependency_install_compilation_failed
  -> manual_system_dependency_or_runtime_preparation_required

dependency_install_failed
  -> inspect stderr_tail before retry
```

This helper prevents drift between project events, work order blockers, workboard items, API responses, and Manager tool results.

`GET /projects/{project_id}/runtime-dependency-jobs/{job_id}` should also reuse this helper for top-level normalized fields while preserving the existing raw `payload`, `result`, and `error` fields for audit compatibility.

### P0.2 Enrich Project Events With Terminal Failure Details

Update:

```text
backend/app/services/runtime_dependency_job_service.py
```

`RuntimeDependencyJobService._emit_project_event(...)` should include normalized details when the job is terminal, especially when `status == "failed"`.

Minimum failed event payload:

```json
{
  "task_id": "bgtask_...",
  "job_id": "depjob_...",
  "job_status": "failed",
  "runtime": "python_env",
  "resolved_runtime": "/path/to/env",
  "ecosystem": "python",
  "packages": ["pydeseq2"],
  "manager": "conda",
  "ok": false,
  "error_code": "package_not_found_in_conda_channels",
  "message": "Package pydeseq2 was not found in conda channels.",
  "requested_package": "pydeseq2",
  "attempted_candidates": ["pydeseq2"],
  "fallback_available": ["pip"],
  "retry_hint": "do_not_retry_same_conda_request",
  "card_id": "card_...",
  "run_id": "run_...",
  "dedupe_key": "dep:python:python_env:pydeseq2:package_not_found_in_conda_channels:pydeseq2",
  "started_at": "...",
  "finished_at": "..."
}
```

Keep payload tails bounded:

```text
stdout_tail <= 2KB or 50 lines, whichever comes first
stderr_tail <= 2KB or 50 lines, whichever comes first
```

If either tail is truncated, include:

```json
{
  "truncated": true,
  "full_log_job_id": "depjob_..."
}
```

Do not emit full logs into project events. The full detail endpoint remains the audit path and should reuse the existing project read authorization model.

### P0.3 Expose Runtime Dependency Blockers In Work Orders

Backend already builds `runtime_dependency_blocker` in `FlowService`, but the payload is too thin and frontend types ignore it.

Update:

```text
backend/app/services/runtime_dependency_state_service.py
backend/app/services/flow_service.py
frontend/lib/types.ts
frontend/components/detail/CardDetailPanel.tsx
```

Backend `runtime_dependency_blocker` should include normalized failure details:

```json
{
  "job_id": "depjob_...",
  "task_id": "bgtask_...",
  "status": "failed",
  "runtime": "python_env",
  "ecosystem": "python",
  "packages": ["pydeseq2"],
  "run_id": "run_...",
  "session_id": "session_...",
  "error_code": "package_not_found_in_conda_channels",
  "message": "Package pydeseq2 was not found in conda channels.",
  "requested_package": "pydeseq2",
  "attempted_candidates": ["pydeseq2"],
  "fallback_available": ["pip"],
  "retry_hint": "do_not_retry_same_conda_request",
  "dedupe_key": "dep:python:python_env:pydeseq2:package_not_found_in_conda_channels:pydeseq2"
}
```

Frontend `WorkItem` should add:

```ts
export interface RuntimeDependencyBlocker {
  job_id: string;
  task_id?: string;
  status: string;
  runtime: string;
  ecosystem?: "python" | "R" | string | null;
  packages: string[];
  run_id?: string | null;
  session_id?: string | null;
  error_code?: string | null;
  message?: string | null;
  requested_package?: string | null;
  attempted_candidates?: string[] | null;
  fallback_available?: string[] | null;
  retry_hint?: string | null;
  dedupe_key?: string | null;
  error?: string | null;
}
```

`CardDetailPanel` should display a compact dependency failure block when `workItem.runtime_dependency_blocker?.status === "failed"`:

- failed package;
- runtime;
- attempted candidates;
- fallback families;
- retry hint;
- link/action to expand job detail.

For display copy:

- Python `attempted_candidates` can be labeled "Package tried".
- R `attempted_candidates` should be labeled "Conda name variants tried" because values such as `r-limma` and `bioconductor-limma` are backend-generated conda candidates, not necessarily user-requested package names.

The UI should map retry hints to concrete actions:

| `retry_hint` | UI action |
| --- | --- |
| `do_not_retry_same_conda_request` | Open runtime detail / edit package list |
| `manual_preparation_required` | Mark manually resolved |
| `manual_runtime_preparation_required` | Open runtime settings / mark manually resolved |
| `choose_fallback` | Try fallback installer only when P1 fallback policy allows it |
| `retry_allowed_after_runtime_check` | Retry after checking runtime availability |
| `inspect_stderr` | View stderr tail / lazy fetch job detail |

The generic `block_reasons` row should remain, but it must not be the only user-facing explanation.

### P0.4 Add A Frontend Project-Level Failure Notice

Update:

```text
frontend/components/layout/ProjectWorkspace.tsx
```

When an event arrives with:

```text
reason == "runtime_dependency_job_changed"
payload.job_status == "failed"
```

the frontend should:

1. refetch project/work order as it does today;
2. show a persistent notice such as:

```text
Dependency install failed: pydeseq2 was not found in configured conda channels. Fallback available: pip.
```

3. select or link the affected card if `card_id` is present;
4. allow opening job detail through `getRuntimeDependencyJob(projectId, jobId)`.

Use event payload for the immediate notice. Fetch the detail endpoint only when the user expands details, to avoid excessive requests on high-frequency project events.

### P0.5 Enforce Duplicate Failure Cooling Before Job Submission

Update:

```text
backend/app/services/runtime_dependency_job_service.py
backend/app/services/manager_blueprint_tools.py
```

Cooling must happen before `RuntimeDependencyJobService.submit(...)` creates a new background task.

Important implementation detail: current `RuntimeDependencyJobService.submit(...)` creates a `BackgroundTask` immediately. Therefore duplicate detection cannot be added only after entering `submit(...)` unless `submit(...)` is refactored into a preflight path. The safer first implementation is:

```text
ManagerBlueprintTools.install_runtime_dependencies
  -> validate payload
  -> runtime_dependency_job_service.find_duplicate_terminal_failure(...)
  -> return duplicate response OR call submit(...)
```

`RuntimeDependencyJobService` can own the duplicate lookup helper, but job creation must remain after the duplicate check.

Cooling scope is project-scoped, not session-scoped. If any session in the same project has already produced the same non-retryable failure for the same runtime/package set, later sessions should also be cooled.

The lookup must read persisted `chat/runtime_dependency_jobs.json`, not only the in-memory `self.jobs` table. `self.jobs` is reconstructed after backend restart, so a restart must not reset duplicate failure cooling.

Also add in-flight dedupe before terminal failure cooling:

```text
status in {"queued", "launching", "running", "waiting"}
and same (project_id, ecosystem, runtime, normalized_packages)
  -> duplicate_dependency_resolution_in_progress
```

In-flight duplicate response:

```json
{
  "ok": false,
  "background": false,
  "error_code": "duplicate_dependency_resolution_in_progress",
  "prior_job_id": "depjob_...",
  "message": "The same dependency installation is already running for this runtime.",
  "retry_hint": "wait_for_existing_dependency_job"
}
```

This is distinct from `runtime_locks`: the existing lock serializes execution after job creation, but it does not prevent multiple duplicate background tasks from being created.

Suggested key:

```text
dep:{ecosystem}:{runtime}:{normalized_packages}:{error_code}:{requested_package}
```

Normalization rules:

- trim package names;
- lower-case Python package names for key comparison;
- keep R package names case-sensitive for display but lower-case in the key;
- remove duplicates while preserving request order for display;
- sort the normalized package set for the key;
- include `requested_package` when a prior resolver identified the exact failing member;
- include `error_code`, because timeout and package-not-found should not cool each other.

Admission behavior:

1. Validate payload.
2. Compute a request key without `error_code`.
3. Load recent terminal jobs for the same project/runtime/ecosystem/package set.
4. If a failed job has a deterministic non-retryable error code, return a non-background duplicate response.

Duplicate response:

```json
{
  "ok": false,
  "background": false,
  "error_code": "duplicate_dependency_resolution_failure",
  "prior_job_id": "depjob_...",
  "prior_error_code": "package_not_found_in_conda_channels",
  "dedupe_key": "dep:python:python_env:pydeseq2:package_not_found_in_conda_channels:pydeseq2",
  "message": "The same dependency request already failed for this runtime.",
  "retry_hint": "do_not_retry_same_conda_request; modify package list, change runtime, or mark manually resolved",
  "fallback_available": ["pip"]
}
```

The duplicate response should be visible in the conversation audit trail. Recommended behavior:

- return the structured non-background tool result to Manager;
- have Manager summarize it in the current assistant turn;
- persist the assistant turn normally as a chat session message;
- optionally add a timeline item with `kind="tool"` and `status="error"` for the rejected install attempt.

Do not create a background task or dependency job for the duplicate response.

Add structured observability on every dedupe rejection:

```python
logger.info(
    "dep_cooling_rejected",
    extra={
        "dedupe_key": dedupe_key,
        "error_code": error_code,
        "kind": "terminal",  # or "in_flight"
        "project_id": project_id,
        "runtime": runtime,
    },
)
```

This is needed to identify hot packages, dominant error codes, and repeated project/runtime failure patterns after P0 lands.

Do not cool these cases by default:

- `dependency_install_timeout`;
- backend interruption/restart failures;
- user explicitly changes package list;
- user explicitly changes runtime;
- user explicitly chooses a different approved installer plan after P1 exists.

This directly addresses OAA-2 repeated `pydeseq2` and R package misses.

### P0.6 Keep Workboard Coalescing Aligned With Job Cooling

Doc 39 introduced dependency failure coalescing at the workboard layer. Doc 41 should use the same semantic key shape where possible.

Expected alignment:

```text
workboard payload.coalescing_key == runtime dependency failure dedupe key or a stable prefix of it
```

If the workboard key remains less detailed, it should at least include:

- ecosystem;
- runtime;
- normalized package set;
- error_code;
- requested_package.

This avoids a split-brain condition where backend job admission suppresses retries but workboard still treats the same failure as new wake fuel.

### P0.7 Define How Failed Dependency Blockers Are Cleared

`FlowService` treats failed runtime dependency jobs as blockers. That is correct while the dependency is unresolved, but the design needs an explicit unblock path.

Accepted clearing conditions:

- a newer dependency job for the same card/runtime succeeds;
- the card is revised to no longer require that runtime/package set;
- the user or Manager records an explicit manual-preparation acknowledgement after the runtime has been fixed outside the installer.

Do not clear a blocker merely because the user dismisses a frontend notice. Dismissal is UI-only. It must not affect scheduling.

Recommended backend action for manual preparation:

```text
POST /projects/{project_id}/runtime-dependency-jobs/{job_id}/mark-resolved
```

or an equivalent Manager tool:

```text
mark_runtime_dependency_prepared
```

The persisted record should keep the original failed result and add:

```json
{
  "resolution_status": "manually_resolved",
  "resolved_at": "...",
  "resolved_by_session_id": "session_...",
  "resolution_message": "User confirmed the runtime package was installed manually."
}
```

`dependency_blockers_by_card(...)` should ignore failed jobs with `resolution_status == "manually_resolved"` only if no newer failed job exists for the same card/runtime/package set.

Permission model:

- request body must include `session_id`;
- only the auto owner session may mark a blocker resolved while auto is enabled for that project;
- btw sessions should receive `409` unless an explicit admin path is added later;
- direct REST calls should reuse project write authorization and still record `resolved_by_session_id`.

Auto mode linkage:

- after a successful mark-resolved, if auto is enabled and `owner_session_id` is present, call `ManagerAutoService.evaluate_workboard_and_maybe_signal(...)`;
- this lets auto continue after the user or Manager confirms manual runtime preparation.

Chat audit:

- mark-resolved should also append a chat session message in the owner session;
- use the same operational-message style as `/auto` command acknowledgements;
- recommended timeline item: `kind="command"`, `status="done"`, content summarizing the job id, package/runtime, and manual-resolution note;
- this ensures later Manager turns can see that the blocker was manually resolved without inferring it only from hidden job metadata.

This avoids permanent blocking after legitimate manual runtime preparation while preserving the original failure audit trail.

### P1.1 Introduce A Resolver Plan Model

Add a deterministic resolver service before introducing any optional agent:

```text
backend/app/services/runtime_dependency_resolver_service.py
```

Core API:

```python
class RuntimeDependencyResolverService:
    def resolve(self, project_id: str, payload: dict[str, Any]) -> RuntimeDependencyResolutionPlan:
        ...
```

Plan shape:

```json
{
  "ok": false,
  "ecosystem": "R",
  "runtime": "R_env",
  "packages": [
    {
      "name": "limma",
      "normalized_name": "limma",
      "classification": "bioconductor",
      "conda_candidates": ["r-limma", "bioconductor-limma"],
      "conda_match": null,
      "fallback_available": ["bioconductor"],
      "status": "fallback_required",
      "message": "Not found in configured conda channels."
    },
    {
      "name": "ggplot2",
      "normalized_name": "ggplot2",
      "classification": "cran",
      "conda_candidates": ["r-ggplot2", "bioconductor-ggplot2"],
      "conda_match": "r-ggplot2",
      "fallback_available": ["cran"],
      "status": "conda_installable"
    }
  ],
  "recommended_actions": [
    {
      "kind": "install",
      "installer": "conda",
      "packages": ["r-ggplot2"]
    },
    {
      "kind": "manual_preparation_required",
      "packages": ["limma"],
      "reason": "package_not_found_in_configured_channels"
    }
  ],
  "error_code": "partial_resolution_requires_manual_preparation",
  "message": "Some packages are not installable through the configured conda channels."
}
```

The initial resolver can stay conservative:

- it may only probe configured conda channels;
- it may classify fallback families as guidance, not execution;
- it must inspect every package in the request before returning failure;
- it must not run arbitrary shell commands beyond existing bounded package search commands.

The resolver should cache package probe results in memory with a short TTL to avoid repeated slow `mamba repoquery search` / `conda search --json` calls:

```text
cache key: ecosystem + channel_set + package
default TTL: 1 hour
```

The cache can be in-memory only; losing it on backend restart is acceptable.

Resolver statuses must map to P0 normalized fields so project events, workboard, UI, and Manager do not need a second vocabulary:

| Resolver status | P0 `error_code` | P0 `retry_hint` |
| --- | --- | --- |
| `conda_installable` | none | none |
| `fallback_required` | `package_not_found_in_conda_channels` | `choose_fallback` |
| `manual_preparation_required` | `manual_preparation_required` | `manual_preparation_required` |
| `source_install_required` | `github_source_install_not_supported` or `external_source_install_not_supported` | `do_not_retry_installer` |
| `runtime_missing` | `dependency_install_start_failed` | `manual_runtime_preparation_required` |
| `unknown` | `dependency_resolution_unknown` | `inspect_stderr` |

### P1.2 Split Manager Tools Into Resolve And Install

Current tool:

```text
install_runtime_dependencies
```

should remain for compatibility, but internally it should call the resolver first.

Add a new Manager-visible tool:

```text
resolve_runtime_dependencies
```

Behavior:

- returns the plan without creating a background job;
- includes `dedupe_key` and prior failure if applicable;
- tells Manager whether install is safe, partial, blocked, or manual.

Updated `install_runtime_dependencies` behavior:

1. validate payload;
2. check duplicate cooling;
3. resolve plan;
4. if plan is fully installable through the currently allowed backend policy, submit a background job;
5. if plan is partial/manual/fallback-only, return a non-background structured failure.

This preserves backend control over actual installation and avoids turning Manager into an installer.

Manager-visible plan summary format:

```json
{
  "ok": false,
  "tool": "resolve_runtime_dependencies",
  "runtime": "R_env",
  "ecosystem": "R",
  "status": "partial_resolution_requires_manual_preparation",
  "message": "Some packages are not installable through the configured conda channels.",
  "installable": [
    {"name": "ggplot2", "installer": "conda", "candidate": "r-ggplot2"}
  ],
  "blocked": [
    {
      "name": "limma",
      "reason": "package_not_found_in_conda_channels",
      "attempted_candidates": ["r-limma", "bioconductor-limma"],
      "fallback_available": ["bioconductor"],
      "recommended_action": "manual_preparation_or_policy_approved_fallback"
    }
  ],
  "recommended_actions": [
    "Do not call install_runtime_dependencies for blocked packages.",
    "Ask for manual runtime preparation or an approved fallback policy."
  ]
}
```

The summary should front-load `status`, `blocked`, and `recommended_actions` so Manager can follow the resolver result without reinterpreting raw package probe details.

### P1.3 Controlled Fallback Policy

Do not automatically execute pip/CRAN/Bioconductor just because `fallback_available` exists.

Add a policy switch before enabling fallback execution:

```text
runtime_dependency_fallback_policy = "report_only" | "allow_safe_registry_install"
```

Storage:

- default value should live in backend runtime config/app config;
- deploy may inject it from backend env;
- per-project override can later live under `graph.metadata.dependency_policy`;
- P1.3 should initially implement only deploy/runtime-level policy, leaving per-project override for a later iteration.

Initial default:

```text
report_only
```

When set to `report_only`, fallback information is surfaced to Manager and frontend, but no fallback installer is executed.

When set to `allow_safe_registry_install`, only structured registry installs are allowed:

- `pip install <validated package>` for Python registry packages;
- `install.packages(...)` for CRAN;
- `BiocManager::install(...)` for Bioconductor.

Still reject:

- GitHub URLs;
- arbitrary tarballs;
- shell snippets;
- system package manager commands;
- package specs containing unsupported flags.

### P1.4 Manager-Agent Prompt And Tool Result Contract

Update:

```text
manager-agent/src/server.js
```

Manager must treat these backend responses as hard control signals:

- `duplicate_dependency_resolution_failure`;
- `partial_resolution_requires_manual_preparation`;
- `package_not_found_in_conda_channels`;
- `github_source_install_not_supported`;
- `external_source_install_not_supported`.

Required behavior:

```text
Do not retry install_runtime_dependencies with the same package/runtime.
If fallback is only report_available, tell the user the exact manual preparation or ask for approval of an explicit fallback policy.
If resolver returns a concrete approved install action, run install_runtime_dependencies once, then stop at the async boundary.
```

Prompt hints are not sufficient by themselves; backend duplicate cooling remains mandatory.

## Implementation Order

1. Add normalized failure detail helper and unit tests.
2. Enrich `runtime_dependency_job_changed` project events.
3. Expand backend work order `runtime_dependency_blocker` payload.
4. Add frontend `RuntimeDependencyBlocker` type and card detail rendering.
5. Add project-level failed dependency notice and lazy job-detail expansion.
6. Add in-flight duplicate suppression before background task creation.
7. Add terminal failure cooling from persisted job history before background task creation.
8. Align workboard dependency coalescing key with the job dedupe key.
9. Add explicit failed dependency blocker clearing semantics and auto wake re-evaluation.
10. Add resolver plan model and deterministic resolver service.
11. Add `resolve_runtime_dependencies` tool and make `install_runtime_dependencies` resolver-first.
12. Add optional fallback policy switch, defaulting to `report_only`.

Steps 1-9 are the OAA-2 stabilization patch. Steps 10-12 can be a follow-up PR.

## Test Plan

### Backend Unit Tests

Add tests for:

- normalized failure detail extraction from persisted job dict;
- normalized failure detail extraction from in-memory `RuntimeDependencyJob`;
- retry hint mapping for `package_not_found_in_conda_channels`;
- retry hint mapping for source-install unsupported errors;
- retry hint mapping for `dependency_install_start_failed`;
- dedupe key normalization for package order and duplicate package names;
- in-flight dedupe key normalization without terminal `error_code`;
- no cooling for timeout/interrupted failures.

### Backend Integration Tests

Add tests for:

- failed dependency job event includes `error_code`, `message`, `requested_package`, `attempted_candidates`, `fallback_available`, `retry_hint`, and `dedupe_key`;
- `FlowService.get_work_order(...)` includes a full `runtime_dependency_blocker` for failed jobs;
- duplicate in-flight same runtime/package request returns `duplicate_dependency_resolution_in_progress` and does not create a background task;
- repeated same `pydeseq2` conda miss returns `duplicate_dependency_resolution_failure` and does not create a new background task;
- backend restart does not reset terminal failure cooling because the lookup reads persisted `runtime_dependency_jobs.json`;
- same package set on a different runtime is not deduped;
- same runtime with a changed package set is not deduped;
- same package set from a different session in the same project is deduped because cooling is project-scoped;
- project event `stdout_tail` / `stderr_tail` are truncated to the documented limit and include `truncated` metadata when needed;
- workboard `runtime_dependency_install_failed` item uses a coalescing key aligned with backend dedupe;
- failed dependency blocker is cleared by a newer successful job for the same card/runtime;
- failed dependency blocker is cleared by explicit manual-resolution metadata but not by frontend notice dismissal.
- mark-resolved from a btw session is rejected while auto is enabled for another owner session;
- mark-resolved triggers workboard evaluation when auto is enabled and an owner session exists.

### Frontend Tests Or Build Checks

Add coverage where the project currently supports it:

- `WorkItem` accepts `runtime_dependency_blocker`;
- card detail renders failed package, attempted candidates, fallback families, and retry hint;
- card detail maps retry hints to concrete actions, including mark manually resolved;
- project event handler shows a dependency failure notice for failed terminal events;
- job-detail expansion uses the detail endpoint and does not load full logs from project events;
- `npm run build` passes.

### Manager-Agent Checks

Add or update checks for:

- `install_runtime_dependencies` duplicate failure response is summarized as non-retryable;
- `duplicate_dependency_resolution_in_progress` is summarized as wait-for-existing-job, not as a new failure;
- Manager does not call status polling immediately after starting a background dependency job;
- Manager does not retry identical package/runtime after duplicate failure response.

## Acceptance Criteria For Future Fix

1. A failed dependency job emits a project event containing `error_code`, `message`, `requested_package`, `attempted_candidates`, `fallback_available`, `retry_hint`, and `dedupe_key`.
2. Frontend shows a clear dependency failure report without requiring the user to ask Manager.
3. A card blocked by a failed dependency repair shows the exact failed job, failed package, attempted candidates, and fallback families in card detail.
4. Repeating the same in-flight dependency request returns `duplicate_dependency_resolution_in_progress` and does not create another background task.
5. Repeating the same impossible conda request returns `duplicate_dependency_resolution_failure` and does not create another background task or dependency job, even after backend restart.
6. Manager receives duplicate responses and treats in-flight duplicates as wait states and terminal duplicates as non-retryable failures.
7. Workboard dependency failure coalescing and backend dependency job cooling use compatible semantic keys.
8. A failed runtime dependency blocker has an auditable clearing path after a newer success, card revision, or explicit manual-preparation acknowledgement.
9. Manual-resolution acknowledgement enforces session ownership and triggers auto workboard evaluation when auto is enabled.
10. Resolver can classify a package list into installable, fallback-required, and manual-required groups before launching a background install.
11. The installer remains backend-controlled and does not become arbitrary shell execution by Manager or an agent.

## Conclusion

The current failure mode is not primarily missing persistence. It is missing failure propagation and resolution policy.

For OAA-2, the backend already knew why the jobs failed, but the frontend did not have a first-class dependency-failure surface. At the same time, the resolver was narrow enough that repeated conda misses became repeated background jobs.

The right direction is to add a structured dependency resolution layer, make terminal dependency failures first-class frontend events, and keep actual installation execution inside backend-controlled tools.
