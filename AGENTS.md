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

## Commit & Pull Request Guidelines
Recent commits use short, imperative subjects such as `Add runtime approval flow for executors` and `Clarify module group aggregate status`. Keep commit titles concise, capitalized, and focused on one change.

Pull requests should explain the user-visible behavior change, list verification steps, and note any `.env`, schema, or deployment impact. Include screenshots for frontend changes and call out updates that affect `workspace/` project data or generated schemas.

## Security & Configuration Tips
Keep secrets in the repository-root `.env`; never commit API keys or tokens. If you change Pydantic models or patch schemas, regenerate `backend/app/schemas/*.json` before opening the PR.
