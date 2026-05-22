# Controlled Executor Runtime Design

## Purpose

Blueprint executors need real local scientific software, but they must not directly mutate Blueprint project state.

For OAA PCA/DEG and similar bioinformatics work, a fully sealed sandbox is too restrictive. Executors may need to:

- run the host conda/R/Python/Node toolchain
- read package files, manuals, CA certificates, DNS resolver state, and CLI runtime files
- use network access for documentation, biological APIs, or agent-model calls
- write run logs, generated scripts, plots, tables, and caches

The runtime boundary is therefore a soft sandbox, not a strong multi-tenant security container. Its job is to make accidental or model-driven writes land in the current run namespace and to keep global environments/project control files read-only.

## Current Decision

Use `bubblewrap` with a host-root-readonly profile plus a reviewer delivery-bundle allowlist.

The two controls solve different problems:

- Soft sandbox: controls where the executor can write and keeps host runtime dependencies available.
- Reviewer allowlist: controls what the reviewer AI may inspect after the run.

The sandbox does not need to hide every host file from the executor. The reviewer also does not need filesystem-wide read access. The reviewer should read only the data, scripts, logs, manifest, and summaries that belong to the current run.

## Runtime Boundary

The executor sees the host filesystem read-only:

```text
/
```

Then Blueprint overlays only the current run's writable paths:

```text
workspace/<project>/runs/<run_id>/
workspace/<project>/results/<card_id>/<run_id>/
workspace/<project>/scripts/generated/<run_id>/
```

Project control paths are masked inside the sandbox:

```text
workspace/<project>/graph/
workspace/<project>/.git/
```

Run-local state is redirected into the run directory:

```text
HOME=runs/<run_id>/home
TMPDIR=runs/<run_id>/tmp
XDG_CACHE_HOME=runs/<run_id>/cache
XDG_CONFIG_HOME=runs/<run_id>/config
XDG_DATA_HOME=runs/<run_id>/data
XDG_STATE_HOME=runs/<run_id>/state/xdg
R_USER_CACHE_DIR=runs/<run_id>/cache/R
MPLCONFIGDIR=runs/<run_id>/cache/matplotlib
PI_CODING_AGENT_DIR=runs/<run_id>/state/pi-agent
PI_CODING_AGENT_SESSION_DIR=runs/<run_id>/state/pi-sessions
```

This means the executor can call existing conda/R/Node tools, but cannot create or modify global conda environments because `/home/solarise/miniconda3` is inherited through the read-only host root unless a future policy explicitly binds a run-local env path writable.

## Reviewer Boundary

Reviewer visibility is based on the current run's delivery bundle, not on raw filesystem access and not on arbitrary manifest claims.

Allowed reviewer inputs:

- selected run metadata: `task_packet.json`, `manifest.json`, `adapter_contract.json`, `manager_brief.json`, `commands.log`, `transcript.md`, `filesystem_audit.json`, `sandbox_plan.json`
- task input assets listed by the backend in `TaskPacket.input_assets`
- created assets whose role/type match `TaskPacket.expected_outputs` and whose path is under the current run's allowed output paths
- code artifacts under `scripts/generated/<run_id>/` or `runs/<run_id>/`

Not allowed:

- `graph/`
- `.git/`
- other runs' files
- generated scripts from other runs
- arbitrary project/backend source files declared by a malicious or confused manifest
- paths outside the project root

This is the key parallelism rule: each executor writes its own run/result/generated-script namespace, and the reviewer only reads that namespace plus backend-declared inputs. Outputs from another executor become visible to future work only after the backend accepts and materializes them into graph state.

## Acceptance Flow

Execution and acceptance are separate:

1. Backend creates `TaskPacket` and run directories.
2. Backend launches the executor through the soft sandbox.
3. Executor writes outputs, generated code, logs, and `manifest.candidate.json`.
4. Wrapper validates/promotes the candidate manifest to `manifest.json`.
5. Backend audits filesystem changes.
6. Backend validates manifest against the task packet.
7. Deterministic validator checks output files and code artifact scope.
8. Reviewer inspects only the delivery bundle through tools.
9. Backend materializes accepted assets, claims, reports, and card state into `graph/`.

Only the backend mutates `graph/`. Executors produce evidence; they do not update project truth directly.

## Network Policy

Current soft-sandbox profile keeps host networking available.

Reason:

- agent executors need model API access
- R/conda documentation lookup can be useful
- some bioinformatics tasks legitimately need external APIs

Network use should be represented in task policy, runtime approvals, transcript, and manifest evidence. We are not using `--unshare-net` in the current profile.

## Implementation Requirements

Runtime:

- `BLUEPRINT_EXECUTOR_SANDBOX_MODE=bwrap`
- `BLUEPRINT_EXECUTOR_HOST_ROOT_READONLY=true`
- deployment must install/check `bubblewrap`
- deployment smoke test must fail hard if `bwrap` cannot run
- bwrap command must use `--clearenv`
- explicitly pass runtime env, adapter env, proxy/CA env, locale, conda, cache, and tool-state keys
- write `runs/<run_id>/sandbox_plan.json`
- agent CLI wrappers must write `runs/<run_id>/agent_trace.json`

Writable paths:

- `runs/<run_id>/`
- `results/<card_id>/<run_id>/`
- `scripts/generated/<run_id>/`

Masked paths:

- project `graph/`
- project `.git/`

Reviewer:

- list and read files only through reviewer tools
- derive the allowlist from task packet plus validated run scope
- never trust manifest paths alone
- reject binary/text decoding problems instead of feeding garbage to the reviewer model

## Agent Behavior Trace

Agent CLI workers write a run-local behavior trace:

```text
runs/<run_id>/agent_trace.json
```

This file is for debugging specialist-card execution speed and failure modes. It records:

- provider and card identity
- wrapper phase: initial provider call, manifest repair calls, completion
- per-attempt start/end time, duration, exit code, stdout line count, BP_EVENT count, and last output lines
- manifest validation attempts and schema errors
- expected output, manifest, manager brief, and generated-script file timeline
- observations such as provider launch failure, non-zero exit, slow attempt, missing manifest, or exhausted repair attempts

The trace intentionally redacts key/token/password style command fragments. It is an execution file for operators and developers; it is not project truth and must not be used to mutate `graph/` directly.

## Non-Goals

This design is not a strong adversarial sandbox. It does not claim to protect host secrets from a malicious local executor, and it does not replace container isolation for untrusted third-party code.

Long term, if Blueprint needs true tenant isolation or reproducible package stacks, use rootless Podman/Docker images or a stronger runtime such as gVisor/Kata/Firecracker. For the current development and OAA analysis workflow, the soft sandbox is intended to solve runtime compatibility, global environment protection, graph write protection, and parallel review scoping.
