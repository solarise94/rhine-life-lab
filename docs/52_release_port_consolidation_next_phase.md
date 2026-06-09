# Release Port Consolidation Next Phase

## Decision

Port consolidation is a follow-up phase after the user-mode release installer is stable.

The installer work remains the current priority. The release installer should keep
the existing service topology for now and only present one user-facing URL.

Current product-facing behavior:

```text
browser -> 127.0.0.1:13001 nginx gateway
```

Internal runtime behavior:

```text
nginx -> 127.0.0.1:13002 Next.js UI
nginx -> 127.0.0.1:18001 FastAPI backend
backend -> 127.0.0.1:18002 manager-agent
```

Only the nginx gateway port should be shown to users. The other ports are
implementation details and should be generated automatically by install/deploy
scripts.

## Why Defer

The current installer phase already changes packaging, runtime bootstrap,
release deployment, service generation, rollback, and offline dependency
handling. Port consolidation touches runtime protocols between services and can
create avoidable regressions in streaming, uploads, health checks, and manager
agent calls.

The next installer milestone should therefore focus on:

- one-line public install UX
- user-mode runtime bootstrap
- no mandatory `sudo`
- no mandatory `apt`
- deterministic release payload validation
- stable `systemd --user` service generation
- clear diagnostics for host-level blockers

Port consolidation should not block those goals.

## Current Port Roles

| Port | Service | Role | User-visible |
| --- | --- | --- | --- |
| `13001` | nginx | single browser gateway | yes |
| `13002` | Next.js standalone | UI server behind nginx | no |
| `18001` | FastAPI backend | API server behind nginx and Next rewrites | no |
| `18002` | manager-agent | backend-to-manager sidecar | no |

The current upload path already uses nginx to bypass Next.js for large uploads:

```text
/upload-api/* -> FastAPI /api/*
```

This is the right direction. It reduces upload risk without forcing an immediate
frontend deployment model rewrite.

## Target Direction

The product target is:

- users configure or see only one port
- nginx remains the browser-facing gateway
- backend and manager-agent should eventually stop consuming TCP ports
- Next.js may remain a private TCP listener unless the frontend is migrated away
  from the standalone server model

Practical next target:

```text
browser -> 127.0.0.1:13001 nginx
nginx -> 127.0.0.1:13002 Next.js UI
nginx -> unix:backend.sock FastAPI backend
backend -> unix:manager-agent.sock manager-agent
```

This reduces TCP listeners from four to two while preserving Next.js standalone.

## Recommended Sequence

### Step 1. Keep User UX To One Port

Do this during the installer phase.

Requirements:

- installer output prints only `http://127.0.0.1:13001`
- internal ports are described as diagnostics only
- installer preflight checks user-facing port conflicts first
- internal port conflicts are auto-resolved where possible or reported as
  implementation-level diagnostics

No protocol migration is required for this step.

### Step 2. Let Nginx Own API Routing

Move normal API traffic to nginx instead of relying on Next.js API proxy routes.

Target:

```text
/api/* -> FastAPI /api/*
/upload-api/* -> FastAPI /api/*
/* -> Next.js UI
```

Expected changes:

- update nginx template
- remove or de-emphasize Next.js `/app/api/[...path]/route.ts`
- keep frontend `NEXT_PUBLIC_API_BASE_URL=/api`
- keep `NEXT_PUBLIC_UPLOAD_API_BASE_URL=/upload-api`
- verify SSE/chat stream behavior through nginx
- verify large upload behavior remains direct to FastAPI

This step simplifies request routing but does not reduce TCP listeners by itself.

### Step 3. Move FastAPI To Unix Socket

Replace backend TCP listener with a Unix domain socket.

Target:

```text
uvicorn app.main:app --uds ~/.local/share/blueprint-re/run/backend.sock
nginx proxy_pass http://unix:.../backend.sock:/api/
```

Expected changes:

- add release runtime directory such as
  `~/.local/share/blueprint-re/run/`
- update backend systemd unit to use `--uds`
- update nginx template to proxy `/api/` and `/upload-api/` to the socket
- update health checks to go through nginx or use a socket-aware local check
- ensure stale socket files are removed before service start

Risk level: moderate. Uvicorn and nginx both support Unix sockets, but streaming
and upload timeout behavior must be re-tested.

### Step 4. Move Manager-Agent To Unix Socket

Replace manager-agent TCP listener with a Unix domain socket.

Target:

```text
backend -> unix:manager-agent.sock manager-agent
```

Expected changes:

- teach `manager-agent/src/server.js` to listen on a Unix socket when
  `MANAGER_AGENT_SOCKET` is set
- teach backend manager service to call the manager-agent over Unix socket
- keep TCP fallback for developer mode
- update release env generation to prefer socket mode
- update manager-agent health checks

Risk level: moderate. The backend currently uses `urllib.request` against a URL,
so it needs a small socket-aware HTTP client path or a lightweight dependency
that supports Unix socket HTTP.

### Step 5. Re-evaluate Next.js Standalone Port

Only consider this after the installer and backend/manager socket migration are
stable.

Options:

- keep Next.js standalone on `127.0.0.1:13002`
- migrate the frontend to static export or SPA-style serving from nginx
- replace the frontend build system with a purely static deployment model

Reducing to exactly one TCP listener requires eliminating the private Next.js
TCP server or making it listen on a Unix socket. This is higher cost because the
current frontend uses Next App Router and standalone output.

## Acceptance Criteria

Installer-phase acceptance:

- users see one URL: `http://127.0.0.1:13001`
- no mandatory port configuration is required for normal install
- default install still starts backend, frontend, manager-agent, and nginx
- uploads and chat streams work through nginx

Port-consolidation-phase acceptance:

- backend no longer listens on TCP
- manager-agent no longer listens on TCP
- nginx remains the only user-facing TCP listener
- Next.js private TCP listener is either retained intentionally or replaced by a
  separate frontend deployment migration
- rollback restores the previous service topology cleanly

## Non-Goals For Installer Phase

- do not remove Next.js standalone server
- do not migrate backend or manager-agent to Unix sockets during the installer
  stabilization phase
- do not change frontend routing model only to reduce internal ports
- do not make port consolidation a blocker for tagged release packaging
