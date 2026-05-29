# Auto / Dependency / Refresh Issue Notes

Status: investigation and remediation plan.

Date: 2026-05-29

This document records three observed UX/runtime issues that should be understood before choosing fixes. The goal is to avoid patching symptoms before the state ownership and refresh paths are clear.

## 1. Planned cards showing ATTENTION

### Symptom

A newly planned OAA-style project can appear to show `ATTENTION` on planned cards even before those cards have run.

### Current live check

The current `oaa-2` backend state did not reproduce this at the API layer:

- `/api/projects/oaa-2/work-order` returned `dependency_attention_count: 0` for planned work items.
- Planned cards still showed normal dependency blockers such as `upstream_cards_not_accepted` and `planned_input_asset_ids`, but not dependency attention.

This means the latest observed UI state may have been caused by stale frontend/session state, a transient graph state during repair, or an older graph snapshot.

### Likely trigger when it does happen

The dependency attention service does not treat `planned` status as a special error by itself. It reports issues when card inputs point to missing, invalid, or outdated assets.

The known risky sequence is:

1. A card is planned with output placeholder ids, for example `deg01_norm_counts_rds`.
2. An upstream run is accepted and the output slot is materialized to a real asset id, for example `asset_run_b15a3061c82d_norm_counts_1`.
3. Downstream planned cards still have `inputs[].asset_id = deg01_norm_counts_rds`.
4. `DependencyAttentionService` no longer sees `deg01_norm_counts_rds` as a valid planned output or materialized asset and may emit `input_asset_missing` or `input_asset_outdated`.
5. `FlowService.get_work_order()` surfaces those issues in the work item list.

### Open design question

There are two possible product directions:

- Keep dependency attention active for planned cards, but fix the underlying input rebinding/materialization edge so fresh plans do not become stale.
- Suppress dependency attention badges for `planned` cards in work-order/card-list views and rely on `block_reasons` until a card is running/reviewing/accepted.

Do not implement either path until the desired product semantics are confirmed. Suppressing planned-card attention is simpler, but may hide genuinely broken planned inputs.

## 2. Auto homepage stream jitter

### Symptom

Auto mode manager output can look choppy or like it is "twitching" on the homepage. It does not feel like the manual chat stream.

### Current architecture

Manual chat:

```text
frontend fetch /chat-stream
-> direct SSE events
-> local applyStreamEvent()
```

Auto wake:

```text
ManagerWakeProcessor
-> manager_service.stream_chat()
-> parse internal SSE payloads
-> publish selected stream_event payloads to ChatSessionService subscribers
-> periodically persist full message snapshots with message_upsert
-> frontend EventSource receives both stream_event and message_upsert
-> frontend also periodically refetches chat session while auto is enabled
```

### Likely causes

1. Auto uses a background session-replay path, not the same direct browser stream as manual chat.
2. `ManagerWakeProcessor` throttles stream publication and persistence:
   - stream events are throttled for delta-like events;
   - full message snapshots are persisted less often.
3. The frontend applies incremental `stream_event` updates and also merges full `message_upsert` snapshots into the same message.
4. During auto mode, chat session polling can fetch a persisted snapshot that is older than the local stream state.
5. `mergeChatMessagesById()` can replace the local timeline with the incoming persisted timeline.

This can create a visible forward/backward effect: local stream advances, then an older persisted snapshot or refetch pulls the message back, then the next stream event pushes it forward again.

### Additional structural cause

Auto is also segmented by wake event. Each wake creates separate messages such as:

```text
wake_notice_<wake_id>
wake_response_<wake_id>
```

So auto mode is "streaming per wake", not "one continuous stream for the whole auto run". That message segmentation can make long auto runs look discontinuous even when each individual wake is streamed correctly.

### Initial direction to evaluate

Prefer a single frontend source of truth while an auto wake message is actively streaming:

- either consume only `stream_event` for active wake messages and ignore older `message_upsert` snapshots for the same message;
- or stop publishing incremental stream events and rely only on persisted snapshots, accepting lower smoothness;
- or introduce a monotonic stream revision / sequence number so older snapshots cannot overwrite newer local stream state.

Manual chat and auto wake should eventually share one reducer/state machine for stream event application.

## 3. Card status refresh lag after Manager starts a run

### Symptom

Manager has already started a card run and can see that the run exists, but the UI still shows the card as planned/old status until a manual refresh or later update. This is confusing because Manager appears ahead of the visible workspace.

### Likely causes

1. There is no unified project-state push channel for card/run mutations. The workspace mostly updates through pull-based React Query refetches, sometimes triggered indirectly by chat/session events.
2. In direct manual stream handling, `tool_end` only triggers an extra `schedulePartialRefresh()` for:

```text
create_card
update_card
delete_card
configure_card_execution
```

That direct-stream tool-name trigger does not include run-control tools that mutate card/run state:

```text
start_card_run
rerun_card
review_card_run
cleanup_run_history
stop_card_run
```

3. Auto owner sessions also have a broader chat-session SSE listener: every non-heartbeat session event calls `schedulePartialRefresh()`. That means the issue is not simply "no frontend trigger exists". The issue is that chat SSE is only an indirect proxy for project state.

4. `card.status` itself is not a worker-thread race. `WorkerService._start_run_with_execution_guard()` sets `card.status = "running"` and saves cards before the executor thread is started. If the frontend refetches after the `start_card_run` tool result, the card should already be `running`.

5. There is still a smaller run-status timing issue. The run is created as `queued` in the start path, while later transitions such as `launching` and `running` happen in the executor thread. Those worker-thread status transitions do not produce chat SSE events, so a chat-triggered refetch can happen too early and then receive no follow-up notification.

```text
start_card_run tool returns run_id
-> frontend refetch happens quickly
-> card.status is already running
-> run.status may still be queued before the worker thread persists launching/running
-> no guaranteed second refresh happens immediately after the worker status transition
```

6. Manager sees the tool response synchronously, so Manager can truthfully say "run started" before the workspace card/run panels have caught up.

### Initial direction to evaluate

There are several possible fixes with different tradeoffs:

- Expand direct stream tool refresh triggers so run-control tool events also refetch project/work-order/run state.
- Add a short delayed second refresh after `start_card_run` / `rerun_card` to cover run-status transitions from `queued` to `launching` / `running`.
- Publish a lightweight project-state/session event when `WorkerService._set_run_status()` changes a card/run status.
- Centralize card/run mutation events instead of relying on chat stream tool names to guess which queries should refresh.

The last option is the cleanest long-term path, but the first two may be enough for a near-term UX fix.

## Current conclusion

These issues share one theme: project state, chat stream state, and auto state are currently synchronized through several partially overlapping paths.

Before implementation, choose the state owner for each surface:

- dependency attention display: keep it derived from snapshot, but decide whether `planned` cards should surface it in normal work lists;
- auto chat output: avoid mixing local stream deltas with older persisted snapshots for the active wake message;
- card/run status: prefer an explicit project-state event or a deterministic refresh trigger for run-control tools.

## Proposed Overall Fix Direction

Treat the three issues as one state synchronization problem, not as three unrelated UI defects.

The key product rule: auto mode should reuse the normal chat streaming path as much as possible. Auto can be triggered by a background wake event, but its visible output should still be the same `ChatStreamEvent` sequence consumed by the same frontend reducer as manual chat.

Desired shape:

```text
manual chat:
  user request
  -> manager stream
  -> ChatStreamEvent
  -> frontend stream reducer

auto wake:
  wake event
  -> manager stream
  -> ChatStreamEvent
  -> frontend stream reducer
```

The trigger source is different. The visible stream model should not be different.

## Background Task Attachment Model

The card executor must be treated as real background work. Manager should not foreground-poll a running card in the same turn after it starts the run.

The useful external reference pattern is:

- A long-running action returns a durable handle, not the final result.
- The visible UI attaches to a stream or session, rather than making the model poll.
- The agent resumes from a wake/session event when the background work reaches a terminal or blocking state.

Local CLI checks line up with that pattern:

- `pi` exposes `--session-dir`, `--session`, `--continue`, `--resume`, plus `json` / `rpc` output modes. The core model is durable session attachment and resumability.
- `kimi` exposes `--session`, `--continue`, `--output-format stream-json`, and session export. It similarly treats the conversation as a resumable session with stream output.
- `claude` exposes `--session-id`, `--resume`, `--output-format stream-json`, partial message streaming flags, and `claude agents` for background agents. Its background-agent surface is explicitly session/handle based.

The desired Blueprint RE mapping is:

```text
Manager tool call:
  start_card_run / rerun_card
  -> returns run_id + async_boundary + wait_for_wake
  -> Manager turn ends

Worker:
  owns run/card lifecycle
  -> emits project-state events while status changes
  -> enqueues wake events only for terminal/blocking states

Frontend:
  displays active_run_id as attached background work
  -> subscribes to project-state events for card/run refresh
  -> subscribes to chat stream/session events for Manager text only

ManagerWakeProcessor:
  claims terminal/blocking wake
  -> runs one Manager turn
  -> may start the next background run
  -> again ends at the next async boundary
```

Current implementation already has the right primitives:

- `WorkerService.start_run()` persists the run/card state and starts a background thread.
- `start_card_run` / `rerun_card` return `background`, `async_boundary`, `do_not_poll`, and `wait_for_wake`.
- Manager auto state stores `active_run_id`.
- Worker terminal states enqueue wake events.
- Runtime `needs_manager` events are not supposed to wake Manager while the subprocess is still running.

The missing pieces are enforcement and event ownership:

1. `do_not_poll` is currently mostly a prompt/tool-result contract. It should become a tool-protocol boundary.
2. Chat/session SSE is currently doing some project refresh work by side effect. It should not be the authoritative card/run state channel.
3. Auto and manual chat streams should share one visible reducer. Auto should not have a separate UI semantics.

### P0A: Enforce Async Boundary After Starting Background Work

Target behavior:

- If `start_card_run` or `rerun_card` succeeds with a real background run, the current Manager turn must stop after reporting the `run_id`.
- The same turn must not call graph-reading or cleanup tools just to wait for the run. Examples include `inspect_project_summary`, `inspect_dependency_attention`, `find_cards`, `get_card_detail`, `find_assets`, and `get_asset_detail`.
- A later wake turn is allowed to inspect state because it is a new turn with a new reason.

Boundary should apply only when the run actually moved into background execution:

- apply when response has `ok: true`, `run_id`, `async_boundary: true`, `wait_for_wake: true`, and no unresolved `pending_approvals` / `rejected_approvals`;
- do not apply when the run cannot start, needs approval, or returns validation blockers. In those cases Manager should fix/report the blocker.

Implementation options:

1. Preferred: if the agent runtime supports step termination, `manager-agent` should end the agent turn immediately after a successful async-boundary tool result is emitted.
2. Fallback: keep a per-`runManagerChat()` `asyncBoundary` marker. Every subsequent tool execute in the same turn checks it and rejects tool calls outside a small allowlist with a terminal `async_boundary_active` result that tells Manager to stop and wait for wake.
3. Also emit a concise `tool_report` such as: "Background run started: `<run_id>`. Waiting for wake event." This is enough user-visible output for that turn.
4. Do not expose `async_boundary_active` as a retryable backend failure. It should have no retry hint and should be phrased as final turn control. Otherwise Manager may interpret it as a transient tool failure and retry in a tight loop.

Suggested guard state:

```js
const asyncBoundary = {
  active: false,
  toolName: null,
  runId: null,
  jobId: null,
};
```

`runId` is for card execution boundaries. `jobId` is reserved for background runtime dependency installation if that job type is later given the same hard turn-boundary behavior; a run-only v1 may omit it.

Set it inside `callLoggedTool()` after parsing a successful tool payload. Check it at the start of every tool executor.

Use an allowlist, not a polling-tool blacklist. Blacklists are fragile because every future read tool would need to be added manually. After the async boundary is active, the default is "no more tool calls in this turn".

Allowed after async boundary:

```text
stop_card_run   # only when the current user request explicitly asks to interrupt/cancel
```

`stop_card_run` should only remain available when the current user request explicitly asked to interrupt or cancel the run. In ordinary auto progression, even `stop_card_run` should not be called.

Blocked by default after async boundary:

```text
inspect_project_summary
inspect_dependency_attention
find_cards
find_assets
get_card_detail
get_asset_detail
get_project_context
list_data_assets
cleanup_run_history
all write/mutation tools except explicit stop_card_run
```

Scope:

- The boundary applies only inside the same `runManagerChat()` turn that started the background run.
- A later wake turn may inspect state because the background task has reached a terminal or blocking point.
- A later user-initiated manual turn may inspect or cancel state because it is a new user instruction, not foreground polling by the launch turn.
- If auto mode disables user input while a run is active, the UI interrupt action should explicitly stop auto mode first; whether it also cancels the run should stay a separate user action.

This makes "no foreground polling" a system property instead of relying only on prompt obedience.

### P0B: Add Project-State Event Stream For Card / Run Changes

Chat events should not be the primary workspace refresh mechanism. Add a project-level event stream for graph/runtime changes.

Project-state events are UI/cache invalidation events, not Manager wake events. They must not by themselves cause Manager to resume, plan, or mutate the graph. Manager re-entry remains owned by the existing wake queue.

Suggested endpoint:

```text
GET /api/projects/{project_id}/events
```

Suggested event payload:

```json
{
  "type": "project_state_changed",
  "project_id": "oaa-2",
  "graph_revision": 128,
  "seq": 942,
  "reason": "run_status_changed",
  "reasons": ["run_status_changed"],
  "card_id": "deg02-differential-expression",
  "run_id": "run_xxx",
  "status": "running",
  "created_at": "2026-05-29T12:00:00Z"
}
```

Revision / sequence source:

- Persist the monotonic counter in project graph metadata, for example `graph.metadata["project_event_revision"]`.
- Increment it under the project lock whenever an event-producing mutation is saved.
- Use the same persisted value for `graph_revision` and `seq` in v1 unless there is a clear need to split them later.
- Do not use an in-memory counter; it must survive backend restart and cannot reset while clients are connected.

Emit from authoritative mutation points:

- `WorkerService.start_run()` after saving the new run and setting the card to `running`;
- `WorkerService._set_run_status()`;
- `WorkerService._set_run_attention()` / structured executor-event handling when `needs_manager_attention` changes;
- run review/finalization paths;
- `cancel_run()`, cleanup/history mutation, and reset run state;
- card create/update/delete/configure tools;
- runtime dependency job state changes;
- manager auto state changes when `active_run_id`, `active_job_id`, or mode changes.

Threading rule:

- Worker callbacks can run from executor daemon threads. The project event service must be thread-safe.
- Use `queue.Queue` plus explicit subscriber locks, or a dedicated dispatcher thread. Do not use `asyncio.Queue` directly from worker threads.
- Publish only after the graph/cards are saved. The event tells clients that a committed snapshot is available.
- Prefer collecting event payloads while holding the project lock, then publishing after the lock is released. A slow subscriber or full queue must not block graph persistence.
- On subscriber queue overflow, either drop the event and log it or close that subscriber stream. The client must recover through baseline refetch / sequence-gap handling.

Baseline rule:

- SSE is push-only, so first connection and reconnect must start from a full baseline fetch.
- The frontend should fetch project/work-order state before applying project-state events.
- The SSE endpoint should also send an initial control event with the current `graph_revision` and `requires_refetch: true`.
- If the client sees a gap in `seq`, it must refetch the full project/work-order state and reset its local event cursor.

Coalescing rule:

- Multiple changes inside one saved mutation should publish one aggregate event with `reasons`, not several independent events.
- Rapid transitions such as `queued -> launching -> running` may still emit multiple committed events. The frontend should debounce invalidation for the same `(project_id, card_id, run_id)` for a short window, for example 100-250 ms.
- Coalescing must not hide terminal states (`failed`, `cancelled`, `reviewed`, `needs_review`) because those can trigger wake-driven Manager behavior.
- Coalescing is only for UI/cache events. It must not coalesce or suppress wake-queue entries.

Frontend subscription rules:

- subscribe once per open project page, independent of chat session ownership;
- on card/run events, debounce and invalidate `project`, `workOrder`, selected card detail, active run events, and result summaries as needed;
- on manager-auto events, invalidate `managerAuto`;
- on first connect and reconnect, refetch full project/work-order state once before applying new events;
- prefer invalidation/refetch over direct cache patching for graph-shaped data. Direct cache patching is allowed only for small local state such as `managerAuto` if it is exact.

Because events are emitted after persistence, invalidation/refetch does not reintroduce the old card-status race. It simply pulls the latest committed snapshot. The delayed refresh fallback remains useful only until this project-state stream is stable, and mainly for run-status transitions.

### P0C: Keep Chat SSE For Manager Output Only

The existing chat session SSE is still useful, but its job should be limited:

- stream Manager text/thinking/tool timeline for auto wake turns;
- deliver final `message_upsert` snapshots;
- reconnect and hydrate chat history.

It should not be treated as the source of truth for card/run status. When chat SSE receives a tool event, it may still schedule a best-effort refresh, but the project-state stream should be the primary path.

For active auto wake messages:

- `stream_event` is the live source;
- non-final `message_upsert` must not overwrite the active streamed message;
- final `message_upsert` may settle the message after `done` / `error`;
- events should carry a monotonic `seq` or stream revision so old snapshots cannot move the UI backward.
- after an EventSource reconnect, a newer persisted `message_upsert` may hydrate or replace the partial active message if its revision is greater than the local revision. Otherwise a network blip could freeze a partial message forever.

Auto wake segmentation is intentional:

- manual chat is one user request and one assistant turn;
- auto mode is a sequence of wake turns, each with its own `wake_response_<wake_id>`;
- do not merge multiple wake responses into one synthetic assistant message, because that would change conversation history semantics.

"Same visible stream model" means the same reducer/schema per assistant message, not one continuous message across the whole auto run.

### Detailed Chat Stream Reducer Plan

Current risk:

- Manual chat uses a direct `/chat-stream` response and locally applies every stream event.
- Auto wake handling streams internally, but then mixes:
  - `stream_event` payloads,
  - persisted `message_upsert` snapshots,
  - chat session refetches,
  - project/work-order refetches.
- This lets older persisted snapshots overwrite newer local stream state.

Target behavior:

1. Extract the frontend stream event application logic into one reducer/hook.
2. Use that reducer for both:
   - manual `/chat-stream`;
   - auto `chat-session/events` `stream_event`.
3. During an active auto wake stream, treat `stream_event` as the only live source for that message.
4. Do not let intermediate `message_upsert` snapshots replace the local timeline/content of the same active message while the EventSource connection is continuous and local stream revision is newer.
5. Persist the final message snapshot at `done` / `error` and let that final snapshot settle the message.
6. On reconnect or full session hydration, allow a newer persisted message snapshot to repair a partial local stream.

Suggested event fields:

```json
{
  "type": "stream_event",
  "message_id": "wake_response_<wake_id>",
  "stream_id": "stream_<id>",
  "seq": 42,
  "event": { "type": "text_delta", "delta": "..." },
  "final": false
}
```

Frontend rule:

- apply only events with increasing `seq`;
- ignore non-final `message_upsert` for active streaming messages only when the local stream is newer and the connection has not been reset;
- accept newer `message_upsert` after reconnect as a recovery snapshot;
- accept final `message_upsert` only after `done` / `error`;
- clear the active-stream marker after final settlement.

Backend rule:

- `ManagerWakeProcessor` should not maintain a separate visible message semantics from manual chat.
- It may create the shell message and persist final snapshots, but live UI state should be driven by the same event stream schema.
- Throttling may still be used for backend persistence, but should not change stream semantics or allow stale snapshots to win.

### P1: Fix Card / Run Status Refresh Lag

Current risk:

- Manager tool calls can start or mutate runs synchronously.
- Manager sees the tool response immediately.
- The workspace UI catches up only when React Query refetches happen to run.
- Manual stream refresh currently triggers for only a subset of tools:

```text
create_card
update_card
delete_card
configure_card_execution
```

Run-control tools are missing from that explicit refresh trigger:

```text
start_card_run
rerun_card
review_card_run
stop_card_run
cleanup_run_history
```

Short-term fix:

1. Expand frontend tool refresh triggers to include all run-control tools.
2. After `start_card_run` / `rerun_card`, schedule:
   - immediate project/work-order refresh;
   - one delayed refresh after the worker thread has time to persist run-status transitions such as `launching` / `running`.
3. Refresh affected run events when a `run_id` is present in the tool report/details.

Important distinction:

- immediate refresh after tool end should be enough for `card.status = running`, because that card state is saved before the worker thread starts;
- delayed refresh is for `run.status` and run events, not for card status.

Long-term fix:

Use the project-state event stream described in P0B. Do not maintain a second event list or schema in this section. P1's short-term refresh triggers are compatibility scaffolding that should become less important once P0B is stable.

### P2: Re-evaluate Planned-Card Dependency Attention After Sync Is Clean

Do not first solve planned-card ATTENTION by hiding it everywhere.

After P0/P1, verify whether planned-card attention is still visible from a fresh backend snapshot.

If it remains:

1. Determine whether the issue is a true stale input reference:
   - downstream `inputs[].asset_id` points to a missing planned placeholder;
   - downstream `inputs[].asset_id` points to an older materialized asset while a current valid output exists.
2. If yes, prefer a scoped data fix over display-only hiding:
   - for first-time planned output materialization, rebind downstream planned-placeholder inputs to the real asset id;
   - do not reintroduce broad rerun replacement rebinding.
3. If product semantics say planned cards should not show dependency attention in normal lists:
   - suppress planned-card badges in work-order/card-list views;
   - keep full diagnostics available through explicit inspect tools/debug views.

This keeps dependency attention truthful while avoiding false user-facing noise.

### Acceptance Checks

Use these checks to verify the implementation, not just the code shape:

1. Manual `start_card_run` / `rerun_card`
   - Manager emits the tool report with `run_id`.
   - The same Manager turn does not call graph-reading tools to wait for completion.
   - The UI shows the card as `running` without a browser refresh.
   - A delayed or project-state event refresh updates run detail from `queued` to `launching` / `running`.

2. Auto `start_card_run` / `rerun_card`
   - Auto state records `active_run_id`.
   - The auto button shows background activity from manager-auto/project state, not from inferred chat text.
   - Manager does not resume until a terminal/blocking wake event is queued.

3. Project-state SSE
   - First connection sends or forces a baseline refetch before applying incremental events.
   - Reconnect performs a baseline refetch and tolerates missed events.
   - A sequence gap causes a full project/work-order refetch.
   - Worker-thread status changes do not corrupt subscriber state and do not block project locks.

4. Chat SSE
   - Manual and auto assistant messages use the same stream-event reducer.
   - Continuous active streams ignore stale non-final `message_upsert` snapshots.
   - After reconnect, a newer persisted message snapshot can repair a partial local message.
   - Separate auto wake turns remain separate assistant messages.

5. Dependency attention
   - Planned-card attention is rechecked from a fresh backend snapshot after P0/P1.
   - Any remaining planned-card ATTENTION is classified as either true stale input data or display policy.

### Implementation Priority

Recommended engineering order:

1. P0A: enforce async boundary after successful `start_card_run` / `rerun_card` with an allowlist-style same-turn guard, so Manager cannot foreground-poll the same run in the launch turn.
2. P1 short-term: add run-control refresh fallbacks immediately. This is not the final architecture, but it removes the worst card-status lag while P0B is built.
3. P0C: keep chat SSE for Manager output only and prevent active auto stream messages from being overwritten by stale snapshots while still allowing reconnect hydration.
4. P0B: add thread-safe project-state event stream from authoritative mutation points, with persisted revision, baseline fetch, reconnect recovery, and event coalescing.
5. P2: revisit planned-card attention semantics with clean, current snapshots.

Priority note: P0B is the long-term architecture. P1 appears before it in build order only because it is a small compatibility fix and reduces user-visible lag sooner. Do not stop at P1.

Avoid:

- adding more auto-specific UI state machines;
- making auto messages render from a different schema than manual chat;
- relying on periodic polling as the primary state propagation path;
- hiding planned-card attention before confirming whether the graph data is actually stale.
