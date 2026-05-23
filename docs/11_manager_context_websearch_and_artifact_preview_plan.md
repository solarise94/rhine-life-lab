# Manager Context, Web Search, and Artifact Preview Execution Plan

## Goal

This plan covers five near-term product changes:

- add Manager context compaction using pi-agent-core compaction;
- expose compaction state in the Manager chat timeline and support `/compact`;
- add Manager web search through Tavily search and extract skills;
- fix card canvas selection behavior and remove the default card detail jump;
- add an Artifact Preview Router so card outputs can be previewed instead of only downloaded.

The implementation should preserve current Manager/card data flows. Manager remains the control plane for blueprint edits, card execution permissions, and result interpretation. Card agents should not ask users for interactive permissions.

## Current State

Manager sidecar:

- `manager-agent/src/server.js` creates a pi `Agent`.
- The current context transform is `transformContext: async (messages) => messages.slice(-30)`.
- Token usage from model responses is already forwarded to the frontend and used by the context ring.
- Tool timeline events are already streamed and persisted.

Frontend workspace:

- Main task layout is `frontend/components/layout/ProjectWorkspace.tsx`.
- Card deck is `frontend/components/cards/CardStream.tsx`.
- Current selected-card centering uses `scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" })`.
- `CardDetailPanel` is still rendered in the Advanced view and should not be part of the default task flow.
- `ResultPreviewPanel` already supports text, markdown, table, image, and binary previews when given an `AssetDetail`.

Backend result assets:

- Result asset metadata and preview content are already exposed through the existing result asset APIs.
- The missing piece is mostly routing and presentation from cards/runs to a preview surface.

## Implementation Order

Implement in this order:

1. Workspace UI blockers.
2. Artifact Preview Router.
3. Manager web search.
4. Context compaction.

Reasoning:

- Card selection and result preview are user-facing blockers with limited backend risk.
- Web search is additive and can be isolated behind environment configuration.
- Compaction affects session correctness, timeline persistence, and model context behavior, so it should land after the current UI/data flow is stable.

## Phase 1: Card Canvas Selection and Detail Removal

### Problems

- Selecting a card can leave the card partially outside the visible canvas.
- Horizontal movement can conflict with browser back gestures on trackpads.
- Clicking cards can still drive the user into a detail panel path instead of keeping focus on the card deck.

### Changes

In `CardStream.tsx`:

- Replace direct `scrollIntoView` with explicit center-scroll logic.
- Center the selected card inside the nearest scroll container.
- Prefer the row container first; if the row cannot scroll enough, adjust the canvas container.
- Use `requestAnimationFrame` before measuring after expansion so the selected card width is accurate.

In CSS:

- Add `overscroll-behavior-x: contain` to `.specialist-canvas` and `.workflow-row-cards`.
- Keep horizontal scrolling available inside the canvas, but prevent overscroll from becoming browser history navigation.
- Ensure rows with fewer cards remain visually centered.

In `ProjectWorkspace.tsx`:

- Keep `CardDetailPanel` out of the default task route.
- If detail information is still needed, expose it as a card-local page, modal, drawer, or Advanced-only technical view.
- Do not auto-scroll the main page down to detail when a card is selected.

### Acceptance

- Clicking a card expands it and animates the canvas so the card is centered.
- The user can select cards near the left or right edge without losing access to the expanded content.
- Trackpad horizontal movement inside the canvas does not trigger browser back navigation.
- Task view does not jump to `CardDetailPanel`.

## Phase 2: Artifact Preview Router

### Goal

Cards should expose outputs as previewable artifacts. Users should be able to open images, tables, markdown/text, reports, and binary files in a small preview surface without leaving the task flow.

### Router Contract

Introduce a frontend routing layer, not necessarily a backend route, named conceptually `ArtifactPreviewRouter`.

Input:

```ts
type ArtifactPreviewRequest = {
  projectId: string;
  assetId?: string;
  runId?: string;
  cardId?: string;
  source: "card" | "run" | "results" | "files" | "manager";
};
```

Output:

```ts
type ArtifactPreviewState = {
  open: boolean;
  loading: boolean;
  error?: string;
  detail?: AssetDetail;
  source?: ArtifactPreviewRequest;
};
```

### Frontend Changes

- Add preview state to the workspace UI store or `ProjectWorkspace`.
- Add `openArtifactPreview(request)` and `closeArtifactPreview()`.
- Reuse `getResultAsset(projectId, assetId)` and `ResultPreviewPanel`.
- Render preview as a right drawer or floating panel over the card canvas.
- From card output rows, file bag entries, run outputs, and result grids, call the router instead of only showing download links.

### Preview Behavior

- Images: show inline image with open/download actions.
- Tables: show first rows with horizontal table scroll.
- Markdown/text: show readable preformatted or rendered markdown preview.
- Reports: treat markdown/html/pdf-like outputs as preview-first, download-second.
- Binary/large unknown files: show metadata plus download/open raw action.

### Manager Integration

Add action buttons in preview:

- `Send to Manager`: attaches the asset reference to Manager chat context.
- `Explain this result`: pre-fills a Manager prompt with the asset mention.
- `Add to Report`: optional follow-up once report composition is stable.

### Acceptance

- A completed card with output assets can open a preview drawer from the card itself.
- Preview supports current `ResultPreviewPanel` kinds without regressions.
- Download remains available but is no longer the only way to inspect output.
- Preview state survives local card selection changes until the user closes it.

## Phase 3: Manager Web Search

### Goal

Manager can search and extract web content when current external information is needed, while preserving explicit tool visibility in the chat timeline.

### Configuration

Add environment/config support:

```bash
TAVILY_API_KEY=...
MANAGER_WEBSEARCH_ENABLED=true
```

Rules:

- If `MANAGER_WEBSEARCH_ENABLED` is false or no API key is available, do not register web tools.
- Never log API keys.
- Surface a concise unavailable-tool result if the model tries to use web search when disabled.

### Manager Tools

Add two Manager sidecar tools:

- `web_search`: Tavily search wrapper.
- `web_extract`: Tavily extract wrapper.

Recommended tool schemas:

```ts
web_search({
  query: string,
  search_depth?: "basic" | "advanced",
  max_results?: number,
  include_domains?: string[],
  exclude_domains?: string[]
})
```

```ts
web_extract({
  urls: string[],
  extract_depth?: "basic" | "advanced",
  format?: "markdown" | "text"
})
```

### Prompt Policy

Manager should use web search when:

- the user explicitly asks for current/latest information;
- external documentation or recent package/API behavior matters;
- a card requires up-to-date database or method guidance;
- Manager needs to verify a claim before modifying the blueprint.

Manager should not use web search when:

- current project context is enough;
- the task is only editing cards based on known user intent;
- the answer would expose user/project secrets to third-party services.

### Timeline Labels

Add tool labels:

- `正在搜索网页` / `已搜索网页`
- `正在读取网页` / `已读取网页`

The UI should render these as the same left-label trailing-line style as current tool calls.

### Acceptance

- With API key configured, Manager can search and cite/summarize web results.
- With API key missing, the service starts normally and no secret is logged.
- Tool use appears chronologically in Manager chat.
- Web results are not silently mixed into answers without a visible tool event.

## Phase 4: Context Compaction

### Goal

Replace the hard `messages.slice(-30)` behavior with model-aware compaction. The user should see compaction as a first-class timeline event, similar to thinking, and be able to trigger it manually through `/compact`.

### pi-agent-core Hooks

`@earendil-works/pi-agent-core` exports:

- `compact`
- `shouldCompact`
- `estimateContextTokens`
- `calculateContextTokens`
- `DEFAULT_COMPACTION_SETTINGS`

These should be used in `manager-agent/src/server.js` instead of manually slicing recent messages.

### Context Window

DeepSeek should be treated as a large-context provider. Use model configuration rather than hard-coded UI percentages.

Recommended config:

```bash
MANAGER_CONTEXT_WINDOW_TOKENS=1000000
MANAGER_COMPACTION_ENABLED=true
MANAGER_COMPACTION_KEEP_RECENT_TOKENS=120000
MANAGER_COMPACTION_RESERVE_TOKENS=16000
```

Notes:

- Do not display percentage in the UI.
- Use reported model usage when available.
- Fall back to conservative estimation only when provider usage is missing.
- The context ring can remain visual, but compact state should be event-based and textual.

### Automatic Compaction

Replace `messages.slice(-30)` with:

1. Estimate current context tokens.
2. If `shouldCompact(...)` is false, pass messages through unchanged.
3. If true, emit `compact_start`.
4. Run pi-agent-core `compact(...)`.
5. Persist the summary as a synthetic context entry or session metadata.
6. Emit `compact_end` with duration and token counts.
7. Use compacted context for the next model call.

### Manual `/compact`

Frontend behavior:

- Intercept `/compact` in the Manager composer.
- Do not send it as a normal user message unless compaction fails and needs a visible error.
- Call a sidecar/backend endpoint that runs compaction for the active session.
- Add a timeline item:

```text
正在压缩上下文 0 分 12 秒 ↓ ——————————
```

After completion:

```text
已压缩上下文 0 分 18 秒 < ——————————
```

The compact summary should be collapsed by default but expandable.

### SSE Events

Add stream events:

```json
{ "type": "compact_start", "id": "compact_...", "turn_index": 12 }
{ "type": "compact_delta", "id": "compact_...", "content": "..." }
{
  "type": "compact_end",
  "id": "compact_...",
  "duration_ms": 18000,
  "tokens_before": 820000,
  "tokens_after": 180000
}
{ "type": "compact_error", "id": "compact_...", "message": "..." }
```

`compact_delta` is optional. If pi-agent-core only returns a final summary, the UI should show elapsed time while running and fill the collapsed content at the end.

### Session Persistence

Persist:

- compact timeline item;
- compact summary text;
- first retained message id or index;
- tokens before/after when available;
- model/provider used for compaction;
- timestamp and duration.

The next Manager turn must include compact summary plus retained recent messages. Refreshing the browser must not lose compaction state.

### Acceptance

- Long sessions no longer depend on `slice(-30)`.
- `/compact` creates a visible compact timeline item.
- Automatic compaction creates the same type of timeline item.
- Refreshing the page preserves compact history and future turns use the compacted context.
- If compaction fails, the Manager turn continues only when safe; otherwise show a small error toast/timeline error without corrupting session state.

## Cross-Cutting Requirements

### Timeline Ordering

Thinking, tool use, compaction, and text deltas must keep chronological order. Use the existing turn index approach:

- every streamed item should carry a stable turn/index;
- frontend should append by stream order and reconcile by id;
- heartbeat updates must update existing running items instead of creating duplicates.

### Error Display

Do not reintroduce persistent top banners. Use:

- small toast for transient UI/system errors;
- card/run-local error display for execution failures;
- Manager timeline error item when the error is part of a Manager operation.

### Security

- Tavily API key remains in `.env` or deployment secrets only.
- Web search tools must not send local file content or secrets to Tavily unless explicitly requested by the user and safe.
- Artifact preview must respect existing backend whitelist and asset access controls.
- Card agents still cannot ask the user for runtime permissions in prompts.

## Verification Plan

Backend:

```bash
PYTHONPATH=backend .venv/backend/bin/python -m unittest discover -s backend/tests
```

Frontend:

```bash
cd frontend
npm run build
```

Manager sidecar:

```bash
node --check manager-agent/src/server.js
```

Manual smoke:

- Select cards at both canvas edges and confirm centering.
- Open a completed card output as image, table, markdown/text, and binary preview.
- Send a previewed asset to Manager.
- Run Manager with Tavily disabled and confirm startup works.
- Run Manager with Tavily enabled and ask for a current external source.
- Trigger `/compact` and verify timeline, persistence, and next-turn context behavior.

## Rollout

Recommended commits:

1. `Fix card canvas selection flow`
2. `Add artifact preview routing`
3. `Add manager web search tools`
4. `Add manager context compaction`

Deploy after each commit if the user is actively testing the UI. Compaction should be deployed only after a successful manual smoke test because it changes core Manager session behavior.
