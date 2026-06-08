# Feature 50: Managed Project With Mounted Data Directory

## Goal

Allow users to keep Blueprint projects in the managed system workspace while attaching a user-selected server data directory for input data and exported results.

The target experience is:

- Blueprint internal state stays under the managed project directory.
- Users can mount an existing non-empty data directory under HOME or configured roots.
- Files in the mounted data directory can be registered as data assets.
- Accepted Blueprint results can be explicitly exported into the mounted data directory.

This is intentionally not "open an arbitrary non-empty directory as the Blueprint project root" and not "let the executor write anywhere under the user's HOME".

## Current Constraints

The current project model assumes:

- `ProjectService.project_path(project_id)` resolves to `BLUEPRINT_DATA_ROOT/<project_id>`.
- Project state files live directly under the project root: `graph/`, `chat/`, `runs/`, `results/`, `scripts/`, `data/`, `configs/`.
- Executor launch uses the project root to derive `runs/<run_id>`, `results/<card_id>/<run_id>`, and `scripts/generated/`.
- The current bwrap policy makes only the current run directory, result directory, and generated scripts directory writable.
- `graph/` and `.git/` are forbidden paths and must remain protected from executor writes.

Those constraints mean the feature should not turn `workspace/<project_id>` into a symlink to a user directory. The user data directory must be modeled as a mounted data tree, separate from Blueprint's internal project state.

## Product Decision

Use a **Managed Project + Mounted Data Directory** model.

Blueprint creates and owns the project directory under the managed system workspace:

```text
BLUEPRINT_DATA_ROOT/<project_id>/
  project.json
  graph/
  chat/
  runs/
  results/
  scripts/
  work/       # optional internal executor scratch/workspace
  configs/
```

The user may also mount an existing data directory:

```text
/home/user/datasets/oaa-2/
  raw/
  metadata.csv
  exports/
```

The mounted data directory is not the project root. Blueprint must not scaffold `project.json`, `graph/`, `runs/`, or `.git` inside it.

The project stores a mount record:

```json
{
  "data_directory": {
    "root_id": "home",
    "path": "datasets/oaa-2",
    "resolved_path": "/home/user/datasets/oaa-2",
    "mounted_at": "2026-06-08T00:00:00Z"
  }
}
```

Files registered from the mounted data directory are stored as project-root-relative logical asset paths:

```text
data_mount/metadata.csv
```

The asset metadata records the source:

```json
{
  "source": "mounted_data_directory",
  "mount_path": "/home/user/datasets/oaa-2/metadata.csv",
  "sha256": "...",
  "size_bytes": 12345
}
```

The executor continues to produce validated outputs under `results/<card_id>/<run_id>/`. Exporting an accepted result into the mounted data directory is an explicit backend copy operation, not automatic executor write access.

## Phase Boundary

This feature must not change the executor working-directory contract in Phase 1.

Phase 1 adds mounted data directory selection, server-side browsing, manual asset registration from the mounted data directory, and explicit result export. Existing executor launches continue to use:

```text
cwd = <project_root>/runs/<run_id>
```

The mounted data directory is not the executor cwd in MVP. If the later `workspace_write` executor mode is kept, it remains a separate future feature and must not change the mounted data directory contract.

## Terminology

- **Project directory**: the Blueprint-managed system directory, normally `BLUEPRINT_DATA_ROOT/<project_id>`.
- **Mounted data directory**: a user-selected server directory used for input data and explicit exports. It may be non-empty.
- **Internal state directory**: Blueprint-owned state such as graph, chat sessions, run audit, task packets, and project metadata.
- **Result directory**: controlled output area used by manifest validation and result acceptance.

UI naming:

- Project creation stays `新建项目`.
- The project creation dialog may include a sub-option `挂载数据目录`.
- Files panel should label the mounted tree as `数据目录`.
- Avoid calling the mounted data directory `Work Directory`.

## Directory Selection Scope

Default scope:

```text
Path.home()
```

If `BLUEPRINT_DATA_DIRECTORY_ROOTS` is configured, extend the selectable roots:

```bash
BLUEPRINT_DATA_DIRECTORY_ROOTS=/data/blueprint-datasets,/mnt/shared/datasets
```

Effective selectable roots:

```text
Path.home()
/data/blueprint-datasets
/mnt/shared/datasets
```

Rules:

- Do not expose `/` as a browsable root.
- Every selected path must be resolved and verified to remain inside one configured root.
- The boundary check must be component-aware: after `Path.resolve()`, the selected path must equal an allowed root or have that allowed root in `selected.parents`. Do not use string prefix checks such as `startswith()`.
- Symlink escape is forbidden unless the resolved target is still inside an allowed root.
- The backend uses the OS permissions of the running Blueprint user. It does not elevate privileges.
- Mounted data directories may be existing and non-empty.
- Reject a mounted data directory only when it is inaccessible, outside allowed roots, resolves through a forbidden symlink escape, or is itself a Blueprint project/internal state directory.
- Reject existing `.git` only if future code intends to initialize or mutate git state inside the mounted directory. The MVP should not write git state into the mounted data directory, so `.git` does not need to be a blocker for read/register/export use.
- Reject mounted data directories that overlap the managed project directory in either direction:
  - Mounted directory must not equal or contain `BLUEPRINT_DATA_ROOT/<project_id>`.
  - Mounted directory must not be inside `BLUEPRINT_DATA_ROOT/<project_id>`.
  - This avoids circular browsing, accidental export into internal state, and confusing `data_mount` self-references.

## Project Registry (Optional/Future)

Projects continue to live under `BLUEPRINT_DATA_ROOT/<project_id>` in the MVP. A project registry is not required for mounted data directory support because project lookup can continue using the existing managed project path.

If future project relocation is needed, introduce a project registry:

```json
{
  "items": [
    {
      "project_id": "oaa-2",
      "name": "OAA 2",
      "project_root": "/home/user/.blueprint-re/workspace/oaa-2",
      "root_kind": "legacy_data_root",
      "created_at": "2026-06-07T00:00:00Z",
      "updated_at": "2026-06-07T00:00:00Z"
    }
  ]
}
```

Recommended location:

```text
BLUEPRINT_DATA_ROOT/_system/project_registry.json
```

Future behavior:

- The registry is authoritative when an entry exists.
- Use lazy fallback for legacy projects: if a `project_id` is not present in the registry, `ProjectService` may fall back to `BLUEPRINT_DATA_ROOT/<project_id>`.
- `list_projects()` returns the union of registry entries and legacy fallback projects. It must de-duplicate by `project_id`, preferring the registry entry.
- `ProjectService.project_path(project_id)` resolves through the registry first.
- Missing or inaccessible project directories appear as `status="error"` with a clear recovery message.

Registry recovery:

- If `project_registry.json` is missing, the backend falls back to scanning legacy `BLUEPRINT_DATA_ROOT/<project_id>` projects and continues serving them.
- If `project_registry.json` is corrupted, the backend must not make all projects disappear. It should report a registry recovery error and still list legacy fallback projects.
- Provide a later operator command or endpoint to rebuild the registry from existing project roots.

## Creation Flow

UI flow:

1. User opens Projects page.
2. User clicks `新建项目`.
3. User enters project name, project id, and goal.
4. Optional: user expands `挂载数据目录`.
5. Backend returns selectable data roots and directory entries.
6. User selects an existing server data directory under HOME or configured roots.
7. Backend creates the managed Blueprint project under `BLUEPRINT_DATA_ROOT/<project_id>`.
8. Backend stores the optional mounted data directory record in project state/metadata.
9. UI navigates to the new project.

MVP validation:

- Directory name must be safe and not empty.
- Managed project id must not already exist.
- Mounted data directory must exist, be readable, and resolve inside an allowed data root.
- Mounted data directory may be non-empty.
- Do not scaffold Blueprint state inside the mounted data directory.
- If the selected data directory already contains Blueprint state (`project.json`, `graph/`, `.blueprint/`), reject or require a separate import flow.

## Directory Browser API

The frontend cannot use a browser file picker for server directories. The backend must expose a server-side directory browser.

Sketch:

```text
GET /api/workspace-roots
GET /api/workspace-roots/{root_id}/entries?path=<relative_path>&kind=directory|all&cursor=<cursor>
POST /api/projects
PUT /api/projects/{project_id}/data-directory
GET /api/projects/{project_id}/data-directory/entries?path=<relative_path>&kind=directory|all&cursor=<cursor>
POST /api/projects/{project_id}/data-directory/assets/register
POST /api/projects/{project_id}/assets/{asset_id}/export-to-data-directory
```

`GET /api/workspace-roots` returns:

```json
{
  "items": [
    {
      "root_id": "home",
      "label": "Home",
      "path_display": "~"
    }
  ]
}
```

`GET /api/workspace-roots/{root_id}/entries` supports two listing modes:

- `kind=directory`: returns directories only. Use this for mounted data directory picking.
- `kind=all`: returns directories and files. Use this for Files panel browsing under an existing project's mounted data directory.

For selecting a data directory in Phase 1, call with `kind=directory`:

```json
{
  "root_id": "home",
  "path": "projects",
  "items": [
    {
      "name": "analysis-a",
      "kind": "directory",
      "is_empty": true,
      "mtime": "2026-06-07T00:00:00Z"
    }
  ],
  "next_cursor": null
}
```

For Files panel browsing in Phase 1, call the project-scoped mounted data directory endpoint with `kind=all`. The response item includes file metadata:

```json
{
  "project_id": "oaa-2",
  "path": "raw",
  "items": [
    {
      "name": "counts.csv",
      "kind": "file",
      "size_bytes": 1048576,
      "mtime": "2026-06-07T00:00:00Z"
    }
  ],
  "next_cursor": null
}
```

`POST /api/projects` remains the managed project creation endpoint. If a data directory is selected at creation time, the frontend may either include a `data_directory` payload in `POST /api/projects` or call `PUT /api/projects/{project_id}/data-directory` immediately after creation. Prefer a single transactional endpoint if the UX requires both operations to succeed together.

Recommended `PUT /api/projects/{project_id}/data-directory` request:

```json
{
  "root_id": "home",
  "path": "datasets/oaa-2"
}
```

Response:

```json
{
  "data_directory": {
    "project_id": "oaa-2",
    "root_id": "home",
    "path": "datasets/oaa-2",
    "path_display": "~/datasets/oaa-2",
    "mounted": true
  }
}
```

`POST /api/projects/{project_id}/data-directory/assets/register` registers a selected mounted data file as a project asset. It stores a logical path like `data_mount/raw/counts.csv` and metadata `source="mounted_data_directory"`.

`POST /api/projects/{project_id}/assets/{asset_id}/export-to-data-directory` copies an accepted result asset into a selected destination under the mounted data directory. It must not overwrite by default.

Directory listing rules:

- Return one directory level at a time. Do not recursively scan.
- Use pagination or a hard entry limit for large directories.
- `path` is a URL-encoded POSIX-style relative path. The backend decodes it, normalizes it as a relative path, resolves it under the selected root, and then performs the boundary check.
- `kind` defaults to `directory` for data-directory selection callers. Files panel callers must request `kind=all`.
- Hide entries that cannot be stat'ed or entered by the backend user.
- Hidden dot-directories can be shown only when the user explicitly enables "show hidden".
- Always validate the resolved path against the selected root before listing.

## Asset Registration And Integrity

`Add to data assets` is a registration action, not a copy action.

Registration stores:

```json
{
  "asset_id": "data_mount_...",
  "path": "data_mount/raw/counts.csv",
  "metadata": {
    "source": "mounted_data_directory",
    "mount_relative_path": "raw/counts.csv",
    "registered_size_bytes": 123456789,
    "registered_mtime": "2026-06-08T00:00:00Z",
    "integrity_kind": "size_mtime",
    "sha256": null
  }
}
```

Integrity policy:

- Always record `registered_size_bytes` and `registered_mtime`.
- Compute `sha256` only when the file is below a configurable threshold, for example `BLUEPRINT_DATA_MOUNT_HASH_LIMIT_BYTES`.
- For files above the threshold, set `integrity_kind="size_mtime"` and do not block registration on hashing.
- For files below the threshold, set `integrity_kind="sha256"` and record the digest.
- Registration must be idempotent by logical `data_mount/...` path.

Stale/missing policy:

- On preview, run launch, and export, stat the source file.
- If the file is missing, mark or display the asset as unavailable.
- If size or mtime differs from the registered metadata, mark the asset as stale and require re-registration before using it as a card input.
- For `integrity_kind="sha256"` assets, a digest mismatch is stale even if size/mtime match.
- Stale mounted data assets must not be used as card inputs without explicit user confirmation or re-registration. MVP should prefer re-registration.

## Data Asset Read Model

Registered mounted data files use logical project-root-relative paths:

```text
data_mount/<relative_path_inside_mounted_directory>
```

MVP read strategy:

- On run launch, if the task packet contains any `data_mount/...` input asset, create `<project_root>/data_mount` as a stable logical mount point.
- Bind the mounted data directory read-only to `<project_root>/data_mount` for the executor process.
- Mounted data directory inputs require the bwrap soft sandbox path. If bwrap is unavailable, fail fast rather than running unsandboxed with write-capable access to the source directory.
- The run launcher owns the `data_mount` mount point lifecycle while building the bwrap launch plan.
- Before creating the bind, the launcher must fail fast if `<project_root>/data_mount` exists and is non-empty. This prevents user-created files in the logical mount point from being hidden or confused with mounted assets.
- After the run exits, remove the empty `<project_root>/data_mount` directory if possible. Do not recursively delete it.
- Keep executor cwd unchanged in Phase 1:

```text
cwd = <project_root>/runs/<run_id>
```

- Input asset paths remain project-root-relative. Executor prompts must state that `data_mount/...` paths resolve from `BLUEPRINT_PROJECT_ROOT`, not from cwd.
- Mounted data directory paths are never passed to the executor as writable real filesystem paths.

Why read-only bind instead of copy:

- It avoids duplicating large datasets into the managed project directory.
- It preserves the current graph asset path model.
- It fits the bwrap model: the data directory can be `--ro-bind` mounted while run/result directories remain writable.

Alternative strategies rejected for MVP:

- Copy or hard-link files into `data/<asset_id>` at registration time. This is safer for reproducibility but can be expensive and surprising for large datasets.
- Store only metadata and disallow mounted files as card inputs. This blocks the primary use case.

## Soft Sandbox Policy

This feature is compatible with bwrap if write access is limited to the managed project directory and mounted data directories are read-only or accessed only through backend-controlled operations.

Phase 1 writable binds remain unchanged from today:

```text
<project_root>/runs/<run_id>
<project_root>/results/<card_id>/<run_id>
<project_root>/scripts/generated/<run_id>
```

Mounted data directory bind:

```text
<mounted_data_directory>  --ro-bind-->  <project_root>/data_mount
```

Recommended protected paths:

```text
<project_root>/graph
<project_root>/chat
<project_root>/.git
<project_root>/.blueprint
```

Do not bind the entire HOME directory read-write.

Mounted data directory policy:

- MVP: add the mounted data directory only as a read-only bind when a run needs `data_mount/...` inputs.
- Never bind the mounted data directory read-write in MVP.
- If direct executor writes to the mounted data directory are ever allowed, require an explicit mode, project-level serialization, overwrite protection, and audit logging. Do not add this to Feature 50 MVP.

Two acceptable sandbox variants:

- **Guarded variant**: keep host root read-only and bind only the explicit writable paths above.
- **Tighter variant**: do not bind host root; bind only required system/runtime paths and the selected project paths.

The MVP can keep the guarded variant because it matches the current bwrap model.

Do not broaden `scripts/generated/<run_id>` to the entire `scripts/generated/` tree unless a later design explicitly accepts that write-surface expansion.

## Result Writing And Export Model

Executor validated project outputs continue to go through:

```text
results/<card_id>/<run_id>/
```

Reasons:

- Result validation already expects this path pattern.
- Manifest acceptance depends on controlled output paths.
- Cleanup and run history are easier to preserve.
- Mounted source data and accepted result assets remain conceptually separate.

If users want final files in the mounted data directory, support an explicit "export to data directory" action instead of making every run write there by default.

Export rules:

- Source must be an accepted result/data asset controlled by Blueprint.
- Export source asset must exist and be accepted. Export does not need mounted-source stale checks because accepted results are controlled Blueprint outputs.
- Destination must resolve inside the mounted data directory.
- Missing destination parent directories may be created by the backend, but only after resolving the final path and verifying every created parent remains inside the mounted data directory.
- Export must not overwrite by default.
- Default conflict strategy: generate a unique sibling filename by appending ` (1)`, ` (2)`, and so on before the extension, for example `report.csv` -> `report (1).csv`.
- Conflict naming must be concurrency-safe. Use an atomic temporary-file-plus-rename flow or add a short random suffix if the numbered candidate is taken between check and write.
- Advanced UI may allow the user to provide an explicit destination filename, but the backend still rejects overwrite unless `overwrite=true` is explicitly supported later.
- Export should log source asset id, source path, destination logical path, timestamp, and actor.
- Export failure must not mutate graph state.

## Files UI

Files panel should show at least two areas when a data directory is mounted:

- `数据目录`: files under the mounted data directory.
- `Project Outputs`: files and assets under `results/`, `data/uploads/`, and accepted graph assets.

If the future `workspace_write` mode is enabled, an advanced/internal area may show `<project_root>/work`, but it is not part of the Feature 50 MVP Files UI.

Do not automatically register every file under the mounted data directory as an asset.

Recommended actions:

- `Add to data assets`: registers a selected mounted data file as a project asset reference. It should not copy by default; it should store a logical path such as `data_mount/raw/counts.csv` and mark the asset metadata source as `mounted_data_directory`.
- `Attach to Manager`: optional separate action for chat context. Do not conflate it with asset registration.
- `Open location`: reveals the file in the mounted data directory tree.
- `Export result`: copies accepted result files into a selected location under the mounted data directory.

Listing scale:

- The data-directory file browser must list one level at a time.
- Large directories require pagination, cursoring, or a fixed entry cap.
- Do not recursively scan the mounted data directory to build the Files panel.

## Manager Tools (Future)

Manager may get mounted data directory tools after the UI/backend path is stable.

Tool candidates:

- `list_data_directory(project_id, path)`: list one level under the mounted data directory.
- `register_data_asset(project_id, path)`: register a mounted data file through the same backend service as the UI `Add to data assets` action.
- `export_result(project_id, asset_id, destination)`: export an accepted result through the same backend service as the UI `Export result` action.
- `describe_data_asset(project_id, asset_id)`: return metadata, freshness state, size, mtime, and lightweight preview/schema information.

Rules:

- Manager tools must call the same backend service layer used by the UI.
- Manager tools must not bypass boundary checks, stale checks, overwrite protection, or audit logging.
- Manager tools must not receive direct arbitrary filesystem paths outside the mounted data directory logical path.

## Git Interaction

The current project creation path initializes a git repository in the managed project root. For mounted data directories:

- Phase 1 supports selecting existing non-empty data directories.
- Do not initialize git inside the mounted data directory.
- If the mounted data directory already contains `.git`, do not mutate it. Register/export operations still must obey overwrite protection and audit logging.
- Add `work/` policy to the managed project's `.gitignore` explicitly. The safer MVP default is to ignore `work/**` so internal scratch files and large data are not committed by Blueprint.
- Keep controlled generated scripts and accepted outputs under existing Blueprint paths. If those are ignored today, preserve the current behavior.
- Opening an existing git checkout as the Blueprint project root remains a separate later design.

## Delete And Detach Semantics

Project delete and data directory detach are separate operations.

Recommended UI:

- Project delete: deletes or archives only the managed Blueprint project directory according to existing product policy.
- `Detach data directory`: removes the mount record and leaves the user data directory untouched.
- `Delete data directory`: not available in MVP. Blueprint should not delete user-mounted data directories.

Default should be `Detach data directory` for mount removal.

MVP uses strict active-run blocking:

- If any run in the project has an active status such as `queued`, `launching`, `running`, `reviewing`, or `needs_approval`, detach, remount, and export are blocked.
- This is conservative even though a running bwrap mount namespace is already fixed. It keeps user-visible semantics simple and prevents exports/remounts from racing with run setup or review.
- A later design may relax export blocking, but only with explicit audit and conflict handling.

## Runtime Inaccessibility

A managed project directory or mounted data directory can become inaccessible after it was registered, for example if a mount disappears or the directory is deleted.

Behavior:

- `list_projects()` should show the project with `status="error"` and a clear reason.
- Opening the project should return a recoverable error rather than creating replacement files at the missing path.
- Starting runs must fail fast if the project root, `runs/`, `results/`, or required read-only `data_mount` bind cannot be created or accessed.
- Missing mounted data directory should not make the whole project disappear. Files UI should show `数据目录不可用` and registered `data_mount/...` assets should show an unavailable state.
- If the directory disappears mid-run, the run should transition to failed with the filesystem error captured in run events and logs.
- Reconnection/recovery should not silently create a new directory at the old path unless the user explicitly chooses to recreate it.

## Migration Strategy

Phase 1:

- Existing `BLUEPRINT_DATA_ROOT/<project_id>` projects continue working.
- New projects continue to be created under the managed system workspace.
- Add optional mounted data directory metadata to project state.
- Add server-side data directory browser.
- Add Files UI `数据目录` browser.
- Add manual `Add to data assets` registration for mounted data files.
- Add explicit result export into the mounted data directory.
- Executor cwd remains `runs/<run_id>`.
- Mounted data directory is not executor-writable.

Phase 2:

- Add mounted data directory detach/remount.
- Add unavailable-state recovery UX.
- Add export history and audit display.
- Add stale asset detection in previews and run launch.

Phase 3:

- Add optional Manager tools for browsing, registering, describing, and exporting mounted data assets.

Phase 4:

- Consider moving internal state to `.blueprint/` for a cleaner RStudio-like project root.

Do not combine Phase 4 with the MVP unless the migration cost is explicitly accepted.

## Phase Verification Plan

Each phase must have behavior-level verification before moving to the next phase.

### Phase 1 Verification

Managed project compatibility:

- Legacy projects under `BLUEPRINT_DATA_ROOT/<project_id>` still appear without a registry file.
- Missing project root appears as `status="error"` and does not create replacement directories.

Data directory browser:

- `GET /api/workspace-roots` returns HOME by default.
- `BLUEPRINT_DATA_DIRECTORY_ROOTS` adds extra roots without removing HOME.
- Listing returns one level only and paginates or caps large directories.
- URL-encoded paths with spaces, unicode, `#`, and `&` decode correctly.
- `../` traversal is rejected.
- Prefix attacks are rejected, for example root `/data/projects` and target `/data/projects-evil`.
- Symlink escape outside allowed roots is rejected.

Project creation:

- Creating a managed project still succeeds under `BLUEPRINT_DATA_ROOT/<project_id>`.
- Creating a project with no mounted data directory still works.
- Creating a project with a mounted existing non-empty data directory succeeds.
- Mounted data directory does not receive `project.json`, `graph/`, `runs/`, or `.git`.
- Mounted data directory outside allowed roots is rejected.
- Mounted data directory that is inaccessible is rejected or shown as unavailable.
- Managed project `.gitignore` includes the documented `work/` policy.

Executor/sandbox:

- Phase 1 executor cwd remains `runs/<run_id>`.
- Phase 1 bwrap writable binds remain exactly `runs/<run_id>`, `results/<card_id>/<run_id>`, and `scripts/generated/<run_id>`.
- Phase 1 does not add the mounted data directory to executor writable binds.
- Phase 1 adds the mounted data directory as a read-only bind only when a run uses `data_mount/...` inputs.
- If bwrap is unavailable, runs that need `data_mount/...` inputs fail fast.
- Existing guarded run tests still pass.

Files UI:

- Data-directory browser can show the mounted data directory without recursively scanning it.
- "Add to data assets" registers a `data_mount/...` logical asset reference.
- Files with spaces and unicode in their names can be selected and registered.
- Registered mounted data assets appear in Files and can be used as card inputs.
- Missing mounted data directory shows a clear unavailable state.
- Accepted result files can be exported into the mounted data directory without overwriting by default.

### Phase 2 Verification

Detach/remount/recovery:

- Detaching a mounted data directory removes only the mount record and leaves the user directory untouched.
- Remounting a new directory validates the same boundary and overlap rules as initial mount.
- Missing mounted data directory shows a clear unavailable state without hiding the project.
- Registered `data_mount/...` assets become unavailable when the source file is missing.
- Changed source files become stale when size/mtime or sha256 no longer match registered metadata.
- Stale mounted data assets cannot be used as card inputs until re-registered.

Export audit:

- Export history records source asset id, source path, destination path, timestamp, and actor.
- Export conflict names are generated deterministically as `name (1).ext`, `name (2).ext`, etc.
- Export destination parent directories are created only inside the mounted data directory boundary.
- Concurrent exports cannot overwrite each other.
- Export outside the mounted data directory is rejected.

### Phase 3 Verification

Files and assets:

- Mounted data directory listing handles large directories without full recursive scans.
- `Add to data assets` and `Attach to Manager` are separate actions.
- Registered `data_mount/...` assets appear in Files and can be used as card inputs.
- Missing `data_mount/...` assets show a clear unavailable state instead of crashing previews or runs.
- Optional Manager tool can register a mounted data file only through the same backend validation path as the UI.
- Optional Manager tools cannot bypass stale checks or overwrite protection.

Result export:

- Accepted result files can be exported into a selected location under the mounted data directory.
- Export never overwrites by default.
- Export logs source result path, destination data-directory path, timestamp, and actor.
- Export rejects destinations outside the mounted data directory.

### Phase 4 Verification

Internal-state migration:

- Existing root-layout projects migrate to `.blueprint/` without losing graph, chat, runs, results, or project memory.
- Migration is idempotent.
- Interrupted migration leaves a recovery marker and does not produce a half-migrated project that loads as healthy.
- Executor and backend both use the new internal-state root.
- Rollback or recovery instructions are documented before enabling the migration in production.

## Non-Goals

- No arbitrary write access outside the managed project directory or backend-controlled export destinations.
- No arbitrary executor write access to the mounted data directory in MVP.
- No browsing from `/`.
- No multi-user impersonation in MVP.
- No SSH/SFTP remote connector in MVP.
- No automatic asset registration for every file under the mounted data directory.
- No replacement of result manifest validation.
- No application-level upload size cap introduced by this feature.

## Acceptance Criteria

- User can create a managed project under `BLUEPRINT_DATA_ROOT`.
- User can optionally mount an existing non-empty data directory under HOME.
- User can optionally mount an existing non-empty data directory under an extra configured data root.
- Existing projects under the old `BLUEPRINT_DATA_ROOT/<project_id>` layout still load.
- Project list shows missing/inaccessible project directories as recoverable errors.
- Phase 1 executor cwd remains `runs/<run_id>`.
- Executor does not get arbitrary write access to the mounted data directory in MVP.
- Executor can read registered `data_mount/...` assets through the stable read-only logical mount.
- Executor can write current run files and current result files.
- Executor cannot modify `graph/`, `chat/`, `.git/`, or `.blueprint/`.
- Executor cannot write outside the managed project directory except through explicit backend-controlled export actions.
- Mounted data directory can be browsed one level at a time.
- A mounted data file can be manually registered as a `data_mount/...` asset.
- Registered mounted data assets can be used as card inputs.
- Registered mounted data assets become stale when source metadata changes and must be re-registered before use.
- Accepted result assets can be exported into the mounted data directory without overwriting by default.
- Export conflict names are generated safely without overwriting existing files.
- Detaching a mounted data directory never deletes the user data directory.
- Active runs block data-directory detach, remount, and export operations in the MVP.

## Future Appendix: Internal `workspace_write` Mode

This appendix is a design sketch for a possible future feature. It is not part of Feature 50 and must not influence Feature 50 implementation decisions.

Future behavior:

- Add explicit `workspace_write` execution mode.
- Keep guarded mode cwd as `runs/<run_id>`.
- In `workspace_write`, set cwd to `<project_root>/work`.
- Add `BLUEPRINT_USER_WORKSPACE=<project_root>/work`.
- Keep mounted data directory separate from `work/`.
- Do not add the mounted data directory as writable bind.

Future writable binds:

```text
<project_root>/work
<project_root>/runs/<run_id>
<project_root>/results/<card_id>/<run_id>
<project_root>/scripts/generated/<run_id>
```

Future contract requirements:

- `BLUEPRINT_TASK_PACKET`, `BLUEPRINT_MANIFEST_PATH`, `BLUEPRINT_MANIFEST_CANDIDATE_PATH`, and `BLUEPRINT_EXECUTOR_RESULT_TOOL` remain absolute.
- Existing input asset paths remain project-root-relative and prompts must say so.
- Validated project outputs still go through `results/<card_id>/<run_id>/`.
- Formal manifest outputs must not be placed in `work/`.

Future concurrency/lifecycle:

- `work/` is persistent and shared by all runs in the same project.
- Serialize `workspace_write` runs per project, or use per-run work scratch directories.
- Backend never deletes `work/` during normal run cleanup.
- Executor prompt warns that files under `work/` may persist across runs and should not be assumed clean.
