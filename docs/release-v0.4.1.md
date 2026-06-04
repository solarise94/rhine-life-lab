# Blueprint RE v0.4.1

Release date: 2026-06-04

## Summary

`v0.4.1` is a focused stabilization release on top of `v0.4.0`.

This release fixes a cluster of bugs around Manager auto wake flow and strengthens the runtime dependency resolver recovery path. The main goal is to make the workboard and auto loop behave more reliably when dependency jobs finish, fail, or get interrupted.

## What Improved

- Fixed ready-frontier wake behavior in auto mode so newly ready work can wake Manager correctly.
- Released stuck auto wake latches to avoid auto mode getting wedged after interrupted or partial flows.
- Recovered orphaned runtime dependency jobs so the workboard and dependency status do not stay permanently blocked after interruptions.
- Preserved the `v0.4.0` runtime dependency install visibility work while tightening liveness and recovery behavior around dependency repair.

## Scope Of This Release

Primary changes in this release are concentrated in:

- `backend/app/services/manager_auto_service.py`
- `backend/app/services/worker_service.py`
- `backend/app/main.py`
- `manager-agent/src/server.js`
- `backend/tests/test_auto_episode_flow.py`

This should be treated as a patch release with meaningful reliability impact, not a feature reset.

## Verification

- `cd frontend && npm run build`
- `cd manager-agent && node --check src/server.js`
- Local services restarted successfully on the release candidate commit.

## Release Positioning

Recommended release message:

> Blueprint RE v0.4.1 is a stabilization patch release that fixes stuck or missed Manager auto wakes, improves ready-frontier wake behavior, and hardens runtime dependency job recovery after interrupted flows.
