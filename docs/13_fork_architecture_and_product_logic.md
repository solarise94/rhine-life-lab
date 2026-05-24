# Fork Architecture And Product Logic

This document describes the current Blueprint RE v3 implementation as a fork guide. It is written for creating a new system for full-cycle scientific experiment data management, execution tracking, and report generation.

## 1. Current Product Shape

Blueprint RE v3 is a Git-native project management application for AI-assisted scientific analysis. The user works through a Manager chat, and the application maintains a project blueprint made of cards, graph entities, runs, assets, claims, and report sections.

The important product rule is:

- The user does not directly edit the low-level graph.
- Manager is the primary interface for planning and changing the blueprint.
- Cards are the visible work units.
- Runs are execution attempts attached to cards.
- Assets are the data/result files produced or uploaded.
- Claims are accepted scientific findings derived from assets.
- Report items are selected summaries prepared for export.
- Git commits are used as an audit trail for accepted changes and reviewed run outputs.

For a fork aimed at full-cycle scientific experiment management, keep this architecture. Rename the domain language rather than replacing the core mechanics:

- `Project` -> study / research project / experiment program.
- `Card` -> experiment step, data processing step, analysis task, QC task, report task.
- `Run` -> execution record, instrument batch, analysis job, review cycle.
- `Asset` -> raw data, processed data, QC output, figure, table, protocol, report artifact.
- `Claim` -> validated result, conclusion, deviation, QC finding.
- `ReportItem` -> report section, regulatory section, internal summary section.

## 2. Runtime Components

The application has three runtime services.

### Frontend

Path: `frontend/`

Stack:

- Next.js 15
- React 19
- TypeScript
- TanStack Query for server state
- Zustand for local workspace UI state
- ECharts for charts
- React Markdown for manager responses

Responsibilities:

- Project navigation and workspace layout.
- Manager chat UI with timeline items for thinking, tool use, compaction, and text.
- Card canvas and card detail display.
- Run event panel and runtime approval UI.
- Result/file/report/advanced views.
- Artifact preview drawer.
- Local UI persistence such as selected card, current chat session, draft input, selected runtime, and preview drawer state.

Primary entry points:

- `frontend/components/layout/ProjectWorkspace.tsx`
- `frontend/components/manager-chat/ManagerChatPanel.tsx`
- `frontend/lib/api.ts`
- `frontend/lib/hooks.ts`
- `frontend/lib/stores/workspace-ui-store.ts`
- `frontend/app/globals.css`

### Backend

Path: `backend/`

Stack:

- FastAPI
- Pydantic
- Local JSON persistence
- Git-backed project directories
- Worker adapters for external agent CLIs

Responsibilities:

- Serve project state, cards, runs, results, files, reports, chat sessions, and advanced graph views.
- Validate and persist graph/card/project data.
- Start executor runs and reconcile run states.
- Build task packets for workers.
- Validate executor manifests and produced artifacts.
- Provide internal Manager tools behind an internal bearer token.
- Store project memory, chat sessions, and artifact pointers.

Primary entry points:

- `backend/app/main.py`
- `backend/app/core/config.py`
- `backend/app/core/paths.py`
- `backend/app/api/*.py`
- `backend/app/services/project_service.py`
- `backend/app/services/graph_store.py`
- `backend/app/services/manager_service.py`
- `backend/app/services/manager_blueprint_tools.py`
- `backend/app/services/worker_service.py`
- `backend/app/workers/*.py`

### Manager Sidecar

Path: `manager-agent/`

Stack:

- Node.js service
- `@earendil-works/pi-agent-core`
- `@earendil-works/pi-ai`
- DeepSeek-compatible model configuration
- Optional Tavily web search/extract

Responsibilities:

- Run the Manager agent loop.
- Stream thinking/text/tool events to the frontend through the backend.
- Call backend internal tools to inspect or edit the blueprint.
- Compact long chat context with pi-agent-core compaction primitives.
- Optionally call Tavily search/extract when web search is enabled.

Primary entry point:

- `manager-agent/src/server.js`

The sidecar intentionally exposes only controlled tools. It is not a general shell/edit agent. This is the main safety boundary for Manager.

## 3. Project Data Layout

Runtime project data is stored under `workspace/<project_id>/` by default.

Current scaffold:

```text
workspace/<project_id>/
  project.json
  graph/
    graph.json
    cards.json
    modules.json
    assets.json
    claims.json
    runs.json
    report.json
    proposals.json
    cleanup.json
    patches/
      <patch_id>.json
  chat/
    sessions.json
  memory/
    project_memory.json
  runs/
    <run_id>/
      task_packet.json
      events.json
      transcript.md
      commands.log
      manifest.json
      sandbox_plan.json
  results/
  reports/
  artifacts/
    pointers/
      <artifact_id>.json
  artifact_store/
    sha256/
  scripts/
    generated/
    curated/
  configs/
    params.yaml
  data/
```

Persistence is file-based and intentionally simple:

- `GraphStore` reads/writes JSON files using Pydantic validation.
- `ProjectService` creates project scaffolds, loads snapshots, and owns per-project locks.
- `GitService` commits accepted project changes and reviewed run outputs.
- Large/raw data are excluded from Git by project `.gitignore`.

This is a good base for a new scientific data-management fork because it gives auditability without requiring a database at the start. If multi-user collaboration, indexing, permissions, or high-volume search become requirements, introduce a database later while keeping this file layout as the export/audit format.

## 4. Core Domain Models

### Project

Model: `backend/app/models/project.py`

`ProjectState` contains identity and lifecycle state:

- `project_id`
- `name`
- `status`
- `schema_version`
- `current_goal`
- timestamps

Fork suggestion:

- Add fields only if they are project-wide and stable: lab, PI, study code, protocol number, sponsor, data classification.
- Avoid pushing dynamic workflow state into `ProjectState`; use graph entities or cards.

### Cards

Model: `backend/app/models/cards.py`

Cards are the main UI and workflow units. They are deliberately denormalized so the user can understand the blueprint without opening the graph.

Important fields:

- `card_id`
- `card_type`
- `title`
- `status`
- `step`
- `summary`
- `why`
- `inputs`
- `outputs`
- `key_findings`
- `manager_review`
- `next_actions`
- `linked_modules`
- `linked_runs`
- `linked_assets`
- `executor_context`

Current statuses include `proposed`, `planned`, `running`, `reviewing`, `needs_review`, `accepted`, `failed`, `cancelled`, and similar states.

Fork suggestion:

- Keep cards as the primary user-facing work units.
- Add new card types only when the UI behavior is materially different. For example: `experiment`, `sample_batch`, `qc`, `analysis`, `report_section`.
- Keep `executor_context`; it is the bridge between planning and execution permissions/runtime requirements.

### Graph

Model: `backend/app/models/graph.py`

The graph contains normalized project facts:

- `Module`: planned or accepted analysis module / workflow module.
- `Asset`: uploaded or produced file/data object.
- `Claim`: validated finding or conclusion.
- `RunRecord`: execution lifecycle record.
- `ReportItem`: selected report content.

Fork suggestion:

- For experiment management, add new entities only if they must be shared across multiple cards/runs/assets. Likely additions are `Sample`, `Subject`, `Assay`, `InstrumentRun`, and `Protocol`.
- If you add those entities, update `GraphState`, `GraphStore`, schemas, frontend types, and project snapshot serialization together.

### Executor Context

Model: `backend/app/models/executor.py`

`ExecutorContext` controls how a card should be executed:

- `executor_profile`
- `skills`
- `instruction_blocks`
- `references`
- `tool_policy`
- `runtime_bindings`

`ExecutorToolPolicy` currently controls:

- `network`
- `python`
- `rscript`
- `shell`
- `git_write`

Fork suggestion:

- This is where experiment-specific execution constraints should live: instrument software, container image, compute queue, validation profile, SOP references.
- Do not encode execution permissions only in prompts. Keep them structured here so backend and UI can inspect them.

### Runs And Task Packets

Model: `backend/app/models/runs.py`

A run is started from a card. Backend builds a `TaskPacket` and gives it to a worker adapter. The packet includes:

- project/run/card identity
- goal
- input assets
- card inputs/outputs
- expected outputs
- allowed/readonly/forbidden paths
- execution policy
- worker instructions
- run context
- executor context
- manager reporting contract

The executor writes a manifest describing outputs, commands, code artifacts, metrics, findings, warnings, and recommended graph updates.

Fork suggestion:

- Treat `TaskPacket` as the execution contract between the management system and any external worker, whether that worker is an AI agent, a scripted pipeline, an instrument parser, or a human-operated batch import.
- Extend `Manifest` for experiment-specific evidence such as QC metrics, sample exclusions, chain-of-custody, reagent lots, instrument metadata, and report-ready figures.

### Chat, Timeline, Compact

Model: `backend/app/models/chat.py`

Chat sessions are persisted in `chat/sessions.json`. Each message may include a `timeline` with:

- text items
- thinking items
- tool items
- compact items

Token usage and compact metadata are persisted so the frontend can show context state and reload the conversation accurately.

Fork suggestion:

- Keep timeline as the canonical chat UI model.
- Add new timeline kinds only for user-visible events that matter operationally, such as `data_import`, `validation`, or `approval`.

### Project Memory

Model: `backend/app/models/memory.py`

Project memory is intentionally small. It stores:

- `user_preference`
- `correction_memory`

It is not the source of project execution facts. Project facts should come from the blueprint/graph/cards/assets/runs.

Fork suggestion:

- Keep memory small and preference-oriented: report style, plotting style, recurring terminology, user corrections.
- Do not store every execution detail in memory. That would duplicate the graph and waste context.
- Manager should read memory when user preference or correction may affect response style or planning; it should write memory only for explicit preferences/corrections.

## 5. API Surface

All public API routes are mounted under `/api`.

### Project And Blueprint

- `GET /projects`
- `POST /projects`
- `DELETE /projects/{project_id}`
- `GET /projects/{project_id}`
- `GET /projects/{project_id}/cards`
- `GET /projects/{project_id}/asset-flow`
- `GET /projects/{project_id}/work-order`

### Manager Chat

- `POST /projects/{project_id}/chat`
- `POST /projects/{project_id}/chat-stream`
- `POST /projects/{project_id}/chat-compact`
- `POST /projects/{project_id}/chat-jobs`
- `GET /projects/{project_id}/chat-jobs/{job_id}`
- `POST /projects/{project_id}/chat-uploads`

The frontend primarily uses streaming chat now. `/compact` in the UI calls `chat-compact`.

### Chat Sessions

- `GET /projects/{project_id}/chat-sessions`
- `POST /projects/{project_id}/chat-sessions`
- `GET /projects/{project_id}/chat-sessions/{session_id}`
- `PUT /projects/{project_id}/chat-sessions/{session_id}`
- `DELETE /projects/{project_id}/chat-sessions/{session_id}`

### Internal Manager Tools

Mounted under `/api/internal/manager-tools/...` and protected by `BLUEPRINT_INTERNAL_TOOL_TOKEN`.

Tools exposed through backend:

- `get_project_context`
- `list_data_assets`
- `create_card`
- `update_card`
- `configure_card_execution`
- `delete_card`
- `get_tool_policy`
- `set_tool_policy`
- `read_result_asset`
- `list_project_memory`
- `write_project_memory`

Only the Manager sidecar should call these routes. The browser should not depend on them directly.

### Runs

- `POST /projects/{project_id}/cards/{card_id}/start-run`
- `POST /projects/{project_id}/cards/{card_id}/reset-run-state`
- `POST /projects/{project_id}/cards/{card_id}/rerun`
- `GET /projects/{project_id}/runs/{run_id}`
- `GET /projects/{project_id}/runs/{run_id}/events`
- `GET /projects/{project_id}/runs/{run_id}/manifest`
- `POST /projects/{project_id}/runs/{run_id}/review`
- `POST /projects/{project_id}/runs/{run_id}/cancel`
- `POST /projects/{project_id}/runs/{run_id}/cleanup`
- `GET /projects/{project_id}/runs/{run_id}/runtime-approvals`
- `POST /projects/{project_id}/runs/{run_id}/runtime-approvals/{request_id}`
- `WS /projects/{project_id}/runs/{run_id}/ws`

### Results, Files, Report, Advanced

- `GET /projects/{project_id}/results`
- `GET /projects/{project_id}/results/{asset_id}`
- `GET /projects/{project_id}/results/{asset_id}/content`
- `GET /projects/{project_id}/files`
- `GET /projects/{project_id}/files/content`
- `DELETE /projects/{project_id}/files/session-uploads/{asset_id}`
- `DELETE /projects/{project_id}/files/assets/{asset_id}`
- `GET /projects/{project_id}/report`
- `POST /projects/{project_id}/report/reorder`
- `POST /projects/{project_id}/report/export-html`
- `GET /projects/{project_id}/graph`
- `GET /projects/{project_id}/git`

## 6. Manager Logic

Manager has two layers:

1. Backend `ManagerService` forwards chat/stream/compact requests to the sidecar when `BLUEPRINT_MANAGER_BACKEND=pi`.
2. Node sidecar runs the actual agent and exposes a constrained tool set.

### Prompt Contract

The Manager is instructed to:

- Answer directly for general conceptual questions.
- Use tools when inspecting or changing project state.
- Treat cards as the source of blueprint changes.
- Use `create_card`, `update_card`, and `delete_card` for blueprint edits.
- Use `configure_card_execution` for execution permission/runtime changes.
- Use `list_project_memory` and `write_project_memory` only for preferences and corrections.
- Use web tools only when external/current information is required and enabled.
- Never claim a blueprint change is complete unless the tool call succeeded.

### Tool Call Flow

```text
User message
  -> Frontend ManagerChatPanel
  -> Backend /chat-stream
  -> ManagerService
  -> manager-agent /chat-stream
  -> pi-agent-core model loop
  -> tool call
  -> backend internal manager-tools route
  -> GraphStore / ProjectService mutation
  -> streamed tool event back to frontend
```

### Compact Flow

Automatic and manual compaction are handled in the sidecar. Manual `/compact` in the frontend calls:

```text
Frontend /compact command
  -> api.compactChatSession
  -> Backend /chat-compact
  -> ManagerService.compact_chat_session
  -> manager-agent /compact
  -> pi-agent-core compaction
  -> compact timeline item persisted in chat session
```

Environment knobs:

- `MANAGER_CONTEXT_WINDOW_TOKENS`
- `MANAGER_COMPACTION_ENABLED`
- `MANAGER_COMPACTION_KEEP_RECENT_TOKENS`
- `MANAGER_COMPACTION_RESERVE_TOKENS`

### Web Search Flow

When enabled, sidecar adds:

- `web_search`
- `web_extract`

Environment knobs:

- `MANAGER_WEBSEARCH_ENABLED`
- `TAVILY_API_KEY`
- `TAVILY_BASE_URL`

Web search is sidecar-only. Backend does not need Tavily integration.

## 7. Execution Logic

Execution is card-centered.

```text
User/Manager creates planned card
  -> User starts run from card
  -> WorkerService validates card can start
  -> WorkerService builds TaskPacket
  -> Worker adapter builds launch spec
  -> RuntimeApprovalService reviews permission requests
  -> RunRecord + initial RunEvent are saved
  -> Worker process starts asynchronously
  -> Worker writes manifest and artifacts
  -> Backend validates manifest/artifacts
  -> Manager/reviewer decides accept/reject
  -> Graph/cards/assets/claims/report are updated
  -> Git commit records accepted state
```

Worker adapters currently include:

- `pi`
- `opencode`
- `claude_code`
- `codex`

The adapter abstraction is in `backend/app/workers/base.py`. Actual provider launch templates are configured through environment variables such as:

- `BLUEPRINT_PI_COMMAND`
- `BLUEPRINT_OPENCODE_COMMAND`
- `BLUEPRINT_CLAUDE_CODE_COMMAND`
- `BLUEPRINT_CODEX_COMMAND`

The sandbox path is important:

- `BLUEPRINT_EXECUTOR_SANDBOX_MODE=bwrap`
- Bubblewrap is expected on the real host.
- Do not silently fall back to unsandboxed execution.
- Runtime environment must be explicitly passed into the sandbox plan.
- Secrets must be redacted from command logs.

Fork suggestion:

- Keep WorkerAdapter as the extension point.
- Add new adapters for instrument import, LIMS sync, batch QC, HPC submission, or validated pipelines.
- Preserve `TaskPacket` and `Manifest` as stable contracts.

## 8. Frontend Product Logic

The workspace is a two-pane scientific workbench:

- Left: Manager chat.
- Right: current project blueprint and task/result/report surfaces.

Views:

- `tasks`: cards, run details, runtime approvals, run events.
- `results`: accepted/candidate/other assets.
- `files`: project file browser and uploaded assets.
- `report`: report builder and export.
- `advanced`: raw graph, proposals, Git history.

State split:

- Server state: TanStack Query hooks in `frontend/lib/hooks.ts`.
- Local UI state: Zustand store in `frontend/lib/stores/workspace-ui-store.ts`.
- API bindings and stream event types: `frontend/lib/api.ts`.
- Shared TS types: `frontend/lib/types.ts`.

Key UI components:

- `ProjectWorkspace`: orchestrates data loading and view composition.
- `ManagerChatPanel`: chat, slash commands, timeline, attachments, context ring.
- `CardStream`: card canvas.
- `CardDetailPanel`: selected card detail.
- `RunEventsPanel`: run logs, approvals, review actions.
- `ResultsGrid`: result surface.
- `ResultPreviewPanel`: artifact preview drawer.
- `ReportBuilder`: report construction.
- `AdvancedPanels`: graph/git inspection.

Fork suggestion:

- For full-cycle experiment management, keep the layout but add domain-specific views gradually:
  - Samples
  - Protocols
  - Instruments
  - QC
  - Reports
  - Audit
- Do not remove `tasks/results/files/report/advanced` until the replacement views cover the same operational needs.

## 9. UI Design Logic And Fork Recommendations

The current UI is designed around a "manager cockpit + living blueprint" pattern. The important decision is that the AI conversation is not a separate chatbot. It is the control surface for the project, and the right side is the live operational state that proves whether the Manager's plan actually exists.

### Current UI Mental Model

The interface has four conceptual layers:

- Conversation layer: Manager chat, slash commands, context window ring, attachments, thinking/tool/compact timeline.
- Blueprint layer: card stream, card states, inputs/outputs, execution status, run actions.
- Evidence layer: run events, manifests, result assets, file previews, reports.
- Audit layer: advanced graph, proposals/history, Git state, persisted sessions.

The user should be able to answer three questions without opening raw JSON:

- What is the project currently trying to do?
- Which step is blocked/running/done?
- What evidence or output supports the current conclusion?

For the fork, preserve that mental model. A scientific experiment platform will need more domain objects, but the UI should still orbit around plan, execution, evidence, and report.

### Current Layout Rationale

The default workspace is split into a left Manager panel and a right project surface.

Left panel:

- Manager chat stays visible because planning, correction, and explanation are continuous.
- Timeline items are embedded inside the conversation instead of shown as global banners.
- Thinking, tool use, and compact events are local to the exact turn where they happened.
- Attachments let users point the Manager to a card or asset without copying IDs manually.
- Slash commands are intentionally lightweight. `/compact` is a control command, not a separate settings page.

Right panel:

- Cards are the primary blueprint view because cards are easier to reason about than graph nodes.
- Card detail is secondary and should not steal space from the main canvas unless the user asks.
- Run events and runtime approvals are shown close to the selected card because they are operational state, not general notifications.
- Results/files/report are separate views because they represent different evidence workflows.
- Advanced view exists for inspection and debugging, not as the main editing path.

Fork recommendation:

- Keep the left Manager visible in most project views.
- Keep operational feedback in-context rather than adding persistent top banners.
- Keep cards or step tiles as the main work surface even if the backend graph becomes richer.
- Treat raw graph/schema/debug information as an advanced view.

### Interaction Principles

Current interaction rules:

- Manager can propose or directly modify controlled blueprint objects through tools.
- User sees visible card/result changes, not only a chat answer.
- Tool calls are represented as short timeline dividers such as "已查看蓝图" or "已更改卡片".
- Thinking is expandable while running and collapsed after completion.
- Compact has its own visible timeline state because context mutation affects later answers.
- Errors should be local to the relevant chat turn, card, run, or toast-style notice.
- Long-running executor state belongs to cards and run panels, not to the global page chrome.

For the fork:

- Make domain actions visible as timeline events: "已登记样本", "已读取仪器批次", "已生成 QC 报告".
- Prefer local state indicators over app-wide alert bars.
- Use explicit status language for regulated/scientific work: draft, queued, running, needs review, accepted, rejected, superseded.
- Let the Manager explain what changed, but make the UI show the changed object.

### Visual Hierarchy

The current UI uses a soft scientific dashboard style:

- Rounded cards and composer controls.
- Pale surfaces instead of dense enterprise tables.
- Subtle dividers for tool calls and thinking blocks.
- Card status badges for fast scanning.
- Result previews in a drawer so the user does not lose workspace context.
- Thin scrollbars and local overflow regions to avoid page-level jumping.

Fork recommendation:

- Keep the visual style calm and evidence-oriented.
- Avoid turning the system into a generic admin dashboard with dense tables everywhere.
- Use tables for sample sheets, QC metrics, and audit logs, but keep planning and review in card/timeline form.
- Use preview drawers for artifacts so users can inspect evidence without leaving the workflow.
- Use restrained animation for state transitions: card selection, preview drawer, compacting, run progress.

### Scientific Full-Cycle UI Model

For a full-cycle experiment data system, add domain views without removing the central cockpit:

- Overview: study goal, current phase, active blockers, latest accepted outputs.
- Samples: sample sheet, metadata completeness, lineage, QC flags.
- Protocols: SOP/protocol versions, expected inputs, deviations.
- Experiments: wet-lab batches, instrument runs, processing batches.
- Tasks: card workflow for planned/running/reviewing work.
- Results: figures, tables, processed datasets, QC outputs.
- Reports: report sections, selected claims, export history.
- Audit: decisions, approvals, run manifests, Git commits.

Recommended navigation shape:

- Keep project-level side nav.
- Keep Manager on the left or as a persistent dock.
- Let the right view switch between Samples/Experiments/Tasks/Results/Reports/Audit.
- When the user selects an object, use a right-side drawer or lower panel for details instead of navigating away.

### Object UI Patterns

Use different UI patterns for different object types:

- Cards/tasks: horizontal or grouped workflow canvas, because order and dependency matter.
- Samples: table-first with filters, because completeness and batch comparison matter.
- Assets/results: grid/list with preview, because file type and evidence inspection matter.
- Protocols: document/version view, because traceability matters.
- QC: dashboard plus table, because thresholds and outliers matter.
- Reports: outline builder plus preview, because narrative order matters.
- Audit: append-only timeline/table, because trust and traceability matter.

Do not force every domain object into a card. Cards should represent work and decisions. Samples, protocols, instruments, and assets should be first-class entities linked to cards.

### Manager Chat UI Recommendations

Keep these current choices:

- One-line composer that expands as text grows.
- Round send button and low-friction effort selector.
- Slash command hinting inside or immediately near the composer.
- Tool/thinking/compact events rendered inline with the turn.
- Attachments for cards/assets.
- Context window ring near the send action.

Add later:

- Slash command palette for `/compact`, `/memory`, `/report`, `/sample`, `/qc`.
- Mention picker for samples, protocols, runs, assets, and report sections.
- "What changed" mini-diff after Manager mutations.
- Per-turn citation/evidence chips when Manager references assets or report sections.
- Optional compact animation when automatic compaction starts.

Avoid:

- Persistent top banners for routine runtime/tool/chat state.
- Asking users to approve permissions inside card agents unless the question is routed back to Manager chat.
- Showing raw model/tool JSON in the default chat.
- Letting chat answers be the only evidence that a mutation happened.

### Artifact Preview Router

The artifact preview drawer is a core pattern for the fork. It should become the universal evidence viewer.

Current target types:

- Image previews.
- Table previews.
- Markdown/text previews.
- Binary fallback/download.

Fork extensions:

- PDF report preview.
- Notebook preview.
- Interactive HTML report preview with sandboxing.
- CSV/TSV profiler: columns, missingness, row count, quick histogram.
- Scientific image preview: microscopy, plots, gel images, spatial images.
- QC report preview with pass/fail thresholds.
- Provenance panel showing which run/card/sample produced the asset.

### Reporting UI Recommendations

The report builder should become more structured in the fork.

Recommended model:

- Report template defines required sections.
- Claims and assets can be attached to sections.
- Manager can draft narrative, but accepted evidence remains linked.
- Export history is preserved.
- Each section has status: draft, needs review, accepted.

UI pattern:

- Left outline: report sections.
- Center editor/preview.
- Right evidence drawer with linked claims/assets/runs.
- Manager can update a section through a controlled tool.

### UX Risks To Watch

High-risk UI areas during the fork:

- `ManagerChatPanel.tsx` is already complex. Split it before adding many more timeline event types.
- Card canvas can become crowded if every domain object becomes a card.
- Sample tables can overwhelm the workspace if not filtered by batch/status.
- Artifact preview can become slow if large files are fetched eagerly.
- localStorage UI state can become stale across tabs; avoid persisting heavy domain selections.
- Mobile layout should keep only the most important controls: chat, active task, preview.

### Recommended UI Refactor Before Major Fork Work

Before adding many new domain screens, consider splitting UI logic:

- Extract chat streaming merge logic from `ManagerChatPanel.tsx` into a reducer or hook.
- Extract timeline rendering into `ManagerTimeline.tsx`.
- Extract composer into `ManagerComposer.tsx`.
- Extract artifact preview routing into a dedicated preview registry.
- Introduce reusable object headers/status chips for samples, assets, cards, runs, and reports.
- Add a small design token section in CSS for domain status colors.

The main UI recommendation is to keep the product feeling like an AI-operated scientific workbench, not a generic LIMS clone. The Manager should coordinate, cards should show work, assets should show evidence, and reports should turn accepted evidence into a deliverable.

## 10. Dependency Map

### Required Runtime Dependencies

- Python 3.13 for backend.
- Node.js 22.19+ for manager-agent.
- Node.js compatible with Next.js 15 for frontend.
- Git for project audit commits.
- systemd user services for the current deployment path.
- Bubblewrap for sandboxed executor runs.

### Model And Search Dependencies

- DeepSeek API key for Manager and reviewer flows.
- Pi agent sidecar libraries for Manager tool loop and compaction.
- Optional Tavily API key for web search/extract.

### Frontend Dependencies

- `next`
- `react`
- `@tanstack/react-query`
- `zustand`
- `lucide-react`
- `react-markdown`
- `remark-gfm`
- `echarts`
- `echarts-for-react`
- `@monaco-editor/react`

### Backend Dependencies

- `fastapi`
- `uvicorn[standard]`
- `pydantic`
- `pydantic-settings`
- `orjson`
- `python-multipart`

### Manager-Agent Dependencies

- `@earendil-works/pi-agent-core`
- `@earendil-works/pi-ai`
- `typebox`

## 11. Configuration Map

Backend uses `BLUEPRINT_` environment variables through `backend/app/core/config.py`.

Important backend variables:

- `BLUEPRINT_DATA_ROOT`
- `BLUEPRINT_FRONTEND_ORIGIN`
- `BLUEPRINT_DEEPSEEK_API_BASE_URL`
- `BLUEPRINT_DEEPSEEK_API_KEY`
- `BLUEPRINT_MANAGER_MODEL`
- `BLUEPRINT_MANAGER_BACKEND`
- `BLUEPRINT_PI_MANAGER_URL`
- `BLUEPRINT_BACKEND_API_BASE_URL`
- `BLUEPRINT_INTERNAL_TOOL_TOKEN`
- `BLUEPRINT_DEFAULT_WORKER_TYPE`
- `BLUEPRINT_WORKER_TIMEOUT_SECONDS`
- `BLUEPRINT_EXECUTOR_SANDBOX_MODE`
- `BLUEPRINT_EXECUTOR_MAX_CONCURRENT_RUNS`
- `BLUEPRINT_EXECUTOR_CONDA_BASE`
- `BLUEPRINT_EXECUTOR_EXTRA_RO_BINDS`
- `BLUEPRINT_PI_COMMAND`
- `BLUEPRINT_OPENCODE_COMMAND`
- `BLUEPRINT_CLAUDE_CODE_COMMAND`
- `BLUEPRINT_CODEX_COMMAND`

Manager-agent variables:

- `MANAGER_AGENT_HOST`
- `MANAGER_AGENT_PORT`
- `MANAGER_AGENT_PROVIDER`
- `MANAGER_AGENT_MODEL`
- `MANAGER_AGENT_API_KEY`
- `MANAGER_AGENT_TIMEOUT_MS`
- `MANAGER_WEBSEARCH_ENABLED`
- `TAVILY_API_KEY`
- `TAVILY_BASE_URL`
- `MANAGER_CONTEXT_WINDOW_TOKENS`
- `MANAGER_COMPACTION_ENABLED`
- `MANAGER_COMPACTION_KEEP_RECENT_TOKENS`
- `MANAGER_COMPACTION_RESERVE_TOKENS`

Frontend variables:

- `NEXT_PUBLIC_API_BASE_URL`

## 12. Deployment Logic

Current deployment script:

- `scripts/deploy_user_systemd.sh`

It performs:

1. Backend virtualenv creation/update.
2. Backend editable install.
3. Frontend dependency install and production build.
4. Standalone frontend release copy.
5. Manager-agent dependency install.
6. User-level systemd service installation.
7. Service restart.

Services:

- `blueprint-re-backend.service`
- `blueprint-re-manager-agent.service`
- `blueprint-re-frontend.service`

Fork suggestion:

- Keep this deployment path for single-user workstation/server deployments.
- For multi-user lab deployments, plan a second deployment target with reverse proxy, authentication, persistent volume policy, backup policy, and secret management.

## 13. What To Keep For The New Fork

Keep these mostly intact:

- Three-service split: frontend, backend, manager sidecar.
- Project directory as audit/export unit.
- Cards as user-facing workflow units.
- GraphStore as first persistence layer.
- TaskPacket/Manifest contract.
- WorkerAdapter abstraction.
- Manager internal tool boundary.
- Chat timeline model.
- Project memory as small preference/correction store.
- Artifact preview router.
- Git audit commits.
- Bubblewrap sandbox expectation.

These parts are valuable because they separate planning, execution, review, and reporting without requiring a database or heavy workflow engine immediately.

## 14. What To Change For A Scientific Full-Cycle System

### Domain Model Additions

Likely new graph entities:

- `Sample`
- `Subject`
- `Assay`
- `Protocol`
- `Instrument`
- `InstrumentRun`
- `Batch`
- `QCMetric`
- `Deviation`
- `ReportTemplate`

Add them only when there is a concrete UI/API need. The current `Asset.metadata` can temporarily hold many of these facts during early prototyping.

### Product Views

Potential new views:

- `samples`: sample sheet, status, metadata completeness, lineage.
- `protocols`: SOP/protocol library and versioned references.
- `experiments`: wet-lab or instrument batches.
- `qc`: validation status, warnings, deviations, thresholds.
- `reports`: final report package, sections, export history.
- `audit`: Git commits, run manifests, decisions, approvals.

### Manager Tools

Add tools in the same pattern as current manager tools:

- Backend route under `/api/internal/manager-tools/...`.
- Service method in `ManagerBlueprintTools`.
- Tool schema in `manager-agent/src/server.js`.
- Prompt instruction describing when to use it.
- Frontend timeline label if the tool should be visible.

Candidate tools:

- `create_sample_batch`
- `update_sample_metadata`
- `register_instrument_run`
- `validate_qc_metrics`
- `link_asset_to_sample`
- `create_report_section`
- `record_deviation`

### Execution Adapters

Add adapters for:

- Deterministic Python/R pipelines.
- HPC submission.
- LIMS import/export.
- Instrument file parsing.
- Report rendering.
- Human-review-only tasks.

Do not overload the Manager sidecar to execute scientific computation. Manager should plan and coordinate; workers execute.

## 15. Fork Implementation Plan

### Phase 1: Rebrand And Stabilize Base

- Rename product labels in frontend and README.
- Keep API paths initially to reduce risk.
- Replace demo seed with a scientific experiment-management seed project.
- Add `docs/fork_domain_terms.md` mapping old terms to new terms.
- Verify frontend build and backend tests.

### Phase 2: Add Domain Entities Without Breaking Cards

- Add Pydantic models for sample/protocol/instrument entities.
- Extend `GraphState` and `GraphStore`.
- Add schemas via `scripts/generate_backend_schemas.py`.
- Add frontend types.
- Expose read-only views first.
- Keep cards as the planning/execution surface.

### Phase 3: Add Manager Tools For Domain Mutations

- Add internal backend tools for sample/protocol/batch operations.
- Register corresponding sidecar tools.
- Add tool labels for timeline display.
- Add tests for tool validation and graph persistence.

### Phase 4: Add Execution Integrations

- Add worker adapters for real pipelines or instrument imports.
- Extend `TaskPacket` and `Manifest` only where structured evidence is needed.
- Add artifact preview renderers for domain outputs.
- Add report export formats required by the new domain.

### Phase 5: Multi-User And Governance

- Add authentication.
- Add project-level roles.
- Move search/index workloads to a database if necessary.
- Add backup/restore.
- Add immutable audit export packages.

## 16. High-Risk Areas During Fork

### ManagerChatPanel Complexity

`ManagerChatPanel.tsx` owns streaming merge logic, timeline rendering, compact state, slash commands, attachments, and session persistence. Treat it carefully. For major changes, split logic into hooks/reducers before adding more event types.

### Graph Schema Expansion

Adding graph entities requires synchronized changes across:

- backend Pydantic models
- GraphStore
- project snapshot serialization
- JSON schema generation
- frontend `types.ts`
- frontend API consumers
- tests

### Worker Permissions

Execution permissions are structured but still subtle. Keep permission changes in `executor_context.tool_policy` and runtime approval services, not in model prose.

### File-Based Persistence

File persistence is excellent for single-user auditability but has limits:

- Cross-process locking is limited.
- Large listing/search can become slow.
- Multi-user conflict handling needs more work.

For the fork, keep files as the canonical audit export even if you add a DB.

### Web Search And Secrets

Web search is optional and should never receive local private project content or secrets. Keep external search separated from backend execution and project files.

## 17. Verification Commands

Backend:

```bash
PYTHONPATH=backend .venv/backend/bin/python -m unittest discover -s backend/tests
```

Frontend:

```bash
cd frontend
npm run build
```

Manager sidecar syntax:

```bash
node --check manager-agent/src/server.js
```

Deploy:

```bash
bash scripts/deploy_user_systemd.sh
```

Service checks:

```bash
systemctl --user status blueprint-re-backend.service
systemctl --user status blueprint-re-manager-agent.service
systemctl --user status blueprint-re-frontend.service
```

## 18. Minimal Fork Checklist

Before branching:

- Ensure `main` is clean.
- Keep this document with the fork branch.
- Decide the new domain vocabulary.
- Decide whether to keep API path names temporarily.
- Decide whether the first fork target is single-user or multi-user.

First fork commit:

- Rename visible product copy.
- Replace demo seed data.
- Update `.env.example`.
- Update README.
- Keep service boundaries unchanged.

First domain commit:

- Add one new graph entity, not all entities.
- Add one read-only frontend view.
- Add one Manager tool only if mutation is needed.
- Add backend tests for persistence and validation.

The main architectural recommendation is to preserve the existing planning/execution/review/report loop and evolve the domain model around it. The current framework is already close to a scientific workbench; the fork should focus on domain vocabulary, sample/protocol metadata, stronger artifact previews, and report templates rather than replacing the core orchestration.
