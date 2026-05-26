# Manager Auto Mode and Wake Hook Plan

本方案定义 Manager 的 `/auto` 自动模式：用户在一个 session 中显式开启后，card run、reviewer、长执行 tool 和依赖安装 job 可以通过后端 wake hook 唤醒 Manager。Manager 被唤醒后主动解决可处理问题、继续运行 ready cards，然后结束本轮 turn 并休眠等待下一个信号。

本文件是设计文档，不代表当前已全部实现。

## Goals

- 用 `/auto` 显式授权 Manager 自动推进项目。
- card 执行完成、依赖缺失、依赖安装完成等事件可以唤醒 Manager。
- Manager 尽量自动解决 routine blockers，不把每个小决策都交给用户。
- 自动模式只允许一个 session 持有，避免多个 Manager 同时修改蓝图或启动 runs。
- 其他 session 在 auto mode 期间降级为 `/btw` 模式，只允许查询、解释和联网检索，不允许蓝图写入或 run-control。
- 出现不可自动恢复错误时，必须明确提示原因并退出 auto mode。

## Non-Goals

- 不让 LLM 请求长期挂起等待 run/job 完成。
- 不绕过现有 WorkerService、Reviewer、manifest validation 和 manager tools。
- 不让 executor agent 自己安装依赖。
- 不在其他 session 中偷偷执行蓝图修改。
- 不把所有 run events 都喂给 Manager；只处理有推进价值的 wake signals。

## Confirmed Product Decisions

These decisions are fixed for the first implementation:

- `/auto` defaults to continuous mode, not once mode.
- There is no cross-session takeover in the first version. Another session cannot force-take ownership while auto mode is active.
- Auto mode may reset the current planned/failed card slot when a new owner-session directive makes the priority clear, but it should not interrupt a live executor process unless the user explicitly sends `/auto stop`.
- Pre-existing failed cards are not automatically treated as terminal. If Manager reaches an old failed card that was not produced by the current auto loop, it may reset and run it.
- Current-loop failures are judged by Manager: retry only when the failure looks transient, dependency-related, or caused by insufficient instruction/context. If the card appears blocked by data integrity, missing inputs, or irreparable contract failure, stop auto mode.
- Auto mode may adjust input/output bindings when upstream outputs changed and may change runtime bindings when the current runtime is unsuitable.
- Auto mode should not modify skill selection, script assets, analysis parameters, P-value thresholds, biological method choices, or other substantive card design choices. It should run the card as originally designed whenever possible.
- Dependency installation may be based on explicit dependency reports or clear error logs, but only in a selected non-system runtime.
- Manager may switch a card to a more suitable non-system runtime before installing dependencies or rerunning.
- If no ready card remains, auto mode stays enabled and idle.
- Chain limit should be derived from blueprint size rather than a fixed `10`.
- While auto mode is active, UI configuration/run/delete controls become read-only. Users steer auto through owner-session directives instead of manual UI mutation buttons.

## User-Facing Behavior

### `/auto`

用户在 Manager chat 输入：

```text
/auto
```

系统进入 auto mode，并把当前 session 记录为唯一 auto owner session。

Manager 回复类似：

```text
Auto mode 已开启。我会在 card 完成、依赖任务结束或出现可处理阻塞时继续推进，并把每次动作写在这里。
```

### `/auto status`

显示当前 auto 状态：

- 是否开启；
- owner session；
- 当前 active run / dependency job；
- 最近一次 wake event；
- 连续自动步数；
- 最近停止原因。

### `/auto off`

关闭 auto mode，但不主动杀掉已经启动的 run/job。

### `/auto stop`

关闭 auto mode，并停止当前由 auto mode 启动的 active run。对于正在执行的依赖安装 job，第一版可以只标记不再继续后续动作；如果后续支持 job cancellation，再执行真实 cancel。

### `/auto once`

只自动推进一轮：处理当前阻塞或启动下一张 ready card，完成后自动关闭。

### Other Sessions Become `/btw`

当项目已有 session 开启 auto mode 时，其他 session 进入 `/btw` 模式。

`/btw` 模式允许：

- 查询当前 run/card 状态；
- 解释结果、解释错误；
- 查阅资产、报告和日志；
- 联网检索公开资料；
- 回答一般问题。

`/btw` 模式禁止：

- create/update/delete card；
- configure card execution；
- start/stop/rerun/review card run；
- install runtime dependencies；
- save/instantiate card templates；
- 修改项目级 runtime/library 设置。

如果用户在非 owner session 中请求写操作，Manager 应回复：

```text
当前项目正在由另一个 session 的 auto mode 推进。这里处于 /btw 查询模式，不能修改蓝图或启动运行。请先在 auto session 关闭 /auto，或切换到该 session 操作。
```

## Frontend State

auto mode 开启后，输入框发送按钮切换样式：

- off：普通 `Send` 图标；
- on：使用 `Sparkles`、`Bot` 或 `Workflow` 图标；
- 按钮外圈显示细环；
- tooltip: `Auto mode 已开启`；
- 输入框附近显示小型 `AUTO` chip，可点击打开 `status / off / stop / once` 菜单。

auto mode 因错误停止后：

- 发送按钮恢复普通样式；
- `AUTO` chip 显示短暂 stopped 状态；
- 对话中必须追加明确消息，不能只用 toast。

停止消息格式：

```text
因 XXX 原因任务停止，已退出 auto 模式。

当前 card：...
下一步：...
```

## Backend Auto State

auto state 应保存在项目级后端状态中，不能只存在 localStorage。

建议存入 `graph.metadata.manager_auto`：

```json
{
  "enabled": true,
  "mode": "continuous",
  "owner_session_id": "session_xxx",
  "started_at": "2026-05-25T13:00:00Z",
  "last_wake_id": "wake_xxx",
  "chain_count": 3,
  "max_chain_count": 36,
  "chain_limit_basis": {
    "executable_card_count": 12,
    "formula": "max(10, min(80, executable_card_count * 3))"
  },
  "active_run_id": "run_xxx",
  "active_job_id": "depjob_xxx",
  "stopped_at": null,
  "stop_reason": null,
  "stop_message": null
}
```

Only one session can own auto mode per project.

When `/auto` is enabled in another session:

- if no auto owner exists, that session becomes owner;
- if the same session is already owner, return current status;
- if another session is owner, reject with conflict and tell frontend to enter `/btw` mode.
- do not support `/auto takeover` in the first implementation.

### Dynamic Chain Limit

The default chain limit should be computed from the current blueprint size instead of being a fixed small number.

Recommended first formula:

```text
max_chain_count = max(10, min(80, executable_card_count * 3))
```

Rationale:

- `executable_card_count` counts active non-archive cards that can produce outputs.
- `* 3` gives each card room for run, dependency repair/config adjustment, and rerun.
- lower bound `10` keeps small projects useful.
- upper bound `80` prevents runaway loops on large projects.

Per-card retry limit is separate:

```text
max_auto_attempts_per_card = 2
```

If a card fails twice during the same auto loop, auto mode exits unless the second failure is a different upstream dependency issue that Manager can safely handle.

## Session Identity Propagation

Auto mode cannot be enforced correctly unless every Manager turn and every Manager tool call carries the originating chat session id.

Required changes:

- `ChatRequest` gains `session_id: str | None`.
- frontend `api.streamChat`, `api.sendChat`, `api.createChatJob`, and compact/chat helpers pass the current `sessionId`.
- `ManagerService._chat_via_pi`, `stream_chat`, and `compact_chat_session` forward `session_id` to the manager sidecar payload.
- manager-agent includes `session_id`, `auto_mode`, and `btw_mode` in the user envelope.
- manager-agent tool calls send `session_id` to backend internal manager-tool endpoints, either in JSON body or an internal header such as `X-Blueprint-Session-Id`.
- backend internal manager-tool endpoints validate that mutating calls come from the auto owner session when auto mode is active.

The owner session is the only session allowed to mutate blueprint/execution state during auto mode. Without this propagation, `/btw` would only be prompt-level guidance and could be bypassed by a tool call.

Suggested request shape:

```json
{
  "message": "start the next ready card",
  "session_id": "session_xxx",
  "context": {
    "script_preference": "auto"
  },
  "auto_mode": {
    "enabled": true,
    "owner_session_id": "session_xxx",
    "btw_mode": false
  }
}
```

## Session Persistence and Append Safety

Current chat session persistence replaces the entire `messages` array. That is unsafe once backend wake processing can append messages to the owner session while the frontend still has a stale local copy.

Required fix:

- add an append-only API for backend and frontend auto messages;
- or add optimistic concurrency with a revision/updated_at precondition and server-side merge.

Preferred first implementation:

```text
POST /api/projects/{project_id}/chat-sessions/{session_id}/messages
```

Request:

```json
{
  "messages": [
    {
      "id": "auto_wake_xxx",
      "role": "manager",
      "content": "后台事件：Reviewer 已接受免疫浸润分析。",
      "state": "done",
      "timeline": []
    }
  ],
  "dedupe_ids": ["auto_wake_xxx"]
}
```

Rules:

- append must be idempotent by message id;
- appending updates session `updated_at`;
- appending must preserve existing messages;
- frontend full-session save should either stop while auto owner session is receiving backend messages, or use a revision-aware merge;
- if frontend sends stale full messages after backend append, backend must not drop the appended messages.

Recommended model addition:

```json
{
  "session_id": "session_xxx",
  "revision": 42,
  "updated_at": "..."
}
```

`save_session` can accept `base_revision`. If the current revision differs, backend merges by message id instead of replacing blindly.

## Wake Event Queue

Add a persistent queue:

```text
workspace/<project_id>/manager_wake_events.jsonl
```

Event shape:

```json
{
  "wake_id": "wake_xxx",
  "project_id": "oaa",
  "kind": "card_run_reviewed",
  "source_type": "run",
  "source_id": "run_xxx",
  "card_id": "card_xxx",
  "run_id": "run_xxx",
  "job_id": null,
  "severity": "info",
  "message": "Reviewer accepted the run.",
  "payload_summary": {},
  "idempotency_key": "run:run_xxx:reviewed",
  "status": "queued",
  "created_at": "2026-05-25T13:00:00Z",
  "processed_at": null,
  "error": null
}
```

Required service methods:

- `enqueue(event)`;
- `claim_next(project_id)`;
- `mark_running(wake_id)`;
- `mark_done(wake_id)`;
- `mark_failed(wake_id, error)`;
- `mark_skipped(wake_id, reason)`;
- `list_recent(project_id)`.

Idempotency is mandatory. Replaying a terminal run event or restarting the backend must not wake Manager twice for the same lifecycle transition.

Recommended idempotency keys:

- `run:{run_id}:reviewed`;
- `run:{run_id}:failed`;
- `run:{run_id}:dependency_missing`;
- `depjob:{job_id}:succeeded`;
- `depjob:{job_id}:failed`;
- `tooljob:{job_id}:{status}`.

## Wake Producers

### Card Run Lifecycle

`WorkerService._execute_run` should enqueue wake events at terminal or blocking points:

- reviewer accepted run: `card_run_reviewed`;
- dependency missing: `runtime_dependency_missing`;
- validation failed: `executor_validation_failed`;
- manifest failed: `manifest_validation_failed`;
- run timeout: `card_run_failed`;
- user cancelled: usually no auto wake, unless owner session needs a message.

Do not wake Manager for ordinary `executor_progress`.

### Executor Reports

Existing `BP_EVENT issue_report` with `needs_manager=true` should enqueue `card_needs_manager` if it is blocking.

First implementation should wake after the run has failed/stopped, not while the executor process is still running. A later protocol can add true pause/resume if needed.

### Runtime Dependency Jobs

`RuntimeDependencyJobService` should enqueue when a job finishes:

- `runtime_dependency_install_succeeded`;
- `runtime_dependency_install_failed`.

The `install_runtime_dependencies` tool should accept optional source fields:

```json
{
  "card_id": "card_xxx",
  "run_id": "run_xxx",
  "wake_id": "wake_xxx",
  "reason": "Missing R packages reported by executor."
}
```

Without source fields, Manager may not reliably know which card to rerun when the job completes.

Dependency job payload should persist those source fields:

```json
{
  "ecosystem": "R",
  "runtime": "omicverse",
  "packages": ["GSVA", "estimate"],
  "manager": "bioconductor",
  "source": {
    "card_id": "rna_immune",
    "run_id": "run_xxx",
    "wake_id": "wake_xxx",
    "reason": "runtime_dependency_missing"
  }
}
```

When the job finishes, the wake event should include the same source object. If source is missing, the processor may still notify the owner session but should not automatically rerun a card.

### Long Tool Jobs

Any future long-running manager tool should use the same pattern:

1. tool starts background job and returns `job_id`;
2. job service persists status;
3. completion enqueues wake event;
4. Manager is woken for one new turn.

Do not keep the original LLM turn open.

## Wake Processor

`ManagerWakeProcessor` runs in the backend process and periodically claims queued events.

Processing rules:

1. Load project auto state.
2. If auto is disabled, append a passive notification only when useful, then mark event skipped.
3. If auto is enabled, verify `owner_session_id` exists.
4. Acquire a per-project auto lock.
5. Build a `ChatRequest` with the wake event and auto guidance.
6. Call `ManagerService.chat()`.
7. Append both the wake notice and Manager response to the owner session.
8. Update auto state with `last_wake_id`, `chain_count`, active run/job if returned.
9. Mark wake event done or failed.

The processor should never run two Manager wake turns for the same project at the same time.

### Processor Concurrency and Recovery

The first deployment is single-user/single-backend-process, so a file-backed queue plus in-process locks is acceptable. The design should still make recovery explicit:

- claim uses a project-level lock to avoid two wake turns for the same project;
- a running wake event records `claimed_at` and `processor_id`;
- if `status=running` remains stale beyond a timeout, it can be retried or marked failed;
- every wake turn increments `chain_count` only after a successful manager action or explicit no-op response;
- repeated failure of the same wake event should stop auto mode after a small retry limit.

Suggested timeout fields:

```json
{
  "claimed_at": "...",
  "processor_id": "backend_pid_123",
  "attempts": 1,
  "last_error": null
}
```

### Wake Message Persistence

Before calling Manager, the processor should append a short wake notice to the owner session:

```text
后台事件：免疫浸润分析报告缺少 R packages: GSVA, estimate。
```

After Manager responds, append the Manager response as a separate message with metadata:

```json
{
  "origin": "auto_wake",
  "wake_id": "wake_xxx",
  "auto_mode": true
}
```

This gives the user and future Manager turns a durable audit trail.

## Manager Prompt Guidance

When auto mode is enabled, inject this guidance into the Manager sidecar envelope:

```text
Auto mode is enabled.

You are expected to keep the project moving without asking the user for routine decisions.

When a wake event arrives:
- Inspect only the relevant card/run/job first.
- If a card finished successfully, find the next ready card and start it.
- If a runtime dependency is missing and the selected runtime is non-system, install the explicit missing packages with install_runtime_dependencies.
- If dependency installation succeeds, rerun the blocked card.
- If error logs clearly identify missing packages, you may install those packages in a selected non-system runtime even if the executor did not produce a formal dependency report.
- If the current runtime is unsuitable and another project runtime is more appropriate, switch the card runtime before rerunning.
- If upstream output assets changed, update input/output asset bindings so the card consumes the correct current assets.
- If a run fails due to a clear small configuration issue that fits the allowed auto mutation scope, fix it and rerun once.
- Do not change skills, MCP choices, script assets, analysis parameters, P-value thresholds, biological method choices, or substantive card design. Keep the original card intent and method.
- If you encounter an old failed card that was not produced by the current auto loop, you may reset it and run it.
- If a current-loop card fails, decide whether it is transient/retryable or terminal. Stop auto mode if it has failed twice or appears blocked by data integrity, missing inputs, or irreparable contract failure.
- If no ready work remains, report completion and leave auto mode enabled but idle.
- Do not restate the full DAG. Report only what changed, what is running, or why auto mode stopped.

Do not ask the user for permission for routine actions that are already covered by project tools and selected runtime settings.

Stop auto mode and report the stop reason when:
- Required input data is missing or ambiguous.
- A system-level dependency, credential, license, or external account is needed.
- Runtime dependency installation fails or times out.
- Reviewer fails because of data integrity, fake outputs, wrong script logic, or output contract mismatch that is not safely fixable.
- The same card fails twice in auto mode.
- The auto chain reaches its configured maximum step count.
```

Wake event should be included as structured context:

```json
{
  "auto_mode": true,
  "btw_mode": false,
  "wake_event": {
    "kind": "runtime_dependency_missing",
    "card_id": "rna_immune",
    "run_id": "run_xxx",
    "message": "Missing R packages: GSVA, estimate"
  }
}
```

## `/btw` Prompt Guidance

When a session is not the owner while auto mode is active:

```text
Another session owns auto mode for this project.

This session is in /btw mode:
- You may answer questions, inspect status, explain logs, read result assets, and use web search when appropriate.
- You must not call tools that mutate blueprint/card/project execution state.
- You must not start, stop, rerun, review, configure, or delete cards.
- If the user asks for a mutating action, explain that auto mode is active elsewhere and tell them to switch to the owner session or stop auto mode there.
```

Tool filtering should enforce this in code. Prompt text alone is not enough.

## Tool Filtering

Manager sidecar should receive different tool sets based on mode.

### Auto Owner Session

Allowed:

- compact inspect/find/detail tools;
- card write tools;
- configure card execution;
- run-control tools;
- dependency install/status tools;
- artifact preview/read tools;
- project memory tools;
- library search/detail tools.

Auto owner mutating tools are still constrained by auto mutation scope. Manager may update runtime bindings and input/output bindings, but should not change skills, script assets, MCP choices, analysis parameters, thresholds, or core method design while auto mode is running.

### `/btw` Sessions

Allowed:

- inspect project summary;
- find cards/assets;
- get card/asset detail;
- read result asset;
- web search/extract;
- list project memory;
- list library ids/names.

Blocked:

- create/update/delete card;
- configure card execution;
- start/stop/rerun/review run;
- install runtime dependencies;
- cleanup run history;
- save/instantiate templates;
- write project memory if it changes manager behavior during auto mode.

Backend internal tool endpoints should also reject mutating calls when `btw_mode=true` or when caller session is not the auto owner.

### Backend Enforcement

Prompt/tool filtering in manager-agent is not enough. Backend internal manager tools must enforce session ownership.

Add a shared guard:

```text
assert_manager_mutation_allowed(project_id, session_id, tool_name)
```

Behavior:

- if auto mode is disabled, allow normal tool behavior;
- if auto mode is enabled and `session_id == owner_session_id`, allow;
- if auto mode is enabled and `session_id != owner_session_id`, reject mutating tools with 409;
- read-only tools remain allowed for `/btw` sessions;
- calls without `session_id` are rejected for mutating tools while auto mode is active.

Mutating tools include:

- card writes;
- card execution configuration;
- run-control;
- dependency installation;
- cleanup;
- template save/instantiate;
- project memory writes that affect future Manager behavior;
- runtime/library settings changes.

Read-only tools include:

- inspect/find/detail;
- result asset read;
- web search/extract;
- library list/search/detail;
- dependency job status;
- run event/status reads.

### UI Mutations During Auto Mode

While auto mode is active, regular UI controls that mutate project execution state should be locked into read-only mode, even in the owner session.

Lock:

- configure card execution;
- start/stop/rerun/review buttons;
- delete/archive card controls;
- runtime/library configuration controls;
- script/skill/MCP attachment controls;
- report/export actions only if they mutate project state.

Allow:

- view cards;
- inspect run events/logs;
- preview/download assets;
- read reports;
- send owner-session directives;
- `/btw` status and explanation questions.

The owner session remains the control surface through directives. Direct manual UI mutation would bypass the auto audit trail and make Manager's next wake decision less reliable.

## Auto Stop Rules

Auto mode must exit and notify the owner session when:

- dependency install fails or times out;
- selected runtime is system or missing;
- missing dependency is a system package/tool;
- required input data is missing or ambiguous;
- reviewer reports data integrity failure;
- manifest/output contract fails in a way Manager cannot safely repair;
- same card fails twice under the current auto loop;
- dynamic max chain count is reached;
- manager tool call fails repeatedly;
- user sends `/auto off` or `/auto stop`.

State update:

```json
{
  "enabled": false,
  "stopped_at": "...",
  "stop_reason": "dependency_install_failed",
  "stop_message": "因 R 包 GSVA 安装失败，任务停止，已退出 auto 模式。",
  "source": {
    "card_id": "rna_immune",
    "run_id": "run_xxx",
    "job_id": "depjob_xxx"
  }
}
```

Chat message:

```text
因 R 包 GSVA 安装失败，任务停止，已退出 auto 模式。

当前 card：免疫浸润分析
下一步：请检查 omicverse 环境或手动安装缺失依赖后重新开启 /auto。
```

## API Surface

Suggested public API:

```text
GET  /api/projects/{project_id}/manager-auto
POST /api/projects/{project_id}/manager-auto
POST /api/projects/{project_id}/manager-auto/stop
GET  /api/projects/{project_id}/manager-wake-events
POST /api/projects/{project_id}/chat-sessions/{session_id}/messages
```

Suggested internal API:

```text
POST /api/internal/manager-wake/projects/{project_id}/events
```

Request examples:

```json
{
  "enabled": true,
  "mode": "continuous",
  "owner_session_id": "session_xxx"
}
```

```json
{
  "enabled": false,
  "reason": "user_off"
}
```

Append messages response should include the new session revision:

```json
{
  "session": {
    "session_id": "session_xxx",
    "revision": 43,
    "updated_at": "..."
  }
}
```

## Frontend Integration

`ManagerChatPanel` should:

- detect slash commands `/auto`, `/auto status`, `/auto off`, `/auto stop`, `/auto once`;
- call manager-auto API instead of sending those commands through normal chat;
- refetch project auto state after command completion;
- switch send button style when current session owns auto mode;
- show `/btw` state when another session owns auto mode;
- poll or subscribe to current session updates so backend-appended auto messages appear.
- include `session_id` in every Manager request;
- avoid overwriting backend-appended messages with stale local saves.

`SideNav` should:

- mark the owner session with a small `AUTO` indicator;
- render the owner session with a subtle breathing state while auto mode is enabled;
- show other sessions as `/btw` while auto mode is active;
- refetch chat session list periodically while auto mode is active.

### Owner Session Breathing State

The session that enabled auto mode should be visually distinct in `SideNav`.

Suggested states:

- `AUTO idle`: auto mode is enabled, but no wake event is currently being processed.
- `AUTO running`: a card run or dependency job started by auto mode is active.
- `AUTO thinking`: ManagerWakeProcessor is currently running a Manager turn.
- `AUTO stopped`: auto mode exited due to user command or error.

UI behavior:

- owner session row gets a small `AUTO` badge;
- while state is `running` or `thinking`, the badge or session dot uses a subtle breathing animation;
- other sessions show `/btw` or a muted lock indicator;
- do not use a top global banner.

CSS direction:

```css
.nav-session-item.auto-owner .auto-badge.running {
  animation: auto-breathe 1.8s ease-in-out infinite;
}

@keyframes auto-breathe {
  0%, 100% { opacity: 0.55; transform: scale(1); }
  50% { opacity: 1; transform: scale(1.06); }
}
```

### Active Session Refresh

Minimum viable frontend refresh:

- while auto mode is enabled, refetch the active chat session every 3-5 seconds when no local stream is running;
- refetch session list every 5-10 seconds so owner session badges and new messages are visible;
- pause auto-refresh while the user is actively streaming a response in that same session;
- merge fetched messages by id instead of replacing unsaved local draft/stream state.

## Auto Directives During Auto Mode

The owner session should support追加指令 during auto mode. This lets the user steer the automatic run without breaking the single-owner model.

Examples:

```text
优先跑免疫浸润，失败就停。
不要再尝试安装 R 包，缺依赖就退出 auto。
接下来只跑可视化相关 cards。
```

### Owner Session Behavior

If the user sends a message in the auto owner session while auto mode is enabled:

- record it as a normal user message in the owner session;
- classify it as an auto directive unless it is a slash command such as `/auto off`;
- append it to `manager_auto.pending_directives`;
- if the directive changes priority and a previous current card is not actively executing, Manager may reset that previous card and start the newly prioritized card;
- if the previous current card is actively executing, the directive waits until the next wake point unless the user sends `/auto stop`;
- if no Manager wake turn is running and no active run/job blocks immediate action, trigger a directive wake event;
- if a Manager wake turn or long run/job is active, queue the directive and show a short Manager/system message: `已加入 auto 指令队列，将在下一次唤醒时处理。`

The directive must not interrupt a live executor process. It influences the next Manager decision point.

### Non-Owner Session Behavior

Non-owner sessions are in `/btw` mode and cannot add auto directives. If the user tries to steer auto mode from a non-owner session, reply:

```text
当前 auto mode 由另一个 session 持有。这里处于 /btw 查询模式，不能追加自动运行指令。请切换到 AUTO session 或先停止 auto mode。
```

### Auto State Shape

Extend `graph.metadata.manager_auto`:

```json
{
  "enabled": true,
  "owner_session_id": "session_xxx",
  "state": "idle",
  "pending_directives": [
    {
      "id": "directive_xxx",
      "message_id": "msg_xxx",
      "text": "优先跑免疫浸润，失败就停。",
      "created_at": "...",
      "status": "pending"
    }
  ]
}
```

Allowed directive statuses:

- `pending`;
- `consumed`;
- `superseded`;
- `rejected`.

### Wake Envelope

ManagerWakeProcessor should include pending directives in the structured envelope:

```json
{
  "auto_mode": true,
  "wake_event": {
    "kind": "directive_received",
    "source_id": "directive_xxx"
  },
  "pending_directives": [
    "优先跑免疫浸润，失败就停。"
  ]
}
```

After Manager acknowledges or acts on a directive, mark it `consumed`. If a later directive overrides an earlier one, mark the older directive `superseded`.

### Prompt Guidance For Directives

Add to auto prompt:

```text
Auto directives are user instructions added while auto mode is running.

Treat pending directives as higher-priority steering constraints for future actions.
Do not interrupt a live executor process.
If a directive prioritizes a different card and the current card is not actively executing, you may reset the current planned/failed card state and start the requested card when dependencies allow.
If a directive conflicts with safety rules or project contracts, reject the directive, explain why, and keep or stop auto mode according to the severity.
When you act on a directive, briefly mention what changed and mark it as consumed through the auto state update.
```

## Testing Plan

Backend tests:

- `/auto` enables auto state and records owner session.
- second session cannot enable auto while owner exists.
- non-owner mutating tool calls are rejected during auto mode.
- wake event enqueue is idempotent.
- reviewed run enqueues one `card_run_reviewed` wake event.
- dependency-missing run enqueues `runtime_dependency_missing`.
- dependency job completion enqueues success/failure event.
- wake processor appends Manager response to owner session.
- auto stop writes `stop_reason` and appends stop message.
- stale full-session save does not remove backend-appended auto messages.
- append-session API is idempotent by message id.
- mutating internal tool calls without owner `session_id` fail during auto mode.
- dependency install job preserves `card_id/run_id/wake_id` source fields.
- stale `running` wake event can be retried or marked failed.
- owner session user message during auto mode creates a pending directive.
- non-owner session cannot add auto directives.
- directive wake event includes pending directives and marks consumed directives.
- pre-existing failed card can be reset and run by auto mode.
- current-loop card failure stops auto after two unsuccessful attempts.
- auto mutation scope permits input/output/runtime updates but blocks skill/script/parameter changes.
- chain limit is computed from executable card count.
- UI mutation endpoints/buttons are locked while auto mode is active.

Frontend smoke tests:

- `/auto` changes send button icon/state.
- owner session shows `AUTO`.
- non-owner session shows `/btw` and blocks mutation request.
- auto stop restores normal send button.
- backend-appended auto messages appear without page reload.
- active auto owner session does not lose backend-appended messages after local save.
- requests include the current `session_id`.
- owner session shows breathing `AUTO running/thinking` state.
- owner session can queue a directive while auto mode is active.
- non-owner session shows `/btw` and blocks directive-style mutation requests.
- configuration/run/delete UI controls are read-only while auto mode is active.

Manual workflow smoke:

1. Open session A and send `/auto`.
2. Start a card run that reports missing dependencies.
3. Verify Manager receives dependency wake, installs dependencies, then waits.
4. Verify dependency job completion wakes Manager again.
5. Verify Manager reruns the card or exits with a clear reason.
6. Open session B and verify it cannot mutate blueprint while session A owns auto mode.

## Implementation Order

1. Add `session_id` propagation through frontend, backend chat models, ManagerService, and manager-agent.
2. Add append-safe chat session persistence: append API and/or revision-aware merge.
3. Add auto state model/service and `/auto` APIs.
4. Add frontend slash command handling and send button auto state.
5. Add owner-session breathing UI and `/btw` SideNav indicators.
6. Add auto directive queueing for owner-session messages.
7. Add `/btw` mode tool filtering in manager sidecar.
8. Add backend mutating-tool ownership guard.
9. Add wake event model/service with idempotent persistence.
10. Wire WorkerService terminal/blocking events into wake queue.
11. Add dependency job source fields and wire RuntimeDependencyJobService completion into wake queue.
12. Add ManagerWakeProcessor and owner-session response persistence.
13. Add auto-mode prompt guidance, pending directives, and structured wake event envelope.
14. Add auto stop rules and tests.
