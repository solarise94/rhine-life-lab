# 48. Feature/File Upload Workspace Management Upload Orphan Reconcile Plan

Status: remediation plan.

Date: 2026-06-06

Branch: `feature/file-upload-workspace-management`

Related:

- `docs/21_hot_path_performance_remediation.md`
- `docs/47_oaa2_dependency_terminal_and_card_scroll_remediation.md`

## Summary

Manager chat file upload currently has several backend integrity gaps:

- interrupted uploads can leave partial files under `data/uploads/`;
- successful file write followed by asset registration failure can leave orphan
  files on disk that are not visible in the graph.

The frontend upload progress and cancel button make this easier to trigger,
because user-driven abort is now a normal path instead of a rare transport
failure. The current backend route still assumes a single happy path:

1. stream request body directly into final path;
2. compute sha256 from the final path;
3. append asset to graph;
4. save graph.

That sequence is not exception-safe and is not serialized under the project
lock. This document defines a narrower upload contract:

- write to a staging `.part` file first;
- promote to the final path before graph registration;
- register the asset under the project lock using `save_assets()`, not
  `save_graph()`;
- remove orphan uploads at backend startup.

The goal is not to make upload and graph persistence fully transactional. The
goal is to constrain all failure modes to one self-healable state:

- file exists on disk but is not registered in graph.

That state is simpler and safer than:

- graph references a file that does not exist.

## Current Failure Modes

Current route: `backend/app/api/chat.py`

Current session-upload deletion path: `backend/app/services/project_file_service.py`

Current graph persistence: `backend/app/services/graph_store.py`

### 1. Partial File Residue

The route writes directly to:

```text
data/uploads/{asset_id}_{safe_name}
```

If `await file.read(...)`, local disk write, or disconnect handling fails after
the route has already opened the final target, the route closes the incoming
`UploadFile` but does not remove the partially written target.

Result:

- a partial file can remain under the final upload path;
- the graph never references it;
- the frontend cannot see or delete it.

### 2. Final File Before Graph Registration Is Not Self-Healing

The route currently computes `sha256_file(target)` and then appends/saves the
graph. If hashing, model construction, or graph persistence fails after file
write completes, the final file remains on disk but no asset is registered.

Result:

- a complete orphan file exists on disk;
- no current API path cleans it;
- future uploads do not reconcile it.

### 3. Concurrent Upload Lost Update

Graph registration in `upload_chat_file()` does not currently use
`project_service.lock_for(project_id)`.

That means concurrent uploads can do:

1. request A loads graph;
2. request B loads graph;
3. request A appends asset A and saves;
4. request B appends asset B to its stale copy and saves;
5. asset A is lost from `assets.json`.

This is independent from upload interruption, but the same remediation should
close it.

### 4. `save_graph()` Is Too Wide For This Path

`GraphStore.save_graph()` rewrites modules, assets, claims, runs, report items,
and metadata in sequence.

Upload registration only changes assets. Using `save_graph()` here increases
the partial-commit surface:

- `assets.json` may already be persisted;
- a later write such as claims/runs/report can fail;
- in-memory "rollback" does not undo already-written files.

For upload registration, the correct persistence API is:

```python
store.save_assets(graph.assets)
```

not `store.save_graph(graph)`.

## Intended Upload Contract

For manager chat uploads, the backend should enforce these invariants:

1. Partial upload bytes are never written to the final visible upload path.
2. Graph registration is serialized with other project graph mutations.
3. Asset registration only persists `assets.json`, not the whole graph.
4. Any failure before the final file is placed removes staging data
   (the `.part` file).
5. Once the final file has been placed via `temp_target.replace(target)`,
   the exception path never unlinks it. Bias toward "disk orphan on
   failure" because startup reconcile can clean orphans, whereas deleting
   a file that `save_assets()` has (possibly) committed would force the
   forbidden state (graph registered, file missing).
6. Startup reconcile removes leftover orphan upload files from previous crashes
   or interrupted deploys.

The only acceptable residual state after an abnormal process death or a
failed upload is:

- orphan file on disk, graph not registered.

This is acceptable because startup reconcile can safely delete it. The inverse
state:

- graph registered, file missing

must be avoided.

## Recommended Backend Design

### Phase Order

`backend/app/api/chat.py`

Use this order:

1. Build `target` and `temp_target = target.with_name(target.name + ".part")`.
2. Stream request body into `temp_target`.
3. Compute `sha256` from `temp_target`.
4. Under `project_service.lock_for(project_id)`:
   1. load graph;
   2. verify `asset_id` uniqueness (raise 409 on collision);
   3. promote `temp_target` into `target` using `Path.replace()`;
   4. build the `Asset`;
   5. append it to `graph.assets`;
   6. call `store.save_assets(graph.assets)`.
5. In `finally`, close the upload and best-effort remove leftover `.part`.
   Never unlink `target` in the exception path.

Important design choice:

- collision check runs before `replace()`, so a duplicate upload cannot
  silently overwrite a previously registered file;
- `replace()` runs inside the lock, serialized against other graph
  mutations on the same project;
- once `replace()` commits, the exception path never removes `target`.
  Orphan final files are the exclusive responsibility of startup
  reconcile.

`replace()` is intentionally overwriting any leftover final-path garbage
from a previous crash; startup reconcile is the authority for stale final
files. This deliberately biases toward "disk orphan on failure" because
that state is easier to repair than "graph points to missing file".

### Do Not Use `save_graph()` Here

Inside the upload route, only assets are changed. Persist exactly that:

```python
store.save_assets(graph.assets)
```

Do not call:

```python
store.save_graph(graph)
```

Reason:

- `save_graph()` touches unrelated files;
- it broadens the failure window;
- it makes post-failure state harder to reason about.
- even if `GraphStore` later gains a broader batch-persistence API, the upload
  path must remain single-file persistence only.

### Serialize Registration Under Project Lock

The registration phase must be wrapped in:

```python
with project_service.lock_for(project_id):
```

That lock should cover:

- `store.load_graph()`
- asset id collision check
- `graph.assets.append(asset)`
- `store.save_assets(graph.assets)`

This is required to prevent concurrent upload lost-update races.

### File Naming

The current `asset_id` is derived from `utc_now()` with second precision and a
filename stem. That is too weak for concurrent uploads of the same filename.

The remediation should make the upload identifier globally unique, for example:

```python
from uuid import uuid4
asset_id = f"upload_{uuid4().hex}"
```

or:

```python
asset_id = f"upload_{timestamp}_{uuid4().hex[:8]}_{stem}"
```

Requirements:

- the implementation sketch must actually include the UUID segment;
- final path must be unique across concurrent uploads;
- `.part` path must also be unique;
- uniqueness must not depend on second-level timestamps.

### Logging Discipline

Upload interruption is no longer an exceptional product event. With cancel UI,
it is expected.

Recommended logging:

- `ClientDisconnect` / `AbortError`-like path: `info`
- conflict such as asset id collision: `warning`
- disk/hash/persistence failure: `exception`

Do not log all upload aborts as full stack traces.

## Startup Reconcile

### Location

Add `reconcile_project_uploads(project_id)` to:

`backend/app/services/project_file_service.py`

Call it from:

`backend/app/main.py -> initialize_runtime_services()`

after existing runtime service startup reconciliation.

### Scope

The reconcile pass should:

1. lock the project with `project_service.lock_for(project_id)`;
2. attempt to load registered session-upload asset paths from graph;
3. scan `data/uploads/`;
4. remove files that are not registered session uploads and are older than a
   grace threshold.

This method is startup-only. It is not an online GC.

### Grace Windows

Recommended constants:

```python
ORPHAN_PART_GRACE_SECONDS = 60
ORPHAN_FINAL_GRACE_SECONDS = 600
```

Behavior:

- `.part` files older than `ORPHAN_PART_GRACE_SECONDS` may be removed even if
  graph loading fails;
- final files under `data/uploads/` that are not referenced by graph and are
  older than `ORPHAN_FINAL_GRACE_SECONDS` may be removed.

Why keep grace even at startup:

- it gives a small buffer if the backend is restarted during unusual shutdown
  timing;
- it prevents future accidental reuse of this helper as aggressive online GC.

The docstring should explicitly state:

- this method is intended for backend startup only;
- if repurposed for online cleanup, grace semantics must be revisited.
- if graph loading fails, the method may still remove stale `.part` files, but
  must not remove final upload files based on an empty registration set.

## Failure Matrix

| Failure point | Final file present | Graph registered | Expected cleanup result |
| --- | --- | --- | --- |
| disconnect while writing `.part` | no | no | `.part` removed in `finally`; log at `info` |
| local write error while writing `.part` | no | no | `.part` removed in `finally` |
| sha256 failure on `.part` | no | no | `.part` removed in `finally` |
| `replace()` failure | no | no | `.part` removed in `finally` |
| asset id collision (409) | no | no | `replace()` is skipped; log at `warning`; `.part` removed in `finally` |
| any exception after `replace()` commits | yes | no (or maybe yes) | final file preserved; `.part` already gone; startup reconcile removes the orphan if graph did not register it |
| process crash after final file placement but before `save_assets()` | yes | no | startup reconcile removes orphan final file |
| process crash during `atomic_write_json` of `save_assets()` | yes | unknown | may enter forbidden state on exotic FS/journal behavior; out of scope for this remediation |
| process crash while writing `.part` | maybe `.part` | no | startup reconcile removes stale `.part` |
| full success | yes | yes | no cleanup |

The forbidden state is:

- graph registered = yes
- final file present = no

The implementation should be reviewed against that invariant first. The
exception path is shaped so that it cannot produce this state: once
`replace()` commits, `target` is never unlinked by any except branch.

## Implementation Sketch

### `backend/app/api/chat.py`

High-level structure:

```python
from starlette.requests import ClientDisconnect
from uuid import uuid4

asset_id = f"upload_{utc_now().replace(':', '').replace('-', '').replace('Z', '')}_{uuid4().hex[:8]}_{Path(safe_name).stem[:32].lower()}"
size = 0
assets_persisted = False
asset: Asset | None = None
digest: str | None = None

try:
    with temp_target.open("wb") as handle:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            handle.write(chunk)

    digest = sha256_file(temp_target)

    with project_service.lock_for(project_id):
        store = project_service.graph_store(project_id)
        graph = store.load_graph()
        if any(existing.asset_id == asset_id for existing in graph.assets):
            raise HTTPException(status_code=409, detail="Uploaded asset id collision")

        temp_target.replace(target)

        asset = Asset(...)
        graph.assets.append(asset)
        store.save_assets(graph.assets)
        assets_persisted = True

except ClientDisconnect:
    logger.info("upload aborted by client: project=%s asset=%s", project_id, asset_id)
    raise
except HTTPException as exc:
    if exc.status_code == 409:
        logger.warning("upload asset id collision: project=%s asset=%s", project_id, asset_id)
    raise
except Exception:
    if assets_persisted:
        logger.error(
            "upload post-persist failure; file and assets.json may both be committed: %s",
            asset_id,
        )
    logger.exception("upload pipeline failed: project=%s asset=%s", project_id, asset_id)
    raise
finally:
    await file.close()
    # Only the staging .part is cleaned up here. The final target, once
    # placed, is never removed by the exception path: deleting it after
    # save_assets() has (possibly) committed would force the forbidden
    # state "graph registered, file missing". Orphan final files are
    # handled by reconcile_project_uploads() at backend startup.
    if temp_target.exists():
        temp_target.unlink()
```

Notes:

- do not attempt in-memory "graph rollback" after `save_assets()` failure;
  persistence is the truth, not the local object.
- never unlink `target` in any except branch. Once `replace()` has
  committed, orphan-on-disk is the only safe residual state; startup
  reconcile will clean it.
- `assets_persisted` exists solely to distinguish post-persist logging
  from the generic failure log; it does not drive cleanup.

### `backend/app/services/project_file_service.py`

Add:

- module logger
- `import time`
- reconcile constants
- `reconcile_project_uploads(project_id)`

The method should return a small diagnostic payload:

```python
{"removed": [...], "errors": 0}
```

This is useful for startup logging and future tests.

When matching files on disk against registered session-upload assets, the
method must compare using project-relative strings (the same form stored
in `asset.path`, e.g. `data/uploads/upload_..._foo.txt`). It must NOT
compare via `Path.resolve()` on absolute paths, because that is sensitive
to project root symlinks, bind mounts, or root renames and could cause
reconcile to misclassify a legitimately registered upload as an orphan and
delete it.

If graph loading fails:

- continue scanning `data/uploads/`;
- remove only stale `.part` files;
- log and keep all final files.

### `backend/app/main.py`

Extend startup initialization:

1. obtain `project_service`
2. obtain `project_file_service`
3. iterate `project_service.list_projects()`
4. run `reconcile_project_uploads(summary.project_id)`
5. log removed orphan counts per project

Do not fail application startup because one project reconcile throws. Log and
continue.

## Test Plan

Add focused backend tests for the upload route and reconcile helper.

### 1. Interrupted Upload Cleans `.part`

Simulate a read failure during staging write.

Assert:

- request fails;
- `.part` file is absent afterward;
- no final upload file exists;
- no asset is registered.

### 2. `save_assets()` Failure Leaves Orphan For Reconcile

Simulate:

- staging write succeeds;
- `replace()` succeeds;
- `store.save_assets()` raises before persistence commit.

Assert:

- no asset is registered (save_assets raised before persistence);
- no `.part` residue (finally cleaned it up);
- the final file survives as an orphan on disk;
- running `reconcile_project_uploads()` afterward removes the orphan,
  demonstrating the full self-heal loop.

### 2b. Post-Persist Failure Does Not Delete Final File

Simulate a failure after `save_assets()` has already returned and
`assets_persisted` is true.

Recommended simulation:

- mock `save_assets()` so it successfully writes `assets.json` first and then
  raises, or otherwise emulate a "post-persist failure" path after
  `assets_persisted` has become true (the existing test uses a lock context
  manager whose `__exit__` raises after `save_assets` has already
  committed).

Assert:

- final file is preserved (the exception path never unlinks target once
  placed, regardless of whether save committed);
- asset is registered in `assets.json` (save committed);
- implementation logs an explicit error-level post-persist message.

### 3. Startup Reconcile Removes Orphan `.part`

Seed:

- stale `.part` file under `data/uploads/`

Assert:

- `reconcile_project_uploads()` removes it.

### 4. Startup Reconcile Removes Stale Unregistered Final File

Seed:

- stale final upload file under `data/uploads/`
- graph does not reference it

Assert:

- reconcile removes it.

### 5. Registered Upload Survives Reconcile

Seed:

- final upload file under `data/uploads/`
- graph asset registered with `source == manager_chat_upload`

Assert:

- reconcile does not remove it.

### 5b. Reconcile Handles Broken Graph

Seed:

- corrupted `assets.json` or equivalent graph-load failure
- stale `.part` file under `data/uploads/`
- stale unregistered final file under `data/uploads/`

Assert:

- stale `.part` file is removed;
- stale final file is preserved;
- returned error count is greater than zero.

### 5c. Reconcile Recognizes Registered Upload Reached Via Symlink

Seed:

- a real project directory with a registered session-upload asset and
  its on-disk file;
- a symlink pointing at the same project directory;
- a `ProjectFileService` constructed with a `ProjectService` whose
  `data_root` goes through the symlink, so `project_path(project_id)`
  differs textually from the original root even though they address the
  same directory.

Assert:

- `reconcile_project_uploads()` reached via the symlinked root does NOT
  remove the registered upload file;
- returned `removed` list is empty.

This pins the relative-path comparison design: a resolve()-based
implementation would fail this test because `(symlinked_root /
asset.path).resolve()` would be compared against a path that may not
round-trip through the textual symlink, causing reconcile to misclassify
the file as an orphan.

### 6. Concurrent Upload Registration Does Not Lose Assets

This must be a real concurrency test, not just two serialized calls. Force two
upload registrations to overlap after `load_graph()` and before `save_assets()`
using `asyncio.gather`, a barrier, or equivalent synchronization. The key
assertion is:

- two concurrent registrations produce two assets in
  `assets.json`.

## Non-Goals

- No new frontend UI for orphan cleanup.
- No online periodic upload GC thread.
- No broad graph transaction framework.
- No change to `delete_session_upload()` semantics.

## Acceptance Criteria

The remediation is complete when all of the following are true:

- interrupted manager chat uploads do not leave `.part` or partial final files
  behind;
- asset registration uses the project lock;
- upload persistence writes only `assets.json`, not the whole graph;
- backend startup removes stale orphan files from `data/uploads/`;
- registered session uploads remain untouched by reconcile;
- no code path can leave graph registered while the final file is absent,
  excluding catastrophic filesystem corruption outside application control.
