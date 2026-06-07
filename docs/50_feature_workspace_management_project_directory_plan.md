# Feature 50: Server Project Directory Workspace Management

## Goal

Allow users to create or select a project working directory on the server and run Blueprint work inside that directory, while preserving Blueprint's internal project state, run audit, result validation, and soft-sandbox guarantees.

The target experience is close to "choose a server project directory and work there", not "mount arbitrary data sources" and not "let the executor write anywhere under the user's HOME".

## Current Constraints

The current project model assumes:

- `ProjectService.project_path(project_id)` resolves to `BLUEPRINT_DATA_ROOT/<project_id>`.
- Project state files live directly under the project root: `graph/`, `chat/`, `runs/`, `results/`, `scripts/`, `data/`, `configs/`.
- Executor launch uses the project root to derive `runs/<run_id>`, `results/<card_id>/<run_id>`, and `scripts/generated/`.
- The current bwrap policy makes only the current run directory, result directory, and generated scripts directory writable.
- `graph/` and `.git/` are forbidden paths and must remain protected from executor writes.

Those constraints mean the feature should not simply turn the existing internal `workspace/<project_id>` into a symlink to a user directory without also preserving internal-state protection.

## Product Decision

Use a **Managed Server Project Directory** model.

The user chooses or creates a server directory, for example:

```text
/home/user/my-project
```

Blueprint treats that directory as the project directory. In Phase 2 `workspace_write` mode, executor free-write work happens only inside a dedicated user workspace subdirectory:

```text
/home/user/my-project/
  work/        # User and executor working directory.
  results/     # Validated Blueprint outputs.
  .blueprint/  # Internal project state in the future layout.
```

For a lower-risk first implementation that preserves the current root layout, use:

```text
/home/user/my-project/
  work/       # User-visible work directory; executor cwd only in Phase 2 workspace_write mode.
  graph/      # Blueprint internal state, executor read-only or masked.
  chat/       # Blueprint internal state, executor read-only or masked.
  runs/       # Only the current run subdirectory is writable.
  results/    # The current result directory is writable.
  scripts/    # Only scripts/generated/ is writable.
  data/
  configs/
```

The first implementation can keep `graph/`, `chat/`, and `runs/` at the project root to avoid a large migration. The long-term cleaner layout is to move internal state under `.blueprint/`.

## Phase Boundary

This feature must not change the executor working-directory contract in Phase 1.

Phase 1 adds project directory selection, registry resolution, `work/` creation, and server-side browsing. Existing executor launches continue to use:

```text
cwd = <project_root>/runs/<run_id>
```

Phase 2 introduces an explicit `workspace_write` execution mode. Only that mode changes the executor cwd to:

```text
cwd = <project_root>/work
```

Do not silently change the cwd for existing guarded runs. Existing prompts, provider wrappers, manifest helpers, and validation paths assume the run directory is the process cwd.

## Terminology

- **Project directory**: the user-selected server directory that contains the Blueprint project.
- **User work directory**: `work/` inside the project directory. This is the user-visible working area. It becomes the executor cwd only in Phase 2 `workspace_write` mode.
- **Internal state directory**: Blueprint-owned state such as graph, chat sessions, run audit, task packets, and project metadata.
- **Result directory**: controlled output area used by manifest validation and result acceptance.

Avoid calling this feature "mount data directory" in the UI. That implies a separate external data tree. The intended feature is "choose project working directory".

## Directory Selection Scope

Default scope:

```text
Path.home()
```

If `BLUEPRINT_PROJECT_ROOTS` is configured, extend the selectable roots:

```bash
BLUEPRINT_PROJECT_ROOTS=/data/blueprint-projects,/mnt/shared/projects
```

Effective selectable roots:

```text
Path.home()
/data/blueprint-projects
/mnt/shared/projects
```

Rules:

- Do not expose `/` as a browsable root.
- Every selected path must be resolved and verified to remain inside one configured root.
- The boundary check must be component-aware: after `Path.resolve()`, the selected path must equal an allowed root or have that allowed root in `selected.parents`. Do not use string prefix checks such as `startswith()`.
- Symlink escape is forbidden unless the resolved target is still inside an allowed root.
- The backend uses the OS permissions of the running Blueprint user. It does not elevate privileges.
- MVP should prefer creating a new empty directory. Opening an arbitrary existing non-empty directory is a later feature.

## Project Registry

The current "scan `BLUEPRINT_DATA_ROOT` children" model is not enough once projects can live in multiple server directories.

Introduce a project registry:

```json
{
  "items": [
    {
      "project_id": "oaa-2",
      "name": "OAA 2",
      "project_root": "/home/user/my-project",
      "root_kind": "managed_project_directory",
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

Behavior:

- The registry is authoritative when an entry exists.
- Phase 1 uses lazy fallback for legacy projects: if a `project_id` is not present in the registry, `ProjectService` may fall back to `BLUEPRINT_DATA_ROOT/<project_id>`.
- `list_projects()` returns the union of registry entries and legacy fallback projects. It must de-duplicate by `project_id`, preferring the registry entry.
- `ProjectService.project_path(project_id)` resolves through the registry first.
- Missing or inaccessible project directories appear as `status="error"` with a clear recovery message.

Registry recovery:

- If `project_registry.json` is missing, the backend falls back to scanning legacy `BLUEPRINT_DATA_ROOT/<project_id>` projects and continues serving them.
- If `project_registry.json` is corrupted, the backend must not make all projects disappear. It should report a registry recovery error and still list legacy fallback projects.
- Provide a later operator command or endpoint to rebuild the registry from existing project roots. This is not required for Phase 1, but the file format must allow reconstruction.

## Creation Flow

UI flow:

1. User opens Projects page.
2. User clicks `新建服务器项目目录`.
3. Backend returns selectable roots and directory entries.
4. User picks a parent directory under HOME or configured roots.
5. User enters project directory name.
6. Backend creates the directory if it does not exist.
7. Backend scaffolds the Blueprint project structure.
8. Backend writes/updates the project registry.
9. UI navigates to the new project.

MVP validation:

- Directory name must be safe and not empty.
- Target directory must not already contain Blueprint state.
- If target exists and is non-empty, reject for MVP with a message: "Opening existing non-empty directories is not supported yet."
- If target exists and is empty, allow using it.

## Directory Browser API

The frontend cannot use a browser file picker for server directories. The backend must expose a server-side directory browser.

Sketch:

```text
GET /api/workspace-roots
GET /api/workspace-roots/{root_id}/entries?path=<relative_path>&kind=directory|all&cursor=<cursor>
POST /api/projects/from-directory
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

- `kind=directory`: returns directories only. Use this for project creation directory picking.
- `kind=all`: returns directories and files. Use this for Files panel browsing under an existing project's `work/` directory.

For project creation in Phase 1, call with `kind=directory`:

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

For Files panel browsing in Phase 1, call with `kind=all` and a root/path derived from the opened project's `work/` directory. The response item includes file metadata:

```json
{
  "root_id": "project_work",
  "path": "data",
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

`POST /api/projects/from-directory` is the creation endpoint for the multi-step UI flow.

Request:

```json
{
  "root_id": "home",
  "parent_path": "projects",
  "directory_name": "oaa-2",
  "project_id": "oaa-2",
  "name": "OAA 2",
  "current_goal": "Analyze OAA 2 data"
}
```

Response:

```json
{
  "project": {
    "project_id": "oaa-2",
    "name": "OAA 2",
    "status": "active"
  }
}
```

This endpoint extends the existing `POST /api/projects` creation behavior with server-directory selection. The existing `POST /api/projects` remains available for legacy/default `BLUEPRINT_DATA_ROOT/<project_id>` project creation.

Directory listing rules:

- Return one directory level at a time. Do not recursively scan.
- Use pagination or a hard entry limit for large directories.
- `path` is a URL-encoded POSIX-style relative path. The backend decodes it, normalizes it as a relative path, resolves it under the selected root, and then performs the boundary check.
- `kind` defaults to `directory` for project creation callers. Files panel callers must request `kind=all`.
- Hide entries that cannot be stat'ed or entered by the backend user.
- Hidden dot-directories can be shown only when the user explicitly enables "show hidden".
- Always validate the resolved path against the selected root before listing.

## Executor Working Directory

This section describes Phase 2. It is a breaking change to the executor launch contract and must be implemented behind an explicit `workspace_write` mode.

The executor should operate inside:

```text
<project_root>/work
```

Runtime environment:

```text
BLUEPRINT_PROJECT_ROOT=<project_root>
BLUEPRINT_USER_WORKSPACE=<project_root>/work
BLUEPRINT_RUNTIME_WORKING_DIR=<project_root>/work
BLUEPRINT_RUN_DIR=<project_root>/runs/<run_id>
BLUEPRINT_RESULT_DIR=<project_root>/results/<card_id>/<run_id>
```

Launch cwd:

```text
cwd = <project_root>/work
```

Contract files remain absolute or explicitly referenced:

- `BLUEPRINT_TASK_PACKET`
- `BLUEPRINT_MANIFEST_PATH`
- `BLUEPRINT_MANIFEST_CANDIDATE_PATH`
- `BLUEPRINT_EXECUTOR_RESULT_TOOL`
- `BLUEPRINT_TERMINAL_REPORT_PATH`

The executor can work naturally in `work/`, but still reports outputs through the existing manifest and result-reporting contract.

Required contract updates:

- `BLUEPRINT_TASK_PACKET` must remain an absolute path to `<project_root>/runs/<run_id>/task_packet.json`.
- `BLUEPRINT_MANIFEST_PATH` and `BLUEPRINT_MANIFEST_CANDIDATE_PATH` must remain absolute paths under `<project_root>/runs/<run_id>/`.
- Provider prompts must stop relying on `task_packet.json` being in cwd. They should say "read `$BLUEPRINT_TASK_PACKET`" or use the absolute task packet path.
- Existing card inputs remain graph asset paths relative to `BLUEPRINT_PROJECT_ROOT`. Executor prompts must explicitly state that relative input paths are project-root-relative, not cwd-relative.
- `BLUEPRINT_RESULT_DIR` should be an absolute filesystem path. Manifest asset paths remain project-root-relative strings such as `results/<card_id>/<run_id>/output.csv`.
- Provider adapters (`pi`, `opencode`, `claude_code`, `codex`) must be reviewed because their generated prompts and wrapper files currently use run-dir assumptions.

## Soft Sandbox Policy

This feature is compatible with bwrap if write access is limited to the selected project directory and internal state is protected.

Phase 1 writable binds remain unchanged from today:

```text
<project_root>/runs/<run_id>
<project_root>/results/<card_id>/<run_id>
<project_root>/scripts/generated/<run_id>
```

In Phase 1, `work/` exists and can be browsed by the backend/UI, but it is not added to executor writable binds.

Phase 2 `workspace_write` writable binds:

```text
<project_root>/work
<project_root>/runs/<run_id>
<project_root>/results/<card_id>/<run_id>
<project_root>/scripts/generated/<run_id>
```

Recommended protected paths:

```text
<project_root>/graph
<project_root>/chat
<project_root>/.git
<project_root>/.blueprint
```

Do not bind the entire HOME directory read-write.

Two acceptable sandbox variants:

- **Guarded variant**: keep host root read-only and bind only the explicit writable paths above.
- **Tighter variant**: do not bind host root; bind only required system/runtime paths and the selected project paths.

The MVP can keep the guarded variant because it matches the current bwrap model.

Do not broaden `scripts/generated/<run_id>` to the entire `scripts/generated/` tree unless a later design explicitly accepts that write-surface expansion.

## Concurrency Model For `work/`

`work/` is persistent and shared by all runs in the same project. That is different from the current per-run writable directories.

Phase 2 must choose one concurrency policy before enabling `workspace_write`:

- **Recommended MVP**: serialize `workspace_write` runs per project. At most one run with write access to `work/` may execute at a time.
- **Alternative**: keep project-level concurrency, but make the writable cwd per-run, for example `work/.blueprint-runs/<run_id>/`, and reserve `work/` as a read/write shared area only when the user explicitly opts in.

Do not allow multiple concurrent executors to freely write the same `work/` directory by default. That can cause nondeterministic conflicts such as two runs editing the same script, notebook, cache file, or intermediate output.

Existing guarded runs may continue using the current concurrent executor limit because their writable paths remain per-run.

## `work/` Lifecycle

`work/` is a user-visible persistent working directory. It must not be auto-cleaned like a run directory.

Rules:

- Backend never deletes `work/` during normal run cleanup.
- Project reset may offer an explicit `Clean work directory` action, but it must show a confirmation and a file count/size summary.
- Run cleanup removes `runs/<run_id>` and controlled result directories only; it does not remove arbitrary files in `work/`.
- The UI should show a warning when `work/` contains stale Blueprint run scratch folders, if the per-run scratch alternative is used.
- The executor prompt should warn that files in `work/` may persist across runs and should not be assumed to be clean unless the user requested cleanup.

## Result Writing Model

Executor free-write work can happen in `work/`, but validated project outputs should still go through:

```text
results/<card_id>/<run_id>/
```

Reasons:

- Result validation already expects this path pattern.
- Manifest acceptance depends on controlled output paths.
- Cleanup and run history are easier to preserve.
- User workspace files and accepted result assets remain conceptually separate.

If users want final files inside `work/`, support a later explicit "publish result into work/" action instead of making every run write there by default.

## Files UI

Files panel should show at least two areas:

- `Work Directory`: files under `<project_root>/work`.
- `Project Outputs`: files and assets under `results/`, `data/uploads/`, and accepted graph assets.

Do not automatically register every file under `work/` as an asset.

Recommended actions:

- `Use as input`: registers a selected `work/` file as a project asset reference. Phase 1 should not copy by default; it should store a project-root-relative path such as `work/data.csv` and mark the asset metadata source as `work_directory`.
- `Attach to Manager`: optional separate action for chat context. Do not conflate it with asset registration.
- `Open location`: reveals the file in the work directory tree.
- `Publish output`: copies or links accepted result files into a selected location under `work/`.

Listing scale:

- The work-directory file browser must list one level at a time.
- Large directories require pagination, cursoring, or a fixed entry cap.
- Do not recursively scan `work/` to build the Files panel.

## Git Interaction

The current project creation path initializes a git repository in the project root. For managed server project directories:

- Phase 1 supports only new empty directories or existing empty directories.
- If the selected directory already contains `.git`, reject it in MVP and explain that opening existing git checkouts is a later feature.
- Add `work/` policy to `.gitignore` explicitly. The safer MVP default is to ignore `work/**` so user scratch files and large data are not committed by Blueprint.
- Keep controlled generated scripts and accepted outputs under existing Blueprint paths. If those are ignored today, preserve the current behavior.
- Opening an existing git checkout later requires a separate design because Blueprint's own git repo and the user's repo can conflict.

## Delete And Detach Semantics

Project delete needs explicit behavior because the project may live in a user-chosen directory.

Recommended UI:

- `Remove from Blueprint only`: removes registry entry, leaves directory untouched.
- `Delete project directory`: deletes the whole directory after a strong confirmation.

Default should be `Remove from Blueprint only`.

If the directory contains active runs, both operations should be blocked until runs are stopped or cleaned up.

## Runtime Inaccessibility

A project directory can become inaccessible after it was registered, for example if a mount disappears or the directory is deleted.

Behavior:

- `list_projects()` should show the project with `status="error"` and a clear reason.
- Opening the project should return a recoverable error rather than creating replacement files at the missing path.
- Starting runs must fail fast if the project root, `work/`, `runs/`, or `results/` cannot be created or accessed.
- If the directory disappears mid-run, the run should transition to failed with the filesystem error captured in run events and logs.
- Reconnection/recovery should not silently create a new directory at the old path unless the user explicitly chooses to recreate it.

## Migration Strategy

Phase 1:

- Add project registry.
- Existing `BLUEPRINT_DATA_ROOT/<project_id>` projects continue working.
- New projects can be created under HOME or configured roots.
- Keep current root layout and add `work/`.
- Executor cwd remains `runs/<run_id>`.
- `work/` can be browsed from the Files UI but is not yet the executor cwd.

Phase 2:

- Make executor cwd `work/`.
- Add `BLUEPRINT_USER_WORKSPACE`.
- Update prompts and task packets to tell agents to use `work/` for normal project files and `results/` for validated outputs.
- Implement `workspace_write` mode and its per-project serialization or per-run workspace policy.

Phase 3:

- Add Files panel work-directory browser and "use as input" action.

Phase 4:

- Consider moving internal state to `.blueprint/` for a cleaner RStudio-like project root.

Do not combine Phase 4 with the MVP unless the migration cost is explicitly accepted.

## Phase Verification Plan

Each phase must have behavior-level verification before moving to the next phase.

### Phase 1 Verification

Project registry:

- Legacy projects under `BLUEPRINT_DATA_ROOT/<project_id>` still appear without a registry file.
- Registry entries outside `BLUEPRINT_DATA_ROOT` appear in `list_projects()`.
- If registry and legacy scan contain the same `project_id`, the registry entry wins.
- Missing project root appears as `status="error"` and does not create replacement directories.
- Corrupted `project_registry.json` does not hide legacy projects.

Directory browser:

- `GET /api/workspace-roots` returns HOME by default.
- `BLUEPRINT_PROJECT_ROOTS` adds extra roots without removing HOME.
- Listing returns one level only and paginates or caps large directories.
- URL-encoded paths with spaces, unicode, `#`, and `&` decode correctly.
- `../` traversal is rejected.
- Prefix attacks are rejected, for example root `/data/projects` and target `/data/projects-evil`.
- Symlink escape outside allowed roots is rejected.

Project creation:

- Creating under HOME succeeds for a new safe directory name.
- Creating under a configured extra root succeeds.
- Existing empty target directory is accepted.
- Existing non-empty target directory is rejected in MVP.
- Existing `.git` target is rejected in MVP.
- Created project has current root layout plus `work/`.
- `.gitignore` includes the documented `work/` policy.

Executor/sandbox:

- Phase 1 executor cwd remains `runs/<run_id>`.
- Phase 1 bwrap writable binds remain exactly `runs/<run_id>`, `results/<card_id>/<run_id>`, and `scripts/generated/<run_id>`.
- Phase 1 does not add `work/` to executor writable binds.
- Existing guarded run tests still pass.

Files UI:

- Work-directory browser can show `<project_root>/work` without recursively scanning it.
- "Use as input" registers a `work/...` project-root-relative asset reference.
- Files with spaces and unicode in their names can be selected and registered.

### Phase 2 Verification

Executor contract:

- `workspace_write` mode changes cwd to `<project_root>/work`.
- Guarded mode still uses `runs/<run_id>` cwd.
- `BLUEPRINT_TASK_PACKET` is absolute and points to `<project_root>/runs/<run_id>/task_packet.json`.
- `BLUEPRINT_MANIFEST_PATH` and `BLUEPRINT_MANIFEST_CANDIDATE_PATH` are absolute run-dir paths.
- `BLUEPRINT_RESULT_DIR` is an absolute path under `<project_root>/results/<card_id>/<run_id>`.
- Manifest asset paths remain project-root-relative.
- Provider prompts instruct agents to read `$BLUEPRINT_TASK_PACKET`, not `task_packet.json` from cwd.
- Existing input asset paths are resolved relative to `BLUEPRINT_PROJECT_ROOT`, not cwd.

Sandbox:

- `workspace_write` adds `<project_root>/work` to writable binds.
- `workspace_write` keeps `graph/`, `chat/`, `.git`, and `.blueprint` non-writable.
- `workspace_write` keeps `scripts/generated/<run_id>` as the only generated-script writable bind.
- Executor cannot write outside the selected project directory.

Concurrency:

- If using the recommended MVP policy, two `workspace_write` runs in the same project are serialized.
- Guarded runs retain existing project concurrency behavior.
- The UI or run state explains when a run is waiting for the per-project workspace write lock.

Lifecycle:

- Run cleanup does not delete arbitrary files under `work/`.
- An explicit `Clean work directory` action shows confirmation and file count/size before deleting.
- Stale per-run scratch directories, if implemented, are shown as cleanable but not silently removed.

### Phase 3 Verification

Files and assets:

- Work-directory listing handles large directories without full recursive scans.
- `Use as input` and `Attach to Manager` are separate actions.
- Registered `work/...` assets appear in Files and can be used as card inputs.
- Missing `work/...` assets show a clear unavailable state instead of crashing previews or runs.

Result publishing:

- Accepted result files can be published into a selected location under `work/`.
- Publish never overwrites by default.
- Publish logs source result path, destination `work/...` path, timestamp, and actor.
- Publish rejects destinations outside `work/`.

### Phase 4 Verification

Internal-state migration:

- Existing root-layout projects migrate to `.blueprint/` without losing graph, chat, runs, results, or project memory.
- Migration is idempotent.
- Interrupted migration leaves a recovery marker and does not produce a half-migrated project that loads as healthy.
- Executor and backend both use the new internal-state root.
- Rollback or recovery instructions are documented before enabling the migration in production.

## Non-Goals

- No arbitrary write access outside the selected project directory.
- No browsing from `/`.
- No multi-user impersonation in MVP.
- No SSH/SFTP remote connector in MVP.
- No automatic asset registration for every file under `work/`.
- No replacement of result manifest validation.
- No application-level upload size cap introduced by this feature.

## Acceptance Criteria

- User can create a new project directory under HOME.
- User can create a new project directory under an extra configured project root.
- Existing projects under the old `BLUEPRINT_DATA_ROOT/<project_id>` layout still load.
- Project list shows missing/inaccessible project directories as recoverable errors.
- Phase 1 executor cwd remains `runs/<run_id>`.
- Phase 2 `workspace_write` executor cwd is the selected project's `work/` directory.
- Phase 2 `workspace_write` executor can write files under `work/`.
- Executor can write current run files and current result files.
- Executor cannot modify `graph/`, `chat/`, `.git/`, or `.blueprint/`.
- Executor cannot write outside the selected project directory.
- Concurrent `workspace_write` runs cannot freely write the same `work/` directory unless an explicit concurrency policy has been implemented.
- `work/` is not auto-cleaned by run cleanup.
- Deleting a project defaults to removing the registry entry without deleting the user's directory.
- Active runs block project directory delete/detach operations.
