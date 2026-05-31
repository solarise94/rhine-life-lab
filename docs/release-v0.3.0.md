# Blueprint RE v0.3.0

Release date: 2026-05-31

## Summary

`v0.3.0` is a stability-focused release. It is intentionally positioned as a larger bug-fix release rather than a feature-complete milestone.

This release fixes a broad set of blocking and high-friction issues across Manager auto/workboard control flow, async boundary handling, background task supervision, dependency installation, executor terminal reporting, and frontend state refresh behavior.

The result is not that every known issue is gone. The result is that the system now hits substantially fewer blocking bugs in normal project execution, especially around:

- Manager yielding after background starts instead of falling into foreground polling loops
- workboard-driven background continuation and wake behavior
- dependency installation fallback and structured failure handling
- manual versus Manager-run concurrency semantics
- frontend refresh and auto-state visibility
- executor completion/failure reporting contracts

## What Improved

- Unified more background progression around workboard reevaluation instead of ad hoc terminal wake paths.
- Reduced async-boundary and polling-loop failure modes after `start_card_run`, `rerun_card`, and dependency install starts.
- Improved structured backend diagnostics for execution guard blocks, dependency resolution failures, unsupported source installs, and executor terminal failures.
- Strengthened Manager-agent summaries and retry hints so Manager sees actionable failure information instead of generic tool errors.
- Improved frontend handling for auto state, project refresh, running status visibility, and timeout configuration exposure.
- Added a workbench-visible system setting for executor run timeout.
- Tightened tests around Manager flow, dependency install resolution, executor reporting, and app-config runtime overrides.

## Scope Of This Release

This release contains many bug fixes and contract cleanups, including multi-file changes across:

- backend background task / workboard / auto services
- manager-agent tool summaries and async-boundary behavior
- executor reporting contract and dependency-install handling
- frontend manager chat and settings surfaces
- design and contract documentation

It should be treated as a significant stabilization release, not a cosmetic patch.

## Known Gaps

The system is materially more stable than the previous release, but known gaps remain.

Notably, this release does **not** claim to fully resolve outstanding issues in:

- skill integration
- MCP hub integration
- other remaining non-blocking edge cases outside the main execution path

Those areas still require follow-up work.

## Release Positioning

Recommended release message:

> Blueprint RE v0.3.0 is a major stability release focused on fixing a broad set of blocking bugs across Manager auto flow, background execution, dependency installation, executor reporting, and frontend refresh behavior. Some known issues remain in skill and MCP hub related paths, but the number of blocking bugs in the main workflow has been substantially reduced.
