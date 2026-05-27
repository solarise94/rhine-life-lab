# Hot Path Performance Remediation Plan

## Goal

Reduce avoidable disk IO and pydantic validation in run execution hot paths without changing on-disk formats.

The first pass intentionally avoids migrating `events.json` to JSONL. Existing run timeline, diagnostics, tests, and old run data continue to use the same JSON files.

## Confirmed Bottlenecks

1. `WorkerService._append_event()` is called for every stdout line. It currently loads and rewrites the full `runs/<run_id>/events.json` on every event.
2. `WorkerService._set_run_status()` loads full graph state to update one run, one card, and module status.
3. `WorkerService._run_status()` loads full graph state to read one run status.
4. `ManagerWakeProcessor` polls all projects every second through `ProjectService.list_projects()`, which currently expands to full snapshots.
5. `ManifestService.capture_filesystem_snapshot()` scans the whole project tree before and after each run.
6. `AppConfigService._load()` rereads `_app_settings.json` on every service method.

## First-Pass Changes

### P0. Event Flush Batching

Keep `events.json` as the storage format, but batch high-volume `executor_output` events in memory during stdout pumping.

- Non-output lifecycle events still flush immediately.
- Stdout events flush in bounded batches and once at process end.
- The UI still receives timeline updates during long outputs, just not one disk write per line.

### P0. Lightweight Run Status Updates

Add `GraphStore` helpers that operate on run files directly:

- `get_run_status(run_id)`
- `append_run_events(run_id, new_events)`

Change `_set_run_status()` to load only `runs.json`, `cards.json`, and `modules.json`. It still runs module/card hierarchy sync.

### P1. Wake Loop Throttling

Increase wake loop idle interval from 1 second to 5 seconds. This reduces idle IO immediately while preserving manager auto behavior.

### P1. Lightweight Project Listing

Keep `get_project_snapshot()` as the full-detail endpoint for an opened project, but make `list_projects()` build summaries from only:

- `project.json`
- `graph/graph.json` metadata
- `graph/cards.json`
- `graph/assets.json`

This avoids loading proposals, git history, worker capabilities, runtime lists, runs, claims, and report items for every project in the sidebar and manager wake loop.

### P2. Metadata-Only Runtime Preferences

Add `GraphStore.load_metadata()` and use it when only graph metadata is needed. Runtime preference reads no longer require full graph loading.

### P2. App Settings Mtime Cache

Cache `_app_settings.json` inside `AppConfigService` and reload only when the file `mtime_ns` changes. Saves update the cache immediately, so UI writes and executor profile changes stay visible without repeated disk reads.

### Deferred

- `events.jsonl` migration.
- GraphStore write-through cache.
- Filesystem audit scope narrowing.

These are useful, but each touches broader behavior or compatibility. They should follow after the P0/P1 hot path changes are verified.

## Compatibility And Risk Notes

- On-disk formats stay unchanged. Existing `events.json`, graph JSON files, and old run data remain readable.
- Executor stdout is still written to `transcript.md` immediately per line. Timeline events are buffered in memory and flushed in batches, so the UI may see stdout bubbles slightly later during very chatty runs.
- If the backend process crashes before a buffered flush, the last unflushed stdout timeline events can be lost, but the transcript still contains those lines.
- Lifecycle events flush pending stdout before writing themselves, preserving final event ordering for run failure, review, cancellation, and validation messages.
- Wake processing may react up to 5 seconds later while idle because the polling interval changed from 1 second to 5 seconds.

## Verification

- Backend unit suite.
- Focused manager flow tests covering run events, status transitions, cancellation, and module group status.
- Manual smoke with a verbose executor to confirm event flushing and final timeline persistence.
