# 49. Feature/File Upload Workspace Management Upload Proxy UI Scroll Plan

Status: remediation plan.

Date: 2026-06-07

Branch: `feature/file-upload-workspace-management`

Related:

- `docs/48_feature_file_upload_workspace_management_upload_orphan_reconcile_plan.md`
- `docs/47_oaa2_dependency_terminal_and_card_scroll_remediation.md`

## Summary

The upload lifecycle hardening in doc 48 fixed backend integrity for partial
uploads and orphan final files. The current remaining failures are frontend and
deployment-path issues:

- uploads are currently sent through the Next.js proxy path; large files hit
  its 10 MB body handling limit even when routed through a Next rewrite;
- failed large uploads never reach the existing `addAttachment()` success path,
  so the composer attachment pill is not shown;
- long upload filenames can push the upload cancel control out of the chat
  panel;
- global panel scroll containment leaks into Results and Files views, making
  normal cards trap wheel scrolling when the pointer is over them.

This document intentionally does not add chat messages for uploads. The desired
UI remains the existing small composer attachment pill, the same behavior used
by successful small-file uploads.

## Field Evidence

Runtime logs from the deployed frontend showed:

```text
Request body exceeded 10MB for /api/projects/oaa-2/chat-uploads.
Only the first 10MB will be available unless configured.
```

followed by:

```text
Failed to proxy http://127.0.0.1:18001/api/projects/oaa-2/chat-uploads
[Error: socket hang up] { code: 'ECONNRESET' }
```

After introducing `NEXT_PUBLIC_UPLOAD_API_BASE_URL=/upload-api` as a Next.js
rewrite, a 17 MB `bin10.pdf` upload on 2026-06-07 still failed with the same
Next.js body clone limit:

```text
Request body exceeded 10MB for /upload-api/projects/oaa-2/chat-uploads.
Only the first 10MB will be available unless configured.
```

followed by:

```text
Failed to proxy http://127.0.0.1:18001/api/projects/oaa-2/chat-uploads
[Error: socket hang up] { code: 'ECONNRESET' }
```

This proves that a Next.js rewrite is not an acceptable production upload path.
`/upload-api` must bypass the Next.js process entirely.

Runtime API state for `oaa-2` showed `session_uploads` containing only two text
uploads:

- `OAA_count_matrix.txt`
- `gene_symbol_count_matrix.txt`

The attempted zip upload was not registered as a session upload. This means the
failure is not a file-type classification problem in `ProjectFileService`; it
is an upload transport failure before the normal backend registration contract
can complete.

The other reported test project is currently corrupted: `/api/projects` reports
`test-project (corrupted)` because `project.json` is missing required
`ProjectState` fields. That project cannot be used as a clean signal for mp4
upload behavior until its project state is repaired or replaced with a healthy
test project.

## Current Relevant Code Paths

Upload request construction:

- `frontend/lib/api.ts`
- `uploadChatFile()`
- `uploadChatFileWithProgress()`

Current API base:

```ts
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";
```

Deployed frontend currently uses:

```text
NEXT_PUBLIC_API_BASE_URL=/api
```

That means browser uploads go to the Next.js app first. The Next app then
proxies to the backend through:

- `frontend/app/api/[...path]/route.ts`
- `frontend/next.config.ts` rewrites

Successful upload UI:

- `ManagerChatPanel` calls `addAttachment(projectId, response.attachment)` in
  the upload mutation `onSuccess`;
- the composer attachment bar renders `attachmentsByProject[projectId]`;
- the Files view lists graph assets whose metadata source is
  `manager_chat_upload`.

Backend upload registration:

- `backend/app/api/chat.py` writes `.part`, computes sha256, promotes to final
  path, and registers the `Asset`;
- `backend/app/services/project_file_service.py` classifies session uploads via
  `metadata.source == "manager_chat_upload"` or legacy `data/uploads/` paths.

## Intended Behavior

1. A successful upload, regardless of file extension, produces exactly one
   composer attachment pill.
2. A successful upload is visible under Files Workspace -> Session Uploads.
3. Zip, mp4, and other non-text files are accepted as upload assets unless a
   deliberate product-level size/type rule rejects them.
4. If an upload is rejected or fails, the user sees a clear upload error. Do not
   silently clean up and leave the UI looking like nothing happened.
5. Long filenames never push the upload progress cancel button outside the chat
   panel.
6. Wheel scrolling over Results and Files cards scrolls the page normally. It
   should not be trapped inside ordinary result/file cards.
7. ModuleCard page switching keeps its current minimal wheel handler behavior.

## Non-Goals

- Do not create a chat transcript message for uploads.
- Do not add a second backend upload API if the existing `/chat-uploads`
  endpoint can be reached correctly.
- Do not hide transport failures behind a fake successful UI state.
- Do not add broad JavaScript wheel interception for Results or Files cards.
- Do not change the backend orphan-reconcile contract from doc 48.

## Root Cause 1: Uploads Traverse The Next Proxy

The progress-enabled upload currently posts to:

```ts
`${API_BASE}/projects/${projectId}/chat-uploads`
```

When `API_BASE` is `/api`, the request body is handled by the current Next app
proxy before it reaches FastAPI. Large uploads then hit Next's request body
handling limit and the proxy stream can terminate with `ECONNRESET`.

The same failure has now been reproduced with `NEXT_PUBLIC_UPLOAD_API_BASE_URL`
set to `/upload-api` when `/upload-api` is implemented as a `next.config.ts`
rewrite. The path changed, but the request body still entered the Next.js
process and hit `middlewareClientMaxBodySize`.

The deploy constraint is important: the browser cannot reach the FastAPI backend
directly. Uploads must go through a reverse proxy. Therefore the fix is not
"bypass proxy"; the fix is "do not send large upload bodies through a proxy path
that is not explicitly configured for large streaming uploads."

That failure path prevents:

- backend asset registration;
- `uploadMutation.onSuccess`;
- `addAttachment()`;
- Files Workspace refresh showing a session upload.

### Required Fix: nginx Gateway

Use nginx as the public local gateway. All browser traffic keeps the same public
origin, but large upload bodies must not enter Next.js.

Target runtime topology:

```text
browser
  -> 127.0.0.1:13001  nginx gateway
       /upload-api/*  -> 127.0.0.1:18001/api/*   # direct FastAPI upload path
       /*             -> 127.0.0.1:13002/*       # Next.js UI and normal APIs
```

Next.js should move from the public port `13001` to an internal port such as
`13002`. nginx owns `13001`.

Keep the frontend upload base:

```text
NEXT_PUBLIC_UPLOAD_API_BASE_URL=/upload-api
```

Then route all upload calls through that base:

- `uploadChatFileWithProgress()`
- `uploadChatFile()`

Keep ordinary JSON/SSE traffic on `NEXT_PUBLIC_API_BASE_URL=/api`; those
requests will continue to reach the Next.js app through nginx and the existing
Next API proxy/rewrite path.

The nginx `/upload-api` location must:

- forward to the same FastAPI backend path prefix (`/api`);
- stream request bodies instead of buffering them through Next.js;
- preserve abort/disconnect behavior so backend `.part` cleanup still runs;
- return the backend response body and status unchanged.

This remediation does not define an application-level upload size limit. Do not
add frontend or backend hard caps such as 200 MB. If an operator needs a size
limit, it belongs in deployment-specific reverse-proxy policy and must be
surfaced to the user as a real upload failure, not hidden behind a fake success
state.

Managed deploy should use nginx with no product-level body-size cap:

```nginx
map $http_upgrade $connection_upgrade {
  default upgrade;
  ''      '';
}

server {
  listen 127.0.0.1:13001;

  # No application-level upload size cap. Operators may override this in a
  # site-specific deployment policy, but the product default should not cap.
  client_max_body_size 0;

  location /upload-api/ {
    proxy_request_buffering off;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_pass http://127.0.0.1:18001/api/;
    proxy_read_timeout 36000s;
    proxy_send_timeout 36000s;
  }

  location / {
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_pass http://127.0.0.1:13002;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
  }
}
```

The deploy script should detect nginx availability. If nginx is unavailable,
stop with an explicit deployment error for managed production deploys rather
than silently falling back to the Next rewrite upload path.

Because nginx is run as a user-level systemd service, the generated nginx config
must not rely on system-owned temp directories such as `/var/lib/nginx/body` or
`/var/lib/nginx/proxy`. The generated top-level `http {}` config must set all
nginx temp paths to user-writable directories under `__APP_ENV_DIR__`, and the
deploy script must create them before config validation/startup:

```nginx
client_body_temp_path __APP_ENV_DIR__/nginx-tmp/body;
proxy_temp_path       __APP_ENV_DIR__/nginx-tmp/proxy;
fastcgi_temp_path     __APP_ENV_DIR__/nginx-tmp/fastcgi;
uwsgi_temp_path       __APP_ENV_DIR__/nginx-tmp/uwsgi;
scgi_temp_path        __APP_ENV_DIR__/nginx-tmp/scgi;
```

The generated user service should validate configuration before starting:

```ini
ExecStartPre=__NGINX_BIN__ -t -c __APP_ENV_DIR__/nginx.conf
```

If managed install uses `apt-get install nginx`, the script should disable the
system-level `nginx.service` after installation:

```bash
sudo systemctl disable --now nginx 2>/dev/null || true
```

This avoids leaving an unrelated root/system nginx process enabled while the
product uses its own user-level nginx gateway on port `13001`.

Do not silently fall back from the dedicated upload proxy to the current `/api`
path for any upload, including small files. If the upload proxy is missing,
size-limited, or unreachable, surface the actual error.

### Rejected Alternatives

- Next.js `/api` App Route proxy: already fails large uploads with the 10 MB
  request body clone limit.
- Next.js `/upload-api` rewrite: reproduced the same 10 MB failure with a 17 MB
  `bin10.pdf` upload on 2026-06-07.
- `middlewareClientMaxBodySize`: keeps upload bodies inside the Next.js process
  and replaces one finite cap with another. It is not compatible with the
  product requirement that uploads have no application-level size cap.
- Frontend chunked upload: useful for resumable uploads later, but it is not the
  first fix for a proxy path that cannot pass a 17 MB file.

In either proxy design, browser progress measures bytes sent to the reverse
proxy. It does not prove backend disk registration has completed. The UI should
therefore keep the existing state split:

- upload progress until the browser finishes sending bytes to the proxy;
- "processing" while waiting for FastAPI to return the registered asset;
- composer attachment pill only after the backend success response.

## Root Cause 2: Successful Upload UI Is Transient And Not Refreshed Broadly

For successful small files, the current behavior is acceptable:

```ts
addAttachment(projectId, response.attachment)
```

The gap is that Files view data is only fetched when `view === "files"`, and
`refreshWorkspace()` uses `refetchQueries(..., type: "active")`. If Files is not
active during chat upload, the query may not have fresh data when the user later
opens the Files view unless normal query mount refetch happens.

### Fix

After upload success:

1. keep `addAttachment(projectId, response.attachment)`;
2. always invalidate the Files query for that project, not only active-refetch
   it:

```ts
queryClient.invalidateQueries({ queryKey: queryKeys.files(projectId) });
```

3. never fake a successful Files entry when the backend response did not return
   an asset.

The UI should continue to show only the small composer attachment pill. No chat
message is required.

## Root Cause 3: Upload Progress Layout Does Not Fully Bound Long Names

The upload progress row is currently a flex layout:

```css
.manager-upload-progress-header {
  display: flex;
  justify-content: space-between;
}
```

The filename span has ellipsis rules, but the parent layout still allows a long
filename to compete with the fixed controls. The cancel button can be pushed out
of the panel.

### Fix

Use a grid or stricter flex containment:

```css
.manager-upload-progress {
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
}

.manager-upload-progress-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  min-width: 0;
}

.manager-upload-progress-footer {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  min-width: 0;
}

.manager-upload-progress-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.manager-upload-progress-controls {
  min-width: max-content;
}
```

Also apply the same containment discipline to attachment pills:

```css
.attachment-pill {
  max-width: 100%;
  min-width: 0;
  overflow: hidden;
}

.attachment-pill > .label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
```

If the JSX currently keeps raw text inside `.attachment-pill`, add a single
label element such as `<span className="label">...</span>` and target that
direct child. Do not add multiple nested wrappers.

## Root Cause 4: Global Panel Scroll Containment Is Too Broad

The current global panel body rule is:

```css
.panel-body {
  flex: 1;
  overflow: auto;
  overscroll-behavior: contain;
}
```

This makes every panel body a scroll container and prevents scroll chaining to
the page when the pointer is over Results or Files cards.

This is not caused directly by `ModuleCard`'s wheel handler. The ModuleCard
handler is scoped to its own `.file-bag-paper-slot` and should remain scoped.

### Fix

Do not remove `.panel-body` as a scroll container. Many panels rely on
`overflow: auto` there. The low-blast-radius fix is to remove only global
scroll-chain containment:

```css
.panel-body {
  padding: 16px 18px;
  flex: 1;
  overflow: auto;
  /* no global overscroll-behavior: contain */
}
```

Then keep or add containment only where it is deliberate:

- `.card-detail-panel-body`
- `.page-content-scroll`
- `.artifact-preview-drawer .panel-body`

Leave `.table-preview` as a local `overflow: auto` table scroller without adding
global overscroll containment.

Do not add JavaScript wheel handlers to ResultsGrid or FilesPanel. They do not
need custom wheel logic.

## Implementation Order

1. Add upload proxy-base configuration.
2. Route every file upload call through the upload proxy base while keeping
   non-upload APIs unchanged:
   - `uploadChatFile()`
   - `uploadChatFileWithProgress()`
3. Change managed deploy topology:
   - run Next.js on an internal port such as `13002`;
   - run nginx on the public local port `13001`;
   - route `/upload-api/*` directly to FastAPI `/api/*`;
   - route all other paths to the internal Next.js server.
4. Generate and install the nginx config during managed deploy, and fail
   explicitly if nginx is not available.
5. Create nginx temp directories under `__APP_ENV_DIR__/nginx-tmp/` before
   generating or validating the nginx config.
6. Add nginx config validation via `ExecStartPre=nginx -t -c ...`.
7. If nginx is installed by apt, disable the system-level nginx service after
   installation so only the user-level gateway is active.
8. Update install/deploy output so users see:
   - public frontend: `http://127.0.0.1:13001` through nginx;
   - internal Next.js: `http://127.0.0.1:13002`;
   - backend: `http://127.0.0.1:18001`.
9. Keep successful upload UI as `addAttachment()` only; add Files query
   invalidation on success.
10. Harden upload error messages so proxy/body-limit/network failures are shown
   clearly in the chat panel.
11. Fix upload progress and attachment pill long-name containment.
12. Remove only global `.panel-body` overscroll containment while preserving
   `.panel-body` `overflow: auto`; keep containment on scoped bounded
   containers.
13. Verify with a healthy test project, not the currently corrupted
   `test-project`.

## Test Plan

### Frontend / Playwright

1. Long filename upload progress:
   - use a filename over 180 characters;
   - assert the cancel button remains visible and clickable;
   - assert the progress card stays inside the chat panel width.

2. Large zip upload:
   - use a file larger than 10 MB;
   - assert the browser request path is `/upload-api/...`;
   - assert frontend logs do not contain `Request body exceeded 10MB`;
   - assert nginx starts with user-writable temp paths, not system
     `/var/lib/nginx/*` paths;
   - assert backend logs contain the `POST /api/projects/{id}/chat-uploads`
     request;
   - assert success creates one composer attachment pill;
   - assert Files Workspace -> Session Uploads contains the uploaded asset.

3. Large mp4 upload:
   - same assertions as the zip test;
   - verify asset type is accepted as a generic uploaded file if no richer type
     is defined.

4. Small zip upload:
   - use a 1 KB zip file;
   - assert asset registration succeeds;
   - assert one composer attachment pill renders;
   - assert Files Workspace -> Session Uploads contains the asset.

5. Small mp4 upload:
   - use a small mp4 fixture under 1 MB;
   - assert the same registration, attachment pill, and Session Uploads
     behavior.

6. Files tab not mounted during upload:
   - start from the chat/tasks view with Files closed;
   - upload a file successfully;
   - navigate to Files;
   - assert Session Uploads contains the new asset on first render without a
     manual refresh.

7. Upload failure visibility:
   - force upload endpoint failure;
   - assert the chat panel shows a clear error;
   - assert no fake attachment pill is created.

8. Proxy upload failure visibility:
   - force the upload proxy to reject or drop a request;
   - assert a clear user-visible error and no fake attachment pill.
   - assert no `.part` file remains after backend receives and aborts the upload,
     or no upload file is created if nginx rejects before FastAPI.

9. nginx service validation:
   - run the generated `nginx -t -c __APP_ENV_DIR__/nginx.conf`;
   - assert the generated user service contains `ExecStartPre`;
   - assert system-level `nginx.service` is disabled after apt-managed
     installation;
   - assert non-WebSocket requests do not send unconditional
     `Connection: upgrade`.

10. Results scroll:
   - open Results view with enough content to require page scroll;
   - wheel over a result card;
   - assert the page scroll position changes.
   - assert a Results panel with many cards can still scroll internally if its
     panel body is height-bounded.

11. Files scroll:
   - open Files view with enough content to require page scroll;
   - wheel over a file card;
   - assert the page scroll position changes.

12. Report / detail scroll regression:
   - open a long report/detail panel;
   - assert the panel body still scrolls internally.

13. ModuleCard regression:
   - open Tasks view;
   - wheel inside a ModuleCard page content area;
   - assert internal page content scroll still works;
   - assert card page switching still happens only at the page-content boundary.
   - assert `.page-content-scroll` still has deliberate overscroll containment.

### Backend

1. Existing backend upload endpoint accepts `.zip` and `.mp4`:
   - call `/api/projects/{project_id}/chat-uploads` through the configured
     upload proxy in integration tests, or call the FastAPI test app directly
     in backend unit tests;
   - assert the returned attachment is type `asset`;
   - assert `ProjectFileService.list_files()` returns the asset under
     `session_uploads`.

2. Existing doc 48 remediation tests remain green:
   - partial upload cleanup;
   - orphan reconcile;
   - concurrent registration;
   - collision handling.

## Acceptance Criteria

- A >10 MB text upload goes through the dedicated upload proxy and produces one
  composer attachment pill.
- A >10 MB zip upload produces one composer attachment pill and one Session
  Upload entry.
- A >10 MB mp4 upload produces one composer attachment pill and one Session
  Upload entry in a healthy project.
- A 1 KB zip upload and a small mp4 upload also produce composer attachment
  pills and Session Upload entries.
- Failed uploads show a visible error and do not create fake attachment pills.
- All uploads use the same reverse-proxy path that is explicitly configured for
  large streaming request bodies.
- The production upload path bypasses Next.js entirely; `frontend` logs contain
  no `Request body exceeded 10MB` warning for `/upload-api`.
- nginx owns the public local frontend port and Next.js runs on an internal port.
- Long upload filenames never hide the cancel button.
- Results and Files cards no longer trap page scroll.
- Existing panels that rely on `.panel-body { overflow: auto }` still scroll.
- ModuleCard scroll/page-switch behavior does not regress.
- No new chat messages are created for upload receipts.
- No new backend upload endpoint is added unless proxying the existing endpoint
  proves impossible.
