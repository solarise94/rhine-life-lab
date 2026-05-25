# Manager Runtime Libraries, Script Reuse, and Report Export Plan

This document refines the next product round around four connected areas:

- keep Manager from narrating the entire DAG when the UI already shows it;
- persist runtime preferences at the project level instead of treating them as transient UI state;
- make exported reports discoverable from the UI;
- introduce reusable execution libraries in the right order: `Script Library` first, then `Skill Library`, then `MCP Library`.

The intent is to keep the execution stack coherent. `Script Library` should be part of the executor wrapper packaging flow, not a separate late-stage feature. `MCP Library` depends on that packaging shape, so it should come after `Script Library` is stable.

## Existing Sources Of Truth

- [docs/10_execution_spec_and_executor_context.md](./10_execution_spec_and_executor_context.md) already defines the wrapper optimizer, runtime profile concept, and the near-term MCP path.
- [docs/11_manager_context_websearch_and_artifact_preview_plan.md](./11_manager_context_websearch_and_artifact_preview_plan.md) already defines the artifact preview direction.
- [docs/14_manager_execution_speed_and_card_market_plan.md](./14_manager_execution_speed_and_card_market_plan.md) already defines card templates, script asset requirements/bindings, and template bundling.

This plan is a refinement and reordering of those ideas, not a new architecture.

## Recommended Order

1. Manager prompt restraint and project runtime preference persistence.
2. Report export visibility and open/download entry points.
3. Script Library plus wrapper packaging.
4. Skill Library.
5. MCP Library.

The reason for this order is simple: the executor wrapper needs a stable reusable script story before it becomes the container for skill/MCP selection.

## 1. Manager Should Not Re-Explain The DAG

### Problem

Manager often tends to describe the full DAG back to the user even though the UI already renders it.

### Rule

Manager should:

- refer to the visible DAG, not restate it;
- describe only the selected card, immediate blockers, and next action;
- summarize dependencies only when they are relevant to the current action;
- avoid full graph recaps unless the user explicitly asks for one.

### Prompt Change

Update the Manager sidecar prompt and planner prompt so they say the DAG is already visible in the UI and should not be re-narrated.

### Outcome

The user sees the graph in the workspace, and chat stays focused on decisions, blockers, and execution.

## 2. Runtime Preferences Must Persist Per Project

### Problem

Runtime preferences can be shown in the UI today, but they should not feel like temporary browser state.

### Target Model

Persist these project-level values:

- `script_preference`
- `python_runtime`
- `r_runtime`

The source of truth should live in project metadata or a dedicated project preference record, not only in local storage.

### UI Shape

Put runtime controls in a collapsible section in the side bar, alongside API settings.

- collapsed by default or remembered per project;
- shows a short summary line when collapsed;
- saves immediately to the backend;
- hydrates from the backend on refresh.

### Backend Shape

Use the already existing runtime fields where possible:

- `graph.metadata.default_conda_env`
- `graph.metadata.default_r_env`

If the current metadata shape is too narrow, add a single project preference object rather than spreading runtime state across unrelated models.

### Acceptance

- refresh does not reset the selected runtime;
- Manager and card execution both read the same project preference source;
- local UI state remains an optimistic cache, not the authoritative store.

## 3. Report Export Needs A Visible Entry Point

### Problem

The backend already writes the export to `reports/report.html`, but the UI does not clearly show where to open or download it.

### Target Behavior

After export, the user should have at least one obvious action:

- open preview;
- download HTML;
- copy file path.

### Preferred Integration

Treat the report export as a previewable artifact or a report asset, so it can go through the same artifact preview flow used by other outputs.

### UI Shape

Add a small report actions block near the export button or in the report builder header:

- `导出`
- `打开`
- `下载`
- `复制路径`

### Acceptance

- export returns a stable path;
- the user can immediately open or download the exported report;
- no more hidden export destination.

## 4. Script Library Comes Before MCP

### Why This Comes First

The wrapper is the execution envelope. `Script Library` belongs inside that envelope because it defines reusable code assets, bindings, and file rewrites that the wrapper needs before launch.

### Script Library Role

`Script Library` should store reusable executor-side code assets with metadata such as:

- name;
- summary;
- language;
- runtime requirements;
- hashes;
- dependency notes;
- expected inputs and outputs;
- source template or bundle path.

### How It Connects To The Wrapper

The wrapper should receive selected script assets as part of its launch packaging and use them to build:

- prompt file content;
- path rewrites;
- local bundle layout;
- execution context hints;
- reproducible helper script references.

### Card Template Contract

Keep the existing `script_asset_requirements` / `script_asset_bindings` split:

- `script_asset_requirements` belongs to the reusable template;
- `script_asset_bindings` belongs to the instantiated project card;
- bindings map requirements to real project-local assets.

### Manager Behavior

Manager can choose script assets while configuring a card, but does not need to dump full script bodies into chat.

### Acceptance

- a template can declare script roles without hardcoding project-local ids;
- the wrapper can package selected scripts reproducibly;
- the same template can be reused in another project with different bindings.

## 5. Skill Library

### Goal

Expose a library of skills that Manager can inspect and attach to cards without loading full skill bodies into context by default.

### Tooling

Add a Manager tool such as `list_skill_library` that returns only:

- skill name;
- summary;
- tags;
- enabled state;
- optional compatibility notes.

### UI

Add a config panel section for skills in the sidebar:

- browse installed skills;
- install or refresh from configured directories;
- enable or disable a skill;
- select skills when configuring a card.

### Card Integration

When a skill is selected for a card, write it into `executor_context.skills` so the wrapper can inject it into the executor launch context.

### Acceptance

- Manager can see the skill inventory without loading full skill content;
- selected skills are forwarded into the wrapper;
- unselected skills stay out of the prompt context.

## 6. MCP Library

### Goal

Expose MCP servers in the same general library pattern as skills, but treat them as runtime tool providers rather than plain prompts.

### Tooling

Add a Manager tool such as `list_mcp_library` that returns only summary metadata:

- server name;
- summary;
- supported runtimes;
- enabled state;
- safety notes.

### Wrapper Strategy

Follow the near-term path from [docs/10_execution_spec_and_executor_context.md](./10_execution_spec_and_executor_context.md):

- let the wrapper own MCP injection first;
- generate a run-local MCP config from the selected project/runtime choice;
- pass that config to the external agent CLI;
- keep safety rules unchanged.

### Card Integration

Store selected MCP server ids on the card execution config so the wrapper can inject them at launch time.

### Acceptance

- Manager can inspect MCP entries without loading everything into chat;
- selected MCP servers are available to the wrapper at launch;
- missing MCP support still falls back to the dependency-report flow.

## Suggested File Surface

- `manager-agent/src/server.js`
- `backend/app/services/manager_planner.py`
- `backend/app/services/worker_service.py`
- `backend/app/services/manifest_service.py`
- `backend/app/services/report_service.py`
- `backend/app/api/report.py`
- `backend/app/api/app_settings.py`
- `frontend/components/layout/SideNav.tsx`
- `frontend/components/layout/ProjectWorkspace.tsx`
- `frontend/components/report/ReportBuilder.tsx`
- `frontend/lib/stores/workspace-ui-store.ts`

## Definition Of Done

- Manager does not narrate the full DAG by default.
- Runtime preferences persist per project and survive refresh.
- Exported reports have visible open/download actions.
- `Script Library` is in place before `MCP Library`.
- Skill and MCP registries are readable through summary-only Manager tools.
- Wrapper packaging receives selected scripts, skills, and MCP config in one place.
