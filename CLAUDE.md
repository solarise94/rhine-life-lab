# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Blueprint RE v3 ("莱茵生命实验室") is a bioinformatics workflow management system. Users define analysis workflows as a DAG of "cards", execute them through sandboxed AI coding agents (executors), review results, and export reports. The primary AI provider is DeepSeek, with optional Anthropic/OpenAI support.

## Four-Service Architecture

```
Browser
    ↕ HTTP (127.0.0.1:13001)
nginx Gateway (:13001)
    ↕ /upload-api/* → :18001/api/*        (direct FastAPI, streaming uploads)
    ↕ /*           → :13002/*             (Next.js UI + normal APIs)
Frontend App (Next.js :13002, internal)
    ↕ HTTP proxy (/api/* → :18001/api/*)
Backend (FastAPI :18001)
    ↕ HTTP (bidirectional)
Manager Agent (Node.js :18002)   ←→   LLM APIs (DeepSeek/Anthropic/OpenAI)
    ↕                                    Tavily API (web search)
Backend spawns executor CLIs (pi, opencode, claude_code, codex)
    in bubblewrap sandbox → produces manifest.json + results
```

- **Backend** (`backend/`): Python 3.13+ FastAPI. API routes in `app/api/`, models in `app/models/`, services in `app/services/`, executor adapters in `app/workers/`. Entry point: `app/main.py`. Dependency injection via `lru_cache` singletons in `app/api/deps.py`.
- **Frontend** (`frontend/`): Next.js 15, React 19, TypeScript. App Router under `app/`. Components in `components/`. State: Zustand stores in `lib/stores/`, React Query hooks in `lib/hooks.ts`, API client in `lib/api.ts`.
- **Manager Agent** (`manager-agent/`): Single-file Node.js server (`src/server.js`, ~2700 lines). Uses `@earendil-works/pi-agent-core` + `@earendil-works/pi-ai`. The agent has ~30 tools that call back into the backend's `/internal/manager-tools/*` endpoints.

## Commands

### Backend

```bash
# Setup
python3.13 -m venv .venv/backend
.venv/backend/bin/pip install -e backend
.venv/backend/bin/python scripts/generate_backend_schemas.py

# Run (dev)
.venv/backend/bin/uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 18001

# Tests
PYTHONPATH=backend .venv/backend/bin/python -m unittest discover -s backend/tests
# Fast path (skip slow integration/timeout tests):
SKIP_SLOW_TESTS=1 PYTHONPATH=backend .venv/backend/bin/python -m unittest discover -s backend/tests
# Or with pytest:
.venv/backend/bin/python -m pytest backend/tests/ --tb=short -q
```

### Frontend

```bash
cd frontend
npm install
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:18001/api NEXT_PUBLIC_UPLOAD_API_BASE_URL=http://127.0.0.1:18001/api npm run dev
npm run build          # validation (no test framework)
```

### Manager Agent

```bash
cd manager-agent
npm install
npm start
node --check src/server.js    # syntax check after changes
```

### Deployment (systemd --user services)

```bash
bash scripts/install_blueprint_re.sh        # interactive install
bash scripts/deploy_user_systemd.sh          # unattended deploy from .env

# Service management
systemctl --user status blueprint-re-nginx.service
systemctl --user status blueprint-re-backend.service
systemctl --user status blueprint-re-manager-agent.service
systemctl --user status blueprint-re-frontend.service
systemctl --user restart blueprint-re-nginx.service
systemctl --user restart blueprint-re-backend.service
journalctl --user -u blueprint-re-nginx.service -n 100 --no-pager
journalctl --user -u blueprint-re-backend.service -n 100 --no-pager

# Health checks
curl -fsS http://127.0.0.1:18001/healthz
curl -I http://127.0.0.1:13001
```

## Key Architectural Patterns

**File-based persistence** — No database. All project state is JSON files on disk under `workspace/<project>/graph/` (cards.json, modules.json, assets.json, runs.json, etc.). Mutations use `atomic_write_json()` with per-project `RLock` guards. The `GraphStore` class in `backend/app/services/graph_store.py` provides typed load/save.

**Worker Adapter pattern** — `WorkerAdapter` base class (`backend/app/workers/base.py`) with concrete implementations for pi, opencode, claude_code, codex. Each produces a `WorkerLaunchSpec` (command, env, sandbox config). Provider-specific prompt rendering is in `backend/app/workers/provider_renderers/`.

**Bubblewrap sandboxing** — Executors run in `bwrap` with `--clearenv`. Runtime env vars must be explicitly whitelisted in `backend/app/workers/command_worker.py`. Post-run filesystem audit catches violations. Per-card lock + per-project semaphore prevent concurrent conflicts.

**Manager AI bidirectional tools** — The Manager Agent receives chat from the backend, then calls back into `/internal/manager-tools/*` to inspect/create/update cards, search assets, etc. Authenticated via `internal_tool_token`.

**Event-driven wake system** — `ManagerWakeService` / `ManagerWakeProcessor`: when runs complete/fail/block, wake events trigger auto-mode Manager responses without user intervention.

**Structured executor communication** — Executors emit `BP_EVENT` JSON lines on stdout (parsed as `ExecutorStructuredEvent`) and write `manifest.json` to the run directory. Backend validates manifest against the original `TaskPacket`.

## Coding Style

Follow existing local style — do not introduce a formatter:

- **Python**: 4 spaces, type hints, `snake_case`, small service-focused functions
- **TypeScript/JS**: 2 spaces, double quotes, `PascalCase` components, `camelCase` helpers/hooks/store methods
- Keep edits scoped. Prefer existing patterns over new abstractions.

## Important Constraints

- Default executor is `pi`. Other CLIs (opencode, claude_code, codex) are optional and partially supported — do not treat them as install blockers.
- `bwrap` is the required sandbox. Do not silently fall back to unsandboxed execution.
- Never hardcode user-specific paths (e.g., `/home/<user>/...`). Use `Path.home()`, `${HOME}`, or repo-relative paths.
- Keep secrets out of git. Never log tokens/keys into command logs.
- Command templates prefer `*_COMMAND_JSON` (JSON argv arrays) over shell strings for reliable path handling (especially WSL).
- `script_preference` is a soft planning hint — persist in `card.executor_context.instruction_blocks`, not as executor hard logic.
- Python and R runtimes are separate bindings (`python_runtime` vs `r_runtime`). `__system__` means "no explicit runtime binding".
- The repo-root `.env` is deploy input only. Runtime truth is `~/.config/blueprint-re/*.env`. Editing `.env` alone does not change running services.
- `deploy_user_systemd.sh` uses a whitelist-based `backend.env` write. When backend `Settings` gains a deployment-relevant field, update that whitelist.

## Verification After Changes

```bash
# After frontend changes
cd frontend && npm run build

# After manager-agent changes
node --check manager-agent/src/server.js

# After backend model/schema changes
.venv/backend/bin/python scripts/generate_backend_schemas.py

# After install/deploy changes — verify generated files and live services, not just script text
```

## Key Files

- `backend/app/core/config.py` — `Settings` class (pydantic-settings, env prefix `BLUEPRINT_*`), all 50+ config fields
- `backend/app/services/worker_service.py` — Run lifecycle (~2400 lines, the most critical service)
- `backend/app/services/manager_service.py` — Manager AI orchestration
- `backend/app/services/runtime_dependency_job_service.py` — Background dependency installation jobs (submit, persist, events, mark-resolved)
- `backend/app/services/runtime_dependency_state_service.py` — Normalized failure details, dedupe/cooling, blocker derivation
- `backend/app/services/runtime_dependency_resolver_service.py` — P1 deterministic resolver: per-package classification, policy-aware status, probe cache, fallback-grammar safety
- `backend/app/workers/command_worker.py` — bwrap sandbox command builder
- `manager-agent/src/server.js` — Manager Agent (single file, ~2700 lines)
- `frontend/lib/stores/workspace-ui-store.ts` — Primary Zustand UI state store
- `frontend/lib/api.ts` — Typed API client for all backend endpoints
- `AGENTS.md` — AI agent coding guidelines (supplementary to this file)
