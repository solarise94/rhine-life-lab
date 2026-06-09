# 51-1 Release Installer Credential Gate Follow-up

## Background

The user-mode release installer path reached managed deploy during Podman
smoke testing. At that point, deployment was blocked by a hard requirement in
`scripts/deploy_release.sh`:

- `BLUEPRINT_DEEPSEEK_API_KEY` must be present

This gate made sense in the earlier model where the installer was expected to
materialize all runtime credentials into generated env files. That assumption
is no longer universally true.

Implementation status: this hard install-time gate has since been removed.
Provider credentials are now runtime requirements for provider-backed features,
not release/user-mode install prerequisites.

The current product direction allows two different credential sources:

- credentials explicitly provided to the installer/deploy flow
- credentials already managed at the system or user environment level

Under that model, deployment should not fail merely because a specific key was
not passed through the installer invocation.

## Problem Statement

`deploy_release.sh` previously treated `BLUEPRINT_DEEPSEEK_API_KEY` as a hard
deployment prerequisite and exited before service generation if the variable
was absent. The hard stop was the explicit `die` at the historical
`scripts/deploy_release.sh:491-492` location.

This is too strict for the current architecture because:

- manager credentials may already be injected by the user or host environment
- executor credentials may also come from external configuration
- generated env files are not the only valid credential source anymore

As a result, the old install/deploy behavior conflated:

- "the installer was not given a key"
- "the runtime will definitely have no credential available"

Those are not equivalent.

## Desired Behavior

The release installer/deploy path should:

- allow deployment to continue when a manager/provider key is not explicitly
  passed in
- only write provider key values into generated env files when they are set
- warn when a key was not provided, instead of failing the deploy immediately
- leave the final "credential actually missing" failure to runtime startup or
  first provider use, where the real credential source is known

This keeps the installer aligned with the productized runtime model rather than
the older "installer owns all secrets" model.

## Current Hard Gates Identified

Based on the installer/deploy design and smoke-test results at the time of
this follow-up, these were the credential-related gates in the install path
that needed to be distinguished:

### 1. Release Installer Path: Actual Blocking Point

Files:

- `scripts/install.sh:754`
- `scripts/deploy_release.sh:491-492`
- `scripts/deploy_release.sh:527`
- `scripts/deploy_release.sh:589`

Current behavior:

- the self-extracting installer itself did not fail early on a missing provider
  key
- `scripts/install.sh` reached Phase 10 and then invoked release deploy via
  `run_deploy` at `scripts/install.sh:754`
- the actual install-time hard failure happened inside
  `scripts/deploy_release.sh:491-492`
- after that, both generated env files assumed the key was always present because
  it is unconditionally written to:
  - `backend.env` at `scripts/deploy_release.sh:527`
  - `manager-agent.env` at `scripts/deploy_release.sh:589`

Recommended disposition:

- convert from hard blocker to warning
- only write `BLUEPRINT_DEEPSEEK_API_KEY` when explicitly set

### 2. Legacy Managed Install Bootstrap

Files:

- `scripts/install_blueprint_re.sh:221-222`
- `scripts/install_blueprint_re.sh:241`
- `scripts/install_blueprint_re.sh:297-301`
- `scripts/deploy_user_systemd.sh:402-405`

Current behavior:

- the legacy interactive installer explicitly labels the key as required at
  `scripts/install_blueprint_re.sh:221-222`
- it prompts for `DeepSeek API key (required)` at
  `scripts/install_blueprint_re.sh:241`
- it then hard-fails before deploy at
  `scripts/install_blueprint_re.sh:297-301`
- the legacy deploy path independently hard-fails on the same condition at
  `scripts/deploy_user_systemd.sh:402-405`

Recommended disposition:

- keep this behavior only if that path remains explicitly "legacy/operator
  bootstrap"
- if it is still presented as a product install path, align it with the
  release installer behavior

### 3. Runtime-Level Credential Requirements That Are Not Installer Gates

Files:

- `manager-agent/src/server.js:95-97`
- `manager-agent/src/server.js:3201-3205`
- `manager-agent/src/server.js:3537-3540`
- `scripts/blueprint_pi_launch.sh:9-15`

Current behavior:

- manager-agent startup validation reports missing credentials at
  `manager-agent/src/server.js:95-97`
- manager-agent still throws at actual LLM execution time if no runtime key is
  available:
  - auto mode at `manager-agent/src/server.js:3201-3205`
  - manual compaction at `manager-agent/src/server.js:3537-3540`
- the pi launcher also requires a DeepSeek key when
  `BLUEPRINT_AUTH_MODE=project_api`, at `scripts/blueprint_pi_launch.sh:9-15`

Recommended disposition:

- keep these as runtime requirements for provider-backed features
- do not treat them as reasons for installer failure before services are
  generated

### 4. Optional Or Generated Secrets That Are Not Current Install Blockers

Files:

- `scripts/deploy_release.sh:397-401`
- `scripts/deploy_release.sh:543`
- `scripts/deploy_release.sh:590`
- `scripts/deploy_release.sh:592`

Current behavior:

- `BLUEPRINT_INTERNAL_TOOL_TOKEN` is auto-generated if absent, rather than
  treated as a required input
- `TAVILY_API_KEY` is written as optional manager-agent configuration and is
  not a hard deploy blocker

Recommended disposition:

- keep `BLUEPRINT_INTERNAL_TOOL_TOKEN` as generated-by-default
- keep `TAVILY_API_KEY` optional

### 5. Smoke Scripts

File:

- `scripts/smoke_manager_sidecar.sh:29-30`

Current behavior:

- requires `BLUEPRINT_DEEPSEEK_API_KEY` because it is a live provider smoke,
  not because the product installer requires it

Recommended disposition:

- keep as-is if the smoke is intentionally a live provider integration smoke
- do not treat this as product installer policy

## Design Gaps To Resolve Before Implementation

### Gap 1. Manager-Agent Is Still Provider-Locked At Service Startup

Files:

- `scripts/deploy_release.sh:586`
- `manager-agent/src/server.js:95-97`
- `scripts/install.sh:198-230`

Current issue:

- release deploy currently writes `MANAGER_AGENT_PROVIDER=deepseek` at
  `scripts/deploy_release.sh:586`
- manager-agent startup validation still treats missing
  `MANAGER_AGENT_API_KEY` or `BLUEPRINT_DEEPSEEK_API_KEY` as an error at
  `manager-agent/src/server.js:95-97`
- installer success today only depends on `wait_for_health`, which checks
  backend and nginx only at `scripts/install.sh:198-230`

Implication:

- simply converting deploy-time key failure into a warning does not mean the
  runtime becomes healthy
- under the current design, manager-agent may fail to start while the installer
  still reports success, because manager-agent is outside the current health
  contract

Required policy choice:

- either explicitly accept manager-agent degraded startup when no provider
  credential was installer-supplied
- or expand installer health coverage to include manager-agent and report that
  state as a warning/degraded result rather than silent success

Recommended documentation outcome:

- make the degraded-manager-agent behavior explicit for no-key installs
- state that missing provider credentials must not block unit generation, but
  may block manager-agent readiness until runtime credentials are supplied

### Gap 2. Upgrade Credential Retention Is Not Defined

Files:

- `scripts/deploy_release.sh:514`
- `scripts/deploy_release.sh:527`
- `scripts/deploy_release.sh:583`
- `scripts/deploy_release.sh:589`

Current issue:

- upgrade deploy rewrites `backend.env` from scratch starting at
  `scripts/deploy_release.sh:514`
- it also rewrites `manager-agent.env` from scratch starting at
  `scripts/deploy_release.sh:583`
- if the future design changes `BLUEPRINT_DEEPSEEK_API_KEY` to "write only when
  set", then an upgrade launched without that env var would drop previously
  persisted credentials from both files

Implication:

- this is not just a fresh-install behavior change
- it can become an upgrade regression that strips working credentials from an
  existing installation

Required policy choice:

- upgrade mode should preserve existing credential lines when the caller does
  not provide replacement values
- or upgrade mode should fail/warn explicitly that credentials will be removed
  unless re-provided

Recommended documentation outcome:

- define upgrade credential retention separately from fresh-install behavior
- prefer preserving prior managed env credentials unless the operator
  explicitly clears them

### Gap 3. Podman Smoke Success Criteria Need Concrete End States

Files:

- `scripts/install.sh:754`
- `scripts/install.sh:198-230`
- `manager-agent/src/server.js:95-97`

Current issue:

- the document currently says "no-key deploy should not fail only because the
  key is absent" and "keyed deploy should go farther"
- but the installer's current success gate is still `wait_for_health`, which
  only checks backend and nginx at `scripts/install.sh:198-230`
- that means the difference between the two cases is not backend health; it is
  manager-agent readiness and/or a real provider-backed request

Recommended explicit expectations:

1. no-key smoke
   expected end state:
   installer completes without failing at `scripts/install.sh:754`, backend
   `/healthz` is healthy, nginx is reachable, and manager-agent may be degraded
   if that degraded mode is the chosen policy

2. keyed smoke
   expected end state:
   same as above, plus manager-agent reaches active/healthy state and at least
   one real provider-backed request succeeds

### Additional Guardrail

The "should not add new credential gates" rule should also cover:

- systemd unit generation itself

Missing provider credentials should not block writing unit files or env files.
At most they should affect post-start readiness, degraded status, or the first
provider-backed request.

## Gates That Should Not Be Added

The release installer should continue to avoid hard credential gates in these
areas:

- downloader bootstrap
- self-extracting payload validation
- runtime environment creation
- `bwrap` smoke testing
- release unpack and symlink switching
- systemd unit file generation

Code-path check:

- `scripts/install.sh:237-754` contains host checks, payload validation,
  micromamba bootstrap, conda env creation, wheel install, release copy, and
  deploy handoff
- within that path, no separate provider-key hard stop exists before the final
  call into `deploy_release.sh`

Those stages should depend on host capability and artifact integrity, not on
provider credentials.

## Recommended Refactor

### Release Deploy

In `scripts/deploy_release.sh`:

- remove the hard `die` on missing `BLUEPRINT_DEEPSEEK_API_KEY`
- emit a deploy warning instead
- write `BLUEPRINT_DEEPSEEK_API_KEY` into `backend.env` only when set
- write the corresponding manager-agent key only when set

### Env Generation Contract

Generated env files should follow this rule:

- required runtime settings are always written
- credentials are written only when explicitly provided
- in upgrade mode, previously persisted credential lines are preserved unless
  the caller explicitly provides replacement values or explicitly clears them
  (see Gap 2)
- absence of credentials is not treated as installer failure by default

### Smoke Coverage

Update Podman smoke strategy so both of these cases are testable:

1. deploy without explicit key
   expected end state:
   installer completes past `run_deploy`, backend `/healthz` is healthy, nginx
   is reachable, and manager-agent may be degraded under the chosen Gap 1
   policy. There is no install-time hard failure purely because the installer
   invocation did not include a key.

2. deploy with explicit test key
   expected end state:
   same as above, plus manager-agent reaches active/healthy state and at least
   one real provider-backed request succeeds

## Why This Matters

The release installer is now close to a true end-to-end product install path.
At this stage, overly strict credential gates create false negatives in smoke
tests and unnecessary friction in real installs.

The installer should own:

- artifact integrity
- runtime bootstrap
- service generation
- host capability diagnostics

It should not assume it is the only valid source of provider credentials.

## Status

This is a follow-up design fix note only.

Code has not yet been updated in this document step.
