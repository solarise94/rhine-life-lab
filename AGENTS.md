# Repository Guidelines

## Project Structure & Module Organization
`backend/` contains the FastAPI service. Core API routes live in `backend/app/api`, domain models in `backend/app/models`, orchestration logic in `backend/app/services`, and executor adapters in `backend/app/workers`. Backend tests are in `backend/tests`.

`frontend/` is a Next.js 15 app. Route entrypoints are under `frontend/app`, reusable UI lives in `frontend/components`, and client helpers, stores, and API bindings are in `frontend/lib`. `manager-agent/` is a small Node service that hosts the Manager AI sidecar. Deployment templates are in `deploy/`, developer scripts are in `scripts/`, reference docs are in `docs/`, and runtime project data is persisted under `workspace/`.

## Build, Test, and Development Commands
Backend setup and run:

```bash
python3 -m venv .venv/backend
.venv/backend/bin/pip install -e backend
.venv/backend/bin/python scripts/generate_backend_schemas.py
.venv/backend/bin/uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

Frontend run and build:

```bash
cd frontend
npm install
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000/api npm run dev
npm run build
```

Manager agent:

```bash
cd manager-agent
npm install
npm start
node --check src/server.js
```

## Coding Style & Naming Conventions
Follow existing style rather than introducing a new formatter. Python uses 4-space indentation, type hints, `snake_case` modules, and small service-focused functions. TypeScript and JS use 2-space indentation, double quotes, `PascalCase` for React components, and `camelCase` for hooks, helpers, and store methods.

## Testing Guidelines
Backend tests use `unittest`. Add new coverage in `backend/tests/test_*.py`, especially around service flows, patch validation, and run lifecycle logic.

Run tests with:

```bash
PYTHONPATH=backend .venv/backend/bin/python -m unittest discover -s backend/tests
```

There is no frontend test suite configured yet; after UI changes, run `cd frontend && npm run build` to catch type and build regressions.

After changes that touch manager prompts/tool schemas, also run:

```bash
cd manager-agent
node --check src/server.js
```

## Executor Runtime Notes
Executor soft sandboxing is a required deployment dependency when `BLUEPRINT_EXECUTOR_SANDBOX_MODE=bwrap`.

`bwrap` is available in the real host environment for this project. A direct smoke test from Codex's default tool sandbox can incorrectly fail with `No permissions to create new namespace`; that indicates the outer Codex tool sandbox blocked namespace creation, not that the host lacks bubblewrap support. When validating bubblewrap behavior, run the actual `bwrap` smoke test in the approved real execution context. Do not change the implementation to silently fall back to unsandboxed execution, and do not run `sudo codex`.

The executor bwrap profile intentionally keeps host networking enabled but uses `--clearenv`; required runtime env must be added explicitly to `command_worker.py` so it appears in `sandbox_plan.json`. Keep tool state such as `HOME`, `XDG_*`, and `PI_CODING_AGENT_*` inside the current `runs/<run_id>/` directory. Do not write new secrets into `commands.log`; command logging must redact key/token/password style arguments.

Python and R runtimes are now separate execution bindings. When adding run-launch or rerun fields, keep `python_runtime` and `r_runtime` wired consistently from frontend API calls through `backend/app/api/runs.py`, `WorkerService`, `task_packet.json`, and `ExecutorContext.runtime_bindings`. Treat `__system__` as "no explicit runtime binding" rather than a named environment.

For R-capable runs, prefer the resolved `BLUEPRINT_RSCRIPT` path when present. Do not assume the selected Python conda environment also provides `Rscript`, and keep any R library path handling explicit in the sandbox/environment plan.

Project snapshots may expose both `python_runtimes` and `r_runtimes`; frontend runtime selectors and persisted workspace UI state should stay in sync with both lists.

Script-language preference is a soft planning hint, not an executor hard constraint. Pass `script_preference` through chat context, and when Manager creates or updates analysis cards, store the resulting guidance in `card.executor_context.instruction_blocks` instead of introducing special-case execution logic.

Runtime dependencies are declared in `deploy/runtime-dependencies.yml`, and `scripts/deploy_user_systemd.sh` must fail deployment if the bubblewrap smoke test fails in the real deployment environment.

## Manager Project Memory Notes
Project memory is intentionally narrow. Store only explicit long-term `user_preference` and `correction_memory` records, such as remembered plotting/report style preferences or durable corrections the Manager should not repeat. Do not store blueprint execution facts, card state, asset state, run state, or ordinary chat history in project memory.

The blueprint remains the source of truth for project execution facts. Manager should use blueprint/card/asset/run tools to infer project state, and use project memory only to guide how work is planned, explained, or corrected. Keep memory summaries short and avoid injecting unrelated memory into every model turn.

## Commit & Pull Request Guidelines
Recent commits use short, imperative subjects such as `Add runtime approval flow for executors` and `Clarify module group aggregate status`. Keep commit titles concise, capitalized, and focused on one change.

Pull requests should explain the user-visible behavior change, list verification steps, and note any `.env`, schema, or deployment impact. Include screenshots for frontend changes and call out updates that affect `workspace/` project data or generated schemas.

## Security & Configuration Tips
Keep secrets in the repository-root `.env`; never commit API keys or tokens. If you change Pydantic models or patch schemas, regenerate `backend/app/schemas/*.json` before opening the PR.
