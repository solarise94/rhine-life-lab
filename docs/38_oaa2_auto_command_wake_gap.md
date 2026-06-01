# OAA-2 `/auto <目标>` 命令未建立唤醒授权

Status: bug review note.

Date: 2026-06-01

## 结论

OAA-2 暴露的问题不是 workboard 不能被 Manager 读取，也不是 Diff run 终态回调完全失效，而是：

```text
/auto <目标> 只有前端拦截时才会进入 auto 模式；
如果该文本绕过前端拦截并落入 /chat-stream，后端会把它当作普通 Manager 消息转发。
```

因此在失败窗口中，Manager 能够用普通 chat turn 消费 workboard 并启动 Differential Expression card，但项目没有持久化 `manager_auto` owner envelope。Diff run 完成后，后台终态回调没有 owner session 和 wake authorization，不能 enqueue `workboard_actionable` 唤醒事件。

关键区别：

```text
workboard tool permission != auto wake permission
```

普通 Manager chat 可以调用 workboard tools；但只有 `manager_auto.enabled=true`、`wake_allowed=true`、`owner_session_id=<session>` 被持久化后，后台 run/job 完成才允许唤醒 Manager 继续推进。

## 当前工作区状态说明

当前 `workspace/oaa-2` 已经被后续恢复操作推进过，不能直接当作原始失败瞬间的完整快照。

当前图上已有后续 auto 痕迹：

```json
"manager_auto": {
  "enabled": false,
  "owner_session_id": "session_65c671ef6eb9",
  "state": "cancelled",
  "started_at": "2026-06-01T07:30:25Z",
  "last_wake_id": "wake_workboard_9d3a1b145d0f",
  "chain_count": 6,
  "stopped_at": "2026-06-01T07:35:37Z",
  "stop_reason": "user_stop"
}
```

`workspace/oaa-2/chat/manager_wake_events.jsonl` 当前也存在多条后续 `workboard_actionable` wake。这个现状只能说明后续显式进入过 auto 并成功唤醒过，不推翻原始失败窗口的判断。

本文档关注的失败窗口是：用户输入 `/auto 启动分析吧` 后，Diff card 被普通 Manager turn 启动并完成，但该次输入没有建立 durable auto authorization。

## 观测证据

项目：

```text
workspace/oaa-2
```

失败窗口中的用户消息：

```text
/auto 启动分析吧
```

后续 Manager response 表现为普通 chat-stream 工具调用链，而不是 `/manager-auto` 命令确认：

```text
get_background_workboard
promote_workboard_item_to_todo
claim_workboard_item
submit_claimed_workboard_items
```

普通 chat turn 的自然语言总结类似：

```text
Card 2 已经在 workboard 上标记为 ready。先推进，同时清理那 8 个旧失败 dep job。
```

如果命中了当前前端 `/auto <目标>` 命令路径，预期会出现类似确认：

```text
已允许当前会话继续消费 workboard 并在后台唤醒。目标：...
```

Diff run 本身成功完成：

```text
run_dbe18836fec2
card_id = card_differential_expression_limma_vo_20260601_070453
status = reviewed
finished_at = 2026-06-01T07:19:29Z
needs_manager_attention = false
```

原始失败判定不是“run 没完成”，而是“run 完成时没有 auto owner 可以被唤醒”。

## 现有代码路径

### 前端命令路径

当前前端在 `frontend/components/manager-chat/ManagerChatPanel.tsx` 中拦截：

```ts
if (text === "/auto" || text.startsWith("/auto ")) {
  await handleAutoCommand(text);
  return;
}
```

`handleAutoCommand()` 会调用：

```text
POST /projects/{project_id}/manager-auto
```

对应 API binding 在 `frontend/lib/api.ts`：

```ts
enableManagerAuto(projectId, sessionId, "continuous", objective, userMessageId)
```

命中这条路径时，后端 `backend/app/api/manager_auto.py::enable_manager_auto()` 会：

- 校验 `session_id` 对应的 chat session 存在；
- 调用 `ManagerAutoService.enable(...)`；
- 持久化 `graph.metadata.manager_auto`；
- 对 `directive_text` 调用 `add_directive(...)`；
- 在允许时 enqueue `directive_received` wake；
- `ManagerAutoService.enable(...)` 内部还会 evaluate workboard，可能 enqueue `workboard_actionable` wake。

### 后端 chat-stream 路径

当前 `backend/app/api/chat.py::chat_stream()` 直接转发：

```python
stream = manager_service.stream_chat(project_id, request)
```

`backend/app/services/manager_service.py::stream_chat()` 会构造 sidecar payload：

```python
payload = {
    "project_id": project_id,
    "message": chat_request.message,
    "session_id": chat_request.session_id,
    "auto_mode": self._auto_payload(project_id, chat_request.session_id),
    ...
}
```

这里没有 slash-command interception。只要前端没有拦截，`/auto 启动分析吧` 就会作为普通 message 进入 Manager sidecar。

### 背景终态唤醒路径

run/job 终态回调最终进入：

```python
ManagerAutoService.notify_background_task_terminal(...)
```

该方法先读取当前 auto state：

```python
state = self.get_state(project_id)
owner_session_id = state.owner_session_id
if not state.enabled or not owner_session_id:
    return state
```

只有通过这道 guard，才会继续：

```python
evaluate_workboard_and_maybe_signal(project_id, owner_session_id)
```

并在 workboard 有 actionable items 时 enqueue：

```text
kind = workboard_actionable
```

所以失败窗口中的行为是当前代码的直接结果：没有 `manager_auto.enabled` 和 `owner_session_id`，终态回调正确地不唤醒。

## 根因

根因是 `/auto <目标>` 的语义只在前端实现，后端不是 authoritative command parser。

当前实际存在两种入口，语义不一致：

```text
前端拦截 /auto <目标>
  -> POST /manager-auto
  -> 持久化 manager_auto
  -> 允许后续 wake

任意客户端直接 POST /chat-stream，message="/auto <目标>"
  -> ManagerService.stream_chat()
  -> sidecar 普通 chat
  -> 可能消费 workboard
  -> 不持久化 manager_auto
  -> 后续 run/job terminal 无 wake owner
```

因此，用户看到 Manager 已经“照着 /auto 指令开始干活”，但系统实际没有进入 auto 模式。

## 为什么 `/auto + 命令` 不能等价于 auto 模式

需要明确产品契约：

```text
/auto <目标> 是授权命令，不是自然语言提示词。
```

只有当该命令被后端识别并持久化以下字段后，才算进入 auto 模式：

```json
{
  "enabled": true,
  "wake_allowed": true,
  "owner_session_id": "<session_id>",
  "view_workboard": true,
  "consume_workboard": true,
  "last_signaled_board_revision": null
}
```

如果 `/auto <目标>` 只是作为普通 chat text 给 Manager，Manager 可能会主动执行一次 workboard action，但这只是本轮 chat 的工具执行权限，不是后台继续执行授权。

这也是 OAA-2 的用户感知差异：

```text
用户以为：我已经 /auto 了，Diff 完成应继续唤醒 Manager。
系统实际：Manager 只是普通 chat turn 执行了一次 workboard，后续没有 auto owner。
```

## 应修复的边界

后端必须成为 slash command 的权威入口。前端拦截可以保留，但只能作为 UX 快捷路径，不能是唯一语义层。

必须新增后端 command interception，位置在 `/chat-stream` 调用 `ManagerService.stream_chat()` 之前。

实现上建议抽出 `ManagerCommandService`，并让以下两个入口共同调用同一套 command/use-case 方法：

```text
POST /projects/{project_id}/manager-auto
POST /projects/{project_id}/chat-stream, message="/auto ..."
```

这里要求零业务逻辑重复。`backend/app/api/manager_auto.py::enable_manager_auto()` 当前已有完整的 enable + directive + wake 流程，命令拦截器不能复制这段逻辑，否则 `/manager-auto` 与 `/chat-stream` 的幂等、wake、directive、错误语义会继续漂移。

```text
ChatRequest.message.strip()
  -> parse slash command
  -> 如果是 /auto 族命令，由后端处理并返回 SSE ack
  -> 否则才进入 ManagerService.stream_chat()
```

建议处理规则：

- `/auto <objective>`：要求 `session_id`，校验 session 存在，启用 auto，写入 directive，必要时 enqueue wake，返回 SSE ack，不调用 sidecar。
- bare `/auto`：返回指导信息，要求使用 `/auto <目标>`，不调用 sidecar。
- `/auto off` 和 `/auto stop`：调用 stop auto 逻辑，返回 SSE ack，不调用 sidecar。
- `/auto status`：返回当前 auto state 摘要，不调用 sidecar。
- `/auto once`：已废弃。命令路径统一返回弃用提示 SSE，不进入 `mode="once"`，不调用 sidecar。
- active auto 已由其他 session 持有时，非 owner session 发送 `/auto <目标>` 或 `/auto stop` 均返回 SSE `error`，错误内容应明确当前 owner session，业务语义与 REST 409 对齐。

这条修复不应该让“普通 chat 中 Manager 调用了 workboard tools”自动变成 auto 模式。auto 是显式授权，必须来自被识别的 command 或 API。

本轮修复范围仅覆盖 `/chat-stream`。Deprecated legacy 入口 `/chat` 与 `/chat-jobs` 不解析 slash command，保持现状；如果后续仍允许生产客户端使用 legacy 入口，再单独补同等 command interception。

## 命令语法

命令解析必须有明确边界，避免前后端或不同客户端不一致。

建议规范：

```text
normalized = request.message.strip()
match = re.match(r"^/auto(?:\s+(.*))?$", normalized)
```

解析规则：

- 前导和尾随空白允许，先 `strip()`。
- 命令名大小写不做宽松匹配，`/Auto` 不视为命令，进入普通 chat 或返回未知命令，二选一需固定。
- bare `/auto` 的 objective 为空。
- `/auto  继续推进` 归一化为 objective `继续推进`。
- 子命令白名单为 `off`、`stop`、`status`、`once`。
- 除白名单外，`/auto <anything>` 均视为 objective。
- `/auto@目标` 不匹配 `/auto` 命令，不应被误解析。
- 不需要 `shlex` 语义，避免中文、引号、路径类目标被意外拆分；正则整体捕获 objective 更稳。

大小写策略如果产品想宽松支持 `/Auto`，也必须在后端统一实现，并同步前端提示。当前建议保持严格小写，降低误触发风险。

## 返回格式建议

因为 `/chat-stream` 是 SSE endpoint，后端 command handler 必须返回短 SSE stream，而不是普通 JSON。即使是 `session_id` 缺失、重复 enable、非 owner stop 这类错误，也应返回 canonical `error` stream event，保持前端 parser 一致。

当前 `ChatStreamRelay` 绑定的是 `ManagerService.stream_chat()`，不直接支持无 sidecar 的短流；实施时应抽出或新增一个复用其 canonical 事件处理/持久化模型的 helper，例如：

```text
ChatStreamRelay.run_payloads_to_session(...)
ChatStreamRelay.run_static_response_to_session(...)
```

目标是复用现有 `thinking_delta`、`text_delta`、`response`、`done`、`error` 事件形态和 `ChatSessionService.upsert_message()/publish_stream_event()` 机制，不为命令路径新增前端 parser 分支。

最小事件序列可以是：

```text
data: {"type":"text_delta","delta":"已允许当前会话继续消费 workboard 并在后台唤醒。目标：..."}

data: {"type":"response","response":{"message":"已允许当前会话继续消费 workboard 并在后台唤醒。目标：..."}}

data: {"type":"done"}
```

错误事件示例：

```text
data: {"type":"error","detail":"session_id is required for /auto commands."}
```

注意：当前前端实际识别的是 `text_delta` / `thinking_delta` / `response` / `done` / `error`，不是泛化的 `delta`。

## Chat Session 写入策略

后端拦截 `/chat-stream` 后不能只把 ack 返回给当前 HTTP stream。否则前端刷新或重连后，该命令确认会从 chat history 中消失。

命令路径必须写入 chat session：

- 用户命令消息应以 `role="user"`、`content="/auto ..."` 写入。
- Manager ack 应以 `role="manager"`、`content=<ack>`、`state="done"` 写入。
- 建议在 timeline item 或 message metadata 中标记 `source="command"`；当前 `ChatSessionMessage` 没有 metadata 字段，如需避免 schema 变更，可先用 timeline item `kind="command"` 或固定 id 前缀表达来源。
- `source="command"` 的 user command 与 manager ack 默认不进入后续 LLM 上下文。构造 `session_messages/messages` 时必须过滤它们，否则 Manager 可能把“已允许当前会话...”当作业务上下文反复引用。

现有前端 `handleAutoCommand()` 会在本地合成 user/manager 消息。后端接管后，需要统一由后端持久化，前端只消费 stream/session event，避免本地和服务端双写。

## `message_id` 透传

当前前端调用 REST `enableManagerAuto(...)` 时会把 `userMessageId` 作为 directive `message_id` 传给后端。

但 `backend/app/models/chat.py::ChatRequest` 当前没有 `message_id` 字段：

```python
class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    ...
```

因此 `/chat-stream` 命令路径采用固定方案：

```python
class ChatRequest(BaseModel):
    message: str
    message_id: str | None = None
    session_id: str | None = None
    ...
```

前端 `api.streamChat(...)` 必须透传当前 user message id，directive 继续绑定该 id。只有服务端收到空 `message_id` 的兼容客户端请求时，才允许后端生成 server-side user message id，并用该 id 写入 chat session 与 directive。

不建议让 `/chat-stream` command directive 长期 `message_id=None`，否则后续排查 directive 来源会弱于 REST 路径。

## 幂等与错误语义

命令路径必须保持 REST 路径相同的业务语义，但错误传输格式必须是 SSE。

当前 REST `enable_manager_auto()` 对 active scoped continuation 会返回 409。`/chat-stream` 中连续两次 `/auto <目标>` 应保持同等拒绝语义：

```text
active auto + same owner + pending directives 或 state 未完成
  -> SSE error
  -> 不重复 enable
  -> 不新增 directive
  -> 不调用 sidecar
```

跨 session 语义同样对齐 REST 409：

```text
active auto + owner_session_id != request.session_id
  -> SSE error
  -> detail 包含 owner_session_id
  -> 不抢占 owner
  -> 不新增 directive
  -> 不调用 sidecar
```

`session_id=None` 也必须在命令解析阶段 reject：

```text
message="/auto 启动分析吧", session_id=None
  -> SSE error
  -> 不调用 sidecar
  -> 不写 manager_auto
```

不应返回普通 400 JSON，因为调用的是 `/chat-stream`，前端 `streamChat()` 期望读取 SSE body。HTTP 层可以保持 200 + `error` event，或使用非 200 但 body 仍是可读错误文本；为了一致性，建议 200 + canonical `error` event。

## 失败模式清单

后续修复时需要覆盖这些非理想入口：

- 浏览器使用旧 bundle，前端没有 `/auto <目标>` handler。
- 移动端、脚本、测试工具或第三方客户端直接调用 `/chat-stream`。
- 前端 handler 抛错后错误地 fallback 到普通 chat-stream。
- 用户在 auto owner session 之外发送 `/auto <目标>`。
- 用户在已有 active auto session 内再次发送 `/auto <目标>`。
- 用户发送 `/auto stop` 但当前不是 owner session。

其中前三项是 OAA-2 类问题的核心：不能假设所有 chat ingress 都经过最新前端代码。

## 验收测试

建议增加后端回归测试：

- `POST /chat-stream`，message 为 `/auto 启动分析吧` 时，持久化 `manager_auto.enabled=true`。
- 同一路径必须写入 `owner_session_id=session_id`、`wake_allowed=true`、`consume_workboard=true`。
- 同一路径不得调用 `ManagerService.stream_chat()` 或 sidecar。
- 同一路径应创建 pending directive，`scope_objective` 等于命令目标。
- 如果当前 workboard 有 actionable items，应 enqueue `workboard_actionable` 或 `directive_received` wake，具体按最终产品策略固定。
- bare `/auto` 返回指导信息，不调用 sidecar，不启用 auto。
- `/auto stop` 能停止 owner session 的 auto，并拒绝非 owner session。
- `session_id` 缺失时返回 canonical SSE `error` event，不调用 sidecar。
- active auto 期间重复发送 `/auto <目标>` 返回 canonical SSE `error` event，不重复 enable，不新增 directive。
- active auto 已由其他 session 持有时，非 owner session 发送 `/auto <目标>` 返回 canonical SSE `error` event，detail 包含 owner session。
- `/auto once` 返回弃用提示 SSE，不进入 `mode="once"`。
- `/auto  继续推进`、`/auto@目标`、`/Auto 继续推进` 的解析行为符合后端统一语法规则。
- `/chat-stream` command path 写入 user command message 和 manager ack message，刷新 chat session 后不丢失。
- directive `message_id` 绑定到前端透传或后端生成的 user command message id，不长期为空。
- `source="command"` 的 command/ack 消息默认从后续 LLM context 构造中过滤。
- Deprecated `/chat` 与 `/chat-jobs` 保持现状，不解析 `/auto` slash command。
- 前端不需要为 command stream 新增事件类型分支，仍只消费 canonical `text_delta` / `response` / `done` / `error`。
- 后续 run terminal callback 在 downstream workboard actionable 时能 enqueue wake。

建议增加一个 OAA-2 形状的集成 fixture：

1. 创建 project 和 chat session。
2. 准备一个 ready workboard item。
3. 直接调用 `/chat-stream` 发送 `/auto 启动分析吧`，不走 `/manager-auto` API。
4. 验证 `graph.metadata.manager_auto` 已持久化。
5. 模拟 ready card 被提交并完成。
6. 验证下一次 workboard actionable revision 产生 wake event。

## 修复后预期行为

修复后，用户从任何 chat ingress 发送：

```text
/auto 继续推进剩余 ready workboard 项，直到出现需要用户决定的阻塞
```

都必须满足：

- 不进入普通 Manager sidecar chat；
- 后端持久化 auto owner envelope；
- 前端和 chat session 中能看到明确 ack；
- 后续 run/job terminal callback 可以基于 owner session 唤醒 Manager；
- 如果 auto 未被授权，workboard tools 的一次性调用不会隐式开启后台继续执行。

## 与文档 39 的关系

本文档 38 解决的是“没有进入 auto 模式，所以 Diff 完成后没有 wake owner”的问题。

`docs/39_oaa2_auto_wake_loop_review.md` 解决的是“已经进入 auto 模式后，workboard revision/失败项导致频繁唤醒”的问题。

二者修复边界不同：

```text
文档 38：确保 /auto <目标> 一定建立 durable auto authorization。
文档 39：确保已授权 auto 不会被相同或不可处理 workboard 状态反复唤醒。
```
