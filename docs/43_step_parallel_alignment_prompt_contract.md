# Step 并行分组 Prompt 契约审阅

Status: design review.

Date: 2026-06-02

## Summary

OAA-2 及近期多步规划场景暴露了一个 Manager 行为问题：模型在批量建卡时
几乎不做并行分组，习惯给每张卡片分配严格递增的 `step`（1 / 2 / 3 / 4），
而不是把无依赖关系、同属一层的卡片放到同一个 `step`（1 / 1 / 1 / 2）。

后端实际上已经有了并行分组算法 `AssetTimelineService.parallel_batches(...)`，
但当前它只产出 `{"batch_index", "card_ids"}` 两项
（见 `backend/app/services/asset_timeline_service.py:303`）。下游
`background_workboard_service._parallel_group_for_card()` 在 batch 里找
`batch_id` 或 `step` 字段才会返回非空串
（`backend/app/services/background_workboard_service.py:958`），
而 `parallel_batches` 并不生成这两个字段——所以现在 ready_to_start
payload 里的 `parallel_group` 实际是 `""`，并不是 prompt 可以消费的
"step_N"。

```text
AssetTimelineService.parallel_batches(...)
    -> {"batch_index": int, "card_ids": list[str]}          # 当前实际输出
    # 期望输出（本 doc 必做前置）：
    -> {"batch_index": int, "step": int, "card_ids": list[str]}
    -> background_workboard_service: ready_to_start.payload.parallel_group = "step_N"
    -> background_workboard_service: ready_to_start.payload.safe_to_batch_start = true
```

因此本 doc 在 prompt 层收敛之前，需要先补一个**后端前置修复**：
让 `parallel_batches` 把 `step = batch_index + 1` 写进每个 batch dict，
使 `_parallel_group_for_card()` 返回有意义的 `"step_N"`。没有这一步，
prompt 里所有引用 `parallel_group = "step_N"` 的措辞都是对模型的虚假
信号。

Manager 侧的 system prompt、工具描述、planner prompt 也没有告诉模型
"step 是并行层，不是串行序号"。校验器只卡下界（`step < min_step`），
不约束上界。模型用最保守的逐卡 +1 也能通过所有校验，于是并行规划动机塌缩。

## 现象

OAA-2 项目实际蓝图：

```text
card_1 (无输入)                 -> step 1
card_2 (无输入)                 -> step 2     # 期望 step 1
card_3 (输入 card_1, card_2)    -> step 3     # 期望 step 2
card_4 (输入 card_3)            -> step 4     # 期望 step 3
```

`AssetTimelineService.parallel_batches` 返回的批次是：

```text
batch 0: [card_1, card_2]
batch 1: [card_3]
batch 2: [card_4]
```

与期望 step 对齐，但模型实际建出来的 step 是 [1, 2, 3, 4]。UI 把四张卡片
画成四列，workboard 把 batch 0 两张卡片并排显示却分属两个 step 层，
视觉与数据矛盾。

## 根因

### Prompt 侧

1. **Manager system prompt** (`manager-agent/src/server.js:134-211`)
   - Line 141: "Card step is the timeline layer. A card must be later than the
     assets it consumes."
   - Line 206: "step is optional and controls timeline grouping."
   - **只定义了下界，没有定义"同依赖层 = 同 step"。**

2. **`create_card` 工具描述** (`server.js:1668-1710`)
   - description 完全不提 step 的并行语义。
   - `parameters.step: Type.Optional(Type.Number())` 没有任何 hint。

3. **`revise_card_plan` 工具描述** (`server.js:1712-1754`)
   - 同上。

4. **Legacy planner** (`backend/app/services/manager_planner.py:83-96`)
   - SYSTEM_PROMPT 的 "Preferred patterns" 段写：
     "For multi-step workflows, think through the full dependency chain first,
     **but return only the next executable layer** in a single proposal.
     Do not include downstream cards that depend on assets planned in the
     same proposal."
   - **反向压制**：鼓励模型每次只吐一层，进一步削弱了同层多卡一次性建出的
     动机。

5. **Harness prompt** (`manager_planner.py:110-125`)
   - "when you call a proposal tool submit only the current executable layer
     whose inputs already exist in project context."
   - 同样有压制效果。

6. **`get_background_workboard` 返回** (`server.js:588-593`)
   - 已经把后端算好的 `parallel_group` 透传给 Manager：
     `parallel_group: item.payload?.parallel_group`。
   - **但 system prompt 完全没解释这个字段**，模型不知道这是后端已经分好的
     并行层、也不知道应该用它来规划新卡片。

7. **Wake prompt** (`backend/app/services/manager_auto_service.py:563`)
   - 只说 "Consume at most one actionable workboard item or one claimed run
     batch in this turn."
   - 对 frontier 规划没有 "对齐 parallel_group" 的激励。

8. **"单 turn 单任务" 歧义约束**（system prompt + wake prompt + 设计文档）
   - `manager-agent/src/server.js:172`：
     "In auto/background turns, call get_background_workboard first.
     **Consume at most one actionable workboard item or one claimed run batch
     per turn.**"
   - `backend/app/services/manager_auto_service.py:563`：同一句话在 wake
     prompt 里再次重复。
   - `docs/32_unified_background_task_supervisor.md:360`：
     "Each Manager turn should consume at most one action batch."
   - `docs/36_manager_workboard_prompt_contract.md:286`：
     "Manager should consume at most one workboard decision cycle per turn."
   - 预期读法是 "一次 turn 要么消费 1 个零散 item，要么消费 1 个 run batch
     （batch 可含多张卡）"；模型实际读成 "**一次 turn 只能做 1 件事**"。
   - 结果：不会在同一 turn 里同时 `install_runtime_dependencies` +
     `start_card_run`（即使两件完全独立）；不会在同一个 turn 里连续
     claim 多个 ready 卡后一次性 submit；后台任务被串行化。
   - 和下面 F 想鼓励的 "同层并行" 直接冲突。

### 校验侧

9. **`AssetTimelineService.validate_card`**
   (`backend/app/services/asset_timeline_service.py:116-170`)
   - Line 159-165: 只校验 `candidate.step < min_step`（下界），
     不校验 `candidate.step > min_step` 的上界散落。
   - 模型给 step=5 而 min_step=1，校验通过。

10. **`_recommended_step`**
    (`backend/app/services/manager_blueprint_tools.py:2677-2687`)
    - 只返回下界 `min_step = max(asset.step + 1 for each input)`，
      没有 "同层对齐" 逻辑，也没有返回 "当前同层已有 N 张卡" 这类参考。

11. **create_card 成功返回**：只返回 `{ ok, card_id, asset_ids, ... }`，
    不回传 `parallel_group` 或 `step_alignment_hint`，模型建完卡也不知道
    自己的 step 是不是和同层其他卡对齐了。

### script_preference 传导链

`AGENTS.md` 明确写过：

> `script_preference` is a soft planning hint — persist in
> `card.executor_context.instruction_blocks`, not as executor hard logic.

实际代码里这条约定在四处断掉：

12. **`configure_card_execution` 工具没有 `instruction_blocks` 字段**
    (`manager-agent/src/server.js:1822-1835`)。
    - schema 只有 `card_ids / skills / mcp_servers / runtime_bindings(conda_env, r_env)`。
    - Manager 看到用户选了 `prefer_r` 后，**没有工具**把这个偏好写到
      `card.executor_context.instruction_blocks`，只能在对话里说一句
      "我建议用 R"，executor 永远收不到。

13. **`ExecutorContext` 模型没有 `script_preference` 字段**
    (`backend/app/models/executor.py`)。
    - 只有 `instruction_blocks: list[str]`，没有独立的 `script_preference`
      槽位；前端存的偏好和卡片执行契约之间没有显式桥接。

14. **`worker_service._default_executor_context` 不读偏好**
    (`backend/app/services/worker_service.py:1934-1937`)。
    - 写死的 `instruction_blocks` 只有两条通用规则（"Prefer reproducible
      scripts..." / "Summarize findings conservatively..."），**完全不读**
      `project.runtime_preferences.script_preference`。
    - 即使 Manager 在对话里承诺了 R，executor 跑起来还是按默认行为选
      Python。

15. **`install_runtime_dependencies` 的 `ecosystem` 没有偏好默认**
    (`manager-agent/src/server.js:1863`)。
    - schema：`ecosystem: Type.String({ description: "python or R" })`。
    - 用户选了 `prefer_r`，模型装包时仍然可能 `ecosystem="python"`，
      因为 description 没告诉它 "在 R 偏好下默认填 R"。

### 行为后果

- 同层卡片被拆成多个 step → UI 时间轴出现假的串行化。
- workboard 的 `parallel_group` 信号和卡上的 `step` 字段语义分裂：
  workboard 说这两张卡可并行（`step_1`），但卡上 step 分别是 1 和 2。
- auto 模式下 `submit_claimed_workboard_items` 按 workboard batch 提交，
  实际跑起来确实是并行的；但用户从蓝图上看到的是串行序号，认知负担高。
- 模型在 revise_card_plan 修卡时也不会主动 "拉齐" 同层卡片的 step。
- **用户选了 `prefer_r` / `prefer_python`，偏好形同虚设**：Manager
  在对话里说 "我按你的偏好选了 R"，但 executor 实际跑的还是默认
  Python；`install_runtime_dependencies` 的 ecosystem 也不按偏好默认。
  整条传导链断在 Manager → 卡片 executor_context 这一跳。

## 收紧方案

### Prompt 层（必做，影响模型行为）

**A. Manager system prompt 加 step 语义定义**
位置：`manager-agent/src/server.js` `buildSystemPrompt()`，紧跟
"Card step is the timeline layer" 这句之后。

建议插入：

```text
- Card step is the parallel-execution layer, not a serial sequence number.
  Two cards that share no dependency relationship (same min-step floor and no
  transitive input/output link) MUST share the same step value. Only increment
  step when a card consumes an asset produced by an earlier step.
- When creating or revising multiple cards in one turn, align cards at the
  smallest valid step. Use get_background_workboard.ready_to_start[].parallel_group
  as the authoritative parallel layer for existing planned cards; new cards that
  belong to the same layer should reuse that step value.
- A workboard `parallel_group` like "step_N" means the backend has already
  verified these cards can run together. Do not reassign them to different
  steps without a dependency reason.
```

**B. `create_card` 工具 description 加并行 hint**
位置：`server.js:1670`。

建议在现有 description 后追加：

```text
Prefer the smallest valid step for each card; group independent cards at the
same step so the workboard can batch them. When `get_background_workboard`
reports existing ready cards under parallel_group "step_N", align new cards
with the same inputs to the same step.
```

`step` 参数的 schema description 同步加上：

```json
"step": {
  "description": "Parallel-execution layer. Cards with no mutual dependency
    should share the same step. Backend validates the lower bound derived
    from input assets; do not pad step beyond that bound."
}
```

**C. `revise_card_plan` 工具 description 加对齐提示**
位置：`server.js:1714`。追加：

```text
When adjusting step, prefer the smallest valid value that keeps the card in
its parallel layer. Do not shift a card to a later step unless a new input
dependency requires it.
```

**D. Legacy planner Preferred patterns 改写**
位置：`backend/app/services/manager_planner.py:96`。

原文：

```text
- For multi-step workflows, think through the full dependency chain first,
  but return only the next executable layer in a single proposal. Do not
  include downstream cards that depend on assets planned in the same proposal.
```

改为：

```text
- For multi-step workflows, think through the full dependency chain first.
  Return the full next parallel layer in a single proposal: include every
  card whose inputs already exist in project context, and assign them the
  same `step`. Omit only downstream cards whose inputs are planned outputs
  of cards in the same proposal.
```

**E. Harness prompt 同步改写**
位置：`manager_planner.py:122`。

原文：

```text
- For multi-step workflows, plan the whole sequence mentally, but when you
  call a proposal tool submit only the current executable layer whose inputs
  already exist in project context.
```

改为：

```text
- For multi-step workflows, plan the whole sequence mentally. When you call
  a proposal tool, submit the full next parallel layer whose inputs already
  exist in project context and give them the same `step`. Do not split
  independent cards of the same layer across different steps.
```

**F. Wake prompt 增加 batch 对齐信号并解除单任务歧义**
位置：`backend/app/services/manager_auto_service.py:563`。

原文：

```text
Call get_background_workboard first. Consume at most one actionable
workboard item or one claimed run batch in this turn.
```

改为（和 J 段对齐）：

```text
Call get_background_workboard first. This turn may end with at most one
async-boundary-yielding action (one submit_claimed_workboard_items, or one
start_card_run / rerun_card, or one install_runtime_dependencies). You may
combine independent non-yielding tool calls and may start one dependency
install plus one run-yielding action in the same turn when they are truly
independent. When planning new cards from a frontier wake, align new cards
to the parallel_group of existing ready_to_start items that share their
input layer.
```

**J. 单 turn 单任务歧义收紧（以 async boundary 为计量单位）**
位置：
- `manager-agent/src/server.js:172`（system prompt Judgment 段）
- `backend/app/services/manager_auto_service.py:563`（wake prompt，
  已在 F 段一并改写）

system prompt 原文：

```text
- In auto/background turns, call get_background_workboard first. Consume
  at most one actionable workboard item or one claimed run batch per turn.
```

改为：

```text
- In auto/background turns, call get_background_workboard first. Each turn
  should end with at most one async-boundary-yielding action batch: one
  submit_claimed_workboard_items call, or one start_card_run / rerun_card,
  or one install_runtime_dependencies. You may combine independent
  non-yielding tool calls (inspect_*, find_*, get_*, configure_card_execution,
  annotate_card, write_project_memory) freely. You may also start one
  install_runtime_dependencies plus one run-yielding action in the same turn
  when they are truly independent; the async boundary will still yield the
  turn once. Do not call start_card_run repeatedly in the same turn to
  emulate a batch — use submit_claimed_workboard_items instead.
```

关键点：

1. **async-boundary-yielding** 作为计量单位。这是 doc 32 / doc 37 已有的
   概念：async boundary 才是 turn 结束点，而不是 "1 件事"。
2. 显式允许 **install + run 同 turn**：`install_runtime_dependencies` 和
   `start_card_run` 都返回 background/job_id，async boundary 会自动
   yield。串行化它们没有收益。
3. 显式允许 **多个非 yielding 工具 + 一个 yielding 动作**：inspect、
   find、configure 这类工具本来就不让出 turn。
4. 显式禁止 **重复 start_card_run 模拟 batch**：这是 doc 34 / doc 37
   已经写明的反模式，正确路径是 `submit_claimed_workboard_items`。
5. 和 doc 32（unified supervisor）/ doc 36（workboard prompt）/ doc 37
   （claim/wake/stop）的 "one action batch per turn" 语义对齐：把
   "at most one workboard item or one run batch" 的歧义消掉，文档侧的
   "one action batch" 口径保持不变。

wake prompt 同义改写已在 F 段完成，两处同步即可。

**J-tool. async-boundary guard 必须同步放松（不只是 prompt 改动）**
位置：`manager-agent/src/server.js:1440-1454`（`callLoggedTool` 的
`asyncBoundary.active` 守卫）。

J 段 prompt 允许 "install_runtime_dependencies + 一个 run-yielding 动作
同 turn"，但当前 `callLoggedTool()` 一旦 `asyncBoundary.active = true`
就拒绝除 `stop_card_run`（仅中断场景）之外的所有后续工具调用，返回
`{ ok: false, error_type: "async_boundary_active", terminal: true,
wait_for_wake: true }`。如果只改 prompt 不改守卫，模型按 prompt 执行
"install + run" 会在第二步被硬拒，turn 直接 yield，看起来像后端出错。

这是**工具控制变更**，不是单纯 prompt 契约更新。需要的具体改动：

1. 在 `callLoggedTool()` 里，`asyncBoundary.active` 触发后，放行以下
   两类调用（其余仍返回 `async_boundary_active`）：
   - 所有 **非 yielding 工具**：`inspect_*` / `find_*` / `get_*` /
     `configure_card_execution` / `annotate_card` / `write_project_memory`。
     这些本来就不让出 turn，不应该被算作 async boundary 之后续。
   - **首个 yielding 工具之前**的 `install_runtime_dependencies`：
     如果当前 turn 还没触发过 yielding action（即 `asyncBoundary.active`
     仍为 false），允许 `install_runtime_dependencies` 和随后的一个
     run-yielding 工具共存；`install_runtime_dependencies` 本身的返回
     payload 需要正确设置 `async_boundary: true`，使得 guard 在它执行
     后才激活。
2. `asyncBoundary` 状态机新增 `installBeforeRun` 标记：
   - `install_runtime_dependencies` 先于 run-yield 执行时，标记
     `installBeforeRun = true`；
   - 随后的 run-yielding 工具把 `asyncBoundary.active = true`，之后
     除了非 yielding 工具，一律拒绝。
3. 显式禁止的反模式保持不变：同一 turn 内**重复** `start_card_run`
   模拟 batch 仍应被拒；正确路径是 `submit_claimed_workboard_items`。

测试（`manager-agent/test/`）：

- **install + run 同 turn**：构造一个 turn 依次调用
  `install_runtime_dependencies` 与 `start_card_run`，期望第二次调用
  正常执行（不被 guard 拦截），turn 在 run 返回后 yield。
- **重复 run 仍被拒**：构造一个 turn 依次调用两次 `start_card_run`，
  期望第二次被 guard 拦截并返回 `async_boundary_active`。
- **install + submit_claimed 同 turn**：允许，submit 之后 yield。
- **install + run + run 三段式**：第三次 run 必须被拒。
- **中断场景 `stop_card_run`**：现有 `userRequestedInterrupt` 路径
  不变，regression 测试通过。

Doc 交叉引用：

- doc 32（unified supervisor）/ doc 37（claim/wake/stop）里 "one action
  batch per turn" 的 "batch" 语义要和这里一致：一个 batch = 至多一次
  async boundary yield，之前可自由组合非 yielding 工具与 install。
- 如果这两份 doc 里有更严格的 "单 turn 单 tool 调用" 措辞，需要同步
  修订；否则 prompt 和文档之间会出现两套口径。

**J-auto. auto 模式下不要等待用户授权**
位置：
- `manager-agent/src/server.js:185-186`（依赖安装 blocked / resolver
  blocked 的 Judgment 文案）
- `manager-agent/src/server.js:3309`（auto episode `userEnvelope.instruction`）
- `backend/app/services/manager_auto_service.py:563`（wake prompt）

现状：system prompt 对 blocked dependency / resolver 状态的默认动作是
向用户说明阻塞并询问下一步，例如手动准备 runtime、提交更窄 package
请求、或批准 fallback。这个口径适合普通交互 turn，但不适合 `/auto`
episode：auto wake 没有用户正在等待确认，模型如果停下来问授权，会让
episode 悬在 `pending_wake` / `complete` 之间，下一次 wake 仍然看到同一
个无法推进的 fuel。

建议新增 auto 专用 prompt 规则：

```text
- In auto mode, do not ask the user to wait for authorization, manual runtime
  preparation, package-source approval, script binding, or other interactive
  choices. If a workboard item cannot pass with the current project state and
  safe backend tools, explicitly consume or transform it: skip the claimed todo
  item, defer/mark the signal with a concise reason, revise the affected card
  back to a planned repair state when a clear non-interactive repair exists, or
  create a new planned follow-up card. Do not leave the auto turn waiting for
  user confirmation.
```

中文语义：

- auto turn 不等待用户授权、不向用户抛 "请确认后我再继续"。
- 如果当前工具返回 `pending_approvals` / `rejected_approvals` /
  `manual_preparation_required` / `partial_resolution_*` /
  `fallback_available_*` / `unsupported_source_spec` / `runtime_missing`
  等需要人工选择或外部准备的状态，Manager 不应继续重试原请求。
- 对 `todo` fuel：先 claim，再用 `skip_workboard_item` 显式退出 fuel，
  message/summary 里记录原因；不要用 `complete_workboard_item`，因为
  Doc 42 明确 `todo` 的唯一显式出口是 `skip`。
- 对 `complete_signal` / `block_signal` fuel：可用 `defer_workboard_item`
  或 `block_workboard_item_for_user` 显式消费 signal，并写明缺少的人工
  前置；如果有清晰的非交互修复，则用 `revise_card_plan` 把卡片改回
  `planned` 修复态或创建新的 planned follow-up card。
- 对 run-control 返回的 approval 状态：如果工具已经把 card/run 退回
  `planned` 或 `needs_approval`，Manager 不应轮询或等待授权；应记录
  原因并退出本 turn，让 auto episode 通过下一次 fuel evaluation 决定
  是否还有可处理工作。

这不要求 Manager 隐瞒阻塞。它可以在最终回复或 workboard item message
里简短记录 "由于需要用户授权 / 手动准备 runtime，已跳过该自动项"，
但不能把下一步动作设为等待用户。

**与 Doc 42 的关系**

这条规则与 `docs/42_oaa2_terminal_completion_summary_wake.md` 不冲突：

- Doc 42 规定 `todo` 只能 `skip`，或在 submit/start 后因 card 离开
  `planned` 自动退出；J-auto 要求 auto 下无法推进的 `todo` 用
  `skip_workboard_item`，正好符合该规则。
- Doc 42 规定 `complete_signal` / `block_signal` 必须由 Manager 显式
  acknowledge / skip / convert 后才消费；J-auto 要求 defer/block/convert
  而不是仅仅 "看过并等待用户"，符合 signal consumption 语义。
- Doc 42 允许 Manager 在 `complete` turn 中创建新 fuel 或调整 card，
  使 episode 回到 `pending_wake` / `running`；J-auto 的 "创建 planned
  follow-up card" 与此一致。
- Doc 42 的 start validation boundary 仍保持不变：workboard 不负责判断
  卡片能否真的启动。J-auto 只约束 Manager 在 start / install / resolver
  已经返回人工阻塞后如何收束 auto turn。

测试：

- auto wake 中 `install_runtime_dependencies` 返回
  `manual_preparation_required`：Manager 不再次调用 install、不询问用户
  授权；对关联 todo 执行 `skip_workboard_item` 或对 signal 执行
  `block_workboard_item_for_user`，并写入原因。
- auto wake 中 `start_card_run` 返回 `pending_approvals`：Manager 不调用
  poll/continue approval，不请求用户等待；记录原因并消费当前 workboard
  fuel。
- 非 auto 普通 user turn 保留原行为：可以向用户解释 blocked subset 并
  询问是否手动准备 runtime / 批准 fallback / 缩窄 package 列表。

### script_preference 传导链（补强偏好落地路径）

**K1. `configure_card_execution` 增加 `instruction_blocks` 参数**

`configure_card_execution` 当前只在 manager-agent schema 里声明参数，
后端的 `ConfigureCardExecutionPayload` 用了 `extra="forbid"`
（`manager_blueprint_tools.py:73`）——任何未在 payload 模型里显式声明
的字段都会被 `model_validate` 拒绝并抛 `ValidationError`，服务层也就
走不到写卡路径。因此 K1 必须同时改三处，缺一处都无效：

1. **manager-agent schema**
   位置：`manager-agent/src/server.js:1822-1835`。在 parameters 里追加：

   ```js
   instruction_blocks: Type.Optional(
     Type.Array(Type.String(), {
       description: "Free-form planning hints the executor should read. "
         + "Use this to persist selected_context.script_preference into the "
         + "card. Keep entries short and non-binding.",
     }),
   ),
   ```

2. **后端 payload 模型**
   位置：`backend/app/services/manager_blueprint_tools.py:72-79`
   （`ConfigureCardExecutionPayload`）。新增字段：

   ```python
   instruction_blocks: list[str] | None = None
   ```

   不设 `Field(...)` 限制——短字符串列表即可；长度防御放在 service 层
   （见下）而不是 schema 层，避免错误地触发 4xx 响应。

3. **服务写路径**
   位置：`backend/app/services/manager_blueprint_tools.py:879-891`
   （`configure_card_execution` 的 per-card 上下文拼装循环）。在现有
   `skills` / `mcp_servers` / `runtime_bindings` 写入之后追加：

   ```python
   if request.instruction_blocks is not None:
       new_blocks = [str(b).strip() for b in request.instruction_blocks if str(b).strip()]
       merged = list(context.instruction_blocks or [])
       for block in new_blocks:
           if block not in merged:
               merged.append(block)
       context.instruction_blocks = merged
   ```

   要点：
   - **append + dedupe，不覆盖**：`_default_executor_context` 已经写入
     的默认 instruction 不应被 configure 工具清掉；K3 也会在后面追加
     script-preference block。
   - **不去重前保留原顺序**：让 Manager 后续读取 `card.executor_context.
     instruction_blocks` 时，写入顺序 = 追加顺序。
   - **不校验内容语义**：instruction_blocks 是 soft planning hint，不是
     执行契约；service 层只做 strip + 去重，不做策略校验。

完成后 Manager 可在 `create_card` / `revise_card_plan` 之后显式调用
`configure_card_execution` 把 `script_preference` 等偏好写到
`card.executor_context.instruction_blocks`，K3 / K6 的 worker 侧读取
才有内容可读。

**K2. `ExecutorContext` 模型增加 `script_preference` 字段**
位置：`backend/app/models/executor.py`。

```python
script_preference: str | None = None
```

只用于持久化槽位，不进入 executor 硬逻辑；`worker_service` 在读它时
拼出一条 instruction block 即可。

**K3. `worker_service._default_executor_context` 读偏好**
位置：`backend/app/services/worker_service.py:1934-1937`。

现状：`_default_executor_context(graph, card, ...)` 接收的是 `graph`
而非 `project`（见 `worker_service.py:1915-1922`），所以草稿里
`project.runtime_preferences.script_preference` 的写法在当前签名下
取不到值。

实际读取路径：`project_service.update_project_runtime_preferences` 已经
把 `runtime_preferences.model_dump()` 持久化到
`graph.metadata["runtime_preferences"]`
（`project_service.py:515`），因此 worker 侧不需要绕回 project 对象，
直接从 graph metadata 读即可：

```python
runtime_prefs = graph.metadata.get("runtime_preferences") or {}
script_pref = (
    getattr(getattr(card, "executor_context", None), "script_preference", None)
    or runtime_prefs.get("script_preference")
)
if script_pref and script_pref != "auto":
    instruction_blocks.append(
        self._script_preference_block(script_pref)
    )
```

说明：

- **优先级**：`card.executor_context.script_preference`（K1/K2 写入的
  卡级偏好）> `graph.metadata.runtime_preferences.script_preference`
  （项目级偏好）> 默认不追加。
- **旧图兼容**：没有 `runtime_preferences` 键的老 graph 走 `{}` 回退，
  `script_pref` 为 None，不追加 instruction block。
- **依赖显式化**：数据源是 `graph.metadata`，与 `_default_executor_context`
  现有参数 `graph.metadata.get("default_conda_env")` / `"default_r_env"`
  同一渠道；不引入新的隐藏依赖。
- **不在 `_default_executor_context` 里调用 `project_service`**：避免
  把 worker 侧拉回 project 锁路径，也避免在 hot path 上额外读一次
  project state。

`_script_preference_block` 复用 `scriptPreferenceGuidance` 同一套文案
（"Soft script preference: prefer R/Python scripts when practical. This
is not a hard constraint; use the other when more reliable."）。

**K4. system prompt 加偏好落卡指令**
位置：`manager-agent/src/server.js:196-197` 之后追加。

```text
- When creating or revising an analysis card and selected_context.script_preference
  is prefer_python, prefer_r, or prefer_mixed, persist it into
  card.executor_context by calling configure_card_execution with the matching
  instruction_blocks entry. Do not rely on chat-side acknowledgment alone;
  the executor does not read chat history.
```

**K5. `install_runtime_dependencies` ecosystem 加偏好默认 hint**
位置：`manager-agent/src/server.js:1863`。

把 `description: "python or R"` 改为：

```text
"python or R. When selected_context.script_preference is prefer_r, default
ecosystem to R unless the task clearly needs a Python-only package; mirror
for prefer_python. Do not invent a third ecosystem value."
```

**K6. wake turn 确认 userEnvelope 重发偏好**
位置：`manager-agent/src/server.js:3113-3127`。

wake turn 走的也是同一个 `userEnvelope` 构建，已经带
`script_preference_guidance` 和 `runtime_preference_guidance`，无需改动；
只需要在 K4 加完 prompt 后 smoke 验证 wake turn 同样把偏好落卡。

### 校验层（辅助，不替代 prompt）

**G. `validate_card` 加上界 warning（独立 channel，不混入 errors）**
位置：`backend/app/services/asset_timeline_service.py:161-165`。

现状：`validate_card()` 返回 `(Card, list[str])`，而调用方
`manager_blueprint_tools.py:635` / `:730` 把非空列表一律当致命错误
抛 `CardWriteValidationError`。如果只在同一个 `list[str]` 里 append
warning 文案，调用方会把它当成 error 阻断写入——和 "不阻断写入" 的
设计意图矛盾。

需要的显式契约：

1. **改签名**为三元组 `(Card, errors: list[str], warnings: list[str])`。
   第二个位置参数保持 `errors` 语义，第三个是新增的 `warnings`。
   （也可以用 `NamedTuple("ValidateResult", [...])` 或 dataclass，但
   三元组最小改动，与现有 `errors = ...` 赋值对齐。）
2. **所有调用点**（`manager_blueprint_tools.py:635`、`:730` 以及
   `grep validate_card` 命中的其余位置）同步改为
   `card, errors, warnings = ...`，仅 `errors` 触发 raise。
3. **警告进成功响应**：`create_card` / `revise_card_plan` 成功 payload
   新增 `step_alignment_warnings: list[str]` 字段（仅当非空时出现），
   模型可在下一步决策时读到对齐建议。不放进 `retry_hint`——retry hint
   语义是"这次失败后该怎么重试"，而 warning 是"这次成功但有改进空间"，
   两者不混。
4. **`_append_output_role_errors` 等下游辅助函数**的返回值也要区分
   errors / warnings，避免把 role 冲突这类真错误误放进 warnings。

建议追加的 warning 生成逻辑：

```python
warnings: list[str] = []
if candidate.step is not None and candidate.step > min_step:
    same_layer_siblings = [
        other.title for other in candidate_cards
        if other.card_id != candidate.card_id
        and (other.step or 1) == min_step
    ]
    if same_layer_siblings:
        warnings.append(
            f"Card {candidate.card_id} is step {candidate.step} but its "
            f"inputs only require step {min_step}. {len(same_layer_siblings)} "
            f"sibling card(s) are already at step {min_step}; prefer aligning "
            f"to the same parallel layer unless a dependency reason exists."
        )
```

测试：

- `backend/tests/test_asset_timeline_service.py`：`step > min_step` 且
  有同层 sibling 时，`errors` 为空、`warnings` 非空、card 仍成功写入。
- `backend/tests/test_manager_blueprint_tools.py`：create_card 在
  warnings 非空时不抛 `CardWriteValidationError`，且响应 payload 含
  `step_alignment_warnings`。

**H. `create_card` / `revise_card_plan` 响应带回 `parallel_group`**
位置：`backend/app/services/manager_blueprint_tools.py`，成功 payload 增加：

```python
"parallel_group": f"step_{candidate.step or 1}",
"step_alignment_hint": {
    "min_step": min_step,
    "same_layer_siblings": sibling_ids,
},
```

模型在下一步决策时就能读到 "这张卡应该和 card_X 同 step" 的显式提示。

**I. `_recommended_step` 增加同层参考**
位置：`manager_blueprint_tools.py:2677-2687`。返回 `(min_step, siblings_at_min_step)`，
并在 `create_card` 失败或成功时通过 retry hint / payload 透出。

### 测试

- `backend/tests/test_auto_episode_flow.py`：补一个 case 验证同层两张无依赖卡
  建出来 `step` 相等。
- `backend/tests/test_asset_timeline_service.py`：补一个 case 验证
  `validate_card` 在 `step > min_step` 且存在同层 sibling 时返回 warning
  但不阻断。
- Manager 端 smoke：给 OAA-2 同类蓝图下发 "建 3 张独立 QC 卡 + 1 张汇总卡"，
  期望模型返回 step=[1,1,1,2]，不再是 [1,2,3,4]。
- 后台并行 smoke：在一个 turn 里同时下发
  `install_runtime_dependencies`（修某张卡缺的 R 包）+ `start_card_run`
  （另一张已 ready 的卡），期望模型一次性完成两件后台工作而不是分两
  个 turn。验证 J 段解除单任务歧义后后台任务不再串行化。
- script_preference 传导 smoke：项目选 `prefer_r`，下发 "建一张差异表达
  卡并准备依赖"。验证：
  1. Manager 在 create_card 后调用 `configure_card_execution` 把
     `prefer_r` 写进 `card.executor_context.instruction_blocks`；
  2. `install_runtime_dependencies` 的 `ecosystem` 默认填 `R`；
  3. `worker_service` 构造的 task packet 里 executor prompt 包含
     "prefer R scripts when practical" 文本。
- auto 授权阻塞 smoke：auto wake 中让依赖解析或 run approval 返回
  人工阻塞状态，验证 Manager 不询问用户等待授权、不轮询 approval，而是
  skip/defer/block/convert 当前 workboard fuel 并记录原因；普通非 auto
  turn 仍允许询问用户下一步。

## 边界与不做什么

- **不改变 `AssetTimelineService.parallel_batches` 的算法语义**：拓扑排序
  + 按层切分的逻辑是正确的，不重写。P-1 只做一件事：在现有 batch dict
  里补一个 `step`（= `batch_index + 1`）或显式 `parallel_group: "step_N"`
  字段，让 `_parallel_group_for_card()` 拿到非空值。这是输出 schema
  扩展，不是算法改动。
- **不把 step 从 Optional 改 Required**：保留模型省略 step 让后端取
  `min_step` 的路径；只是当模型显式传 step 时必须对齐。
- **不引入"并行层管理器"新运行时组件**：不新增独立服务、队列或状态
  存储。改动集中在**现有 prompt、manager-agent 工具控制
  （`callLoggedTool` async-boundary guard）、后端 payload 模型与
  service 写路径（`ConfigureCardExecutionPayload` / `configure_card_
  execution` / `parallel_batches` 输出字段）、校验 warning 通道、
  以及 worker 默认 `ExecutorContext` 拼装**。每一项都落在既有模块里，
  不引入新的部署单元或 IPC 边界。
- **不改变 UI 渲染逻辑**：UI 已经按 `card.step` 分列，模型把同层卡对齐
  后视觉效果自然正确。
- **不与 doc 38/39/40/41 冲突**：
  - doc 38（`/auto` 命令传达）：本 doc 只动 prompt 与校验 warning，
    不动 ManagerCommandService 或命令解析。
  - doc 39（auto wake loop）：本 doc 在 wake prompt 加了一行
    parallel_group 提示，不改动 wake 触发 / fingerprint / chain budget
    任何一项。
  - doc 40（逻辑资产 vs 物化绑定）：本 doc 不动 input resolution 链，
    `min_step` 计算用的 `asset.step` 仍然来自 `AssetTimelineService`，
    与 doc 40 的 binding 分层正交。
  - doc 41（dependency resolver 上报）：本 doc 不动 resolver 或
    attention 派生，`parallel_group` 由已有 `parallel_batches` 提供。
- **与 doc 32/36/37 的同步修订策略**：
  - doc 32（unified supervisor）/ doc 37（claim/wake/stop）的 "one action
    batch per turn" / "one workboard decision cycle per turn" **核心语义**
    保持不变：一个 batch = 至多一次 async boundary yield。但本 doc J 段
    把 system prompt / wake prompt 里 "one workboard item or one run
    batch" 的歧义表述改写成以 async boundary 为单位的 action batch，
    同时 J-tool 放宽了 `callLoggedTool` 的 async-boundary guard 以允许
    install + run 同 turn。**实施时需要反过去复核 doc 32 / doc 37**：
    若其中有 "单 turn 单 tool 调用" / "一个 turn 只允许一次工具调用"
    等更严格措辞，必须同步修订；若已是 "one batch per turn" 口径，
    只需追加一句澄清 install 与 run 可同 turn 共存。
  - doc 36（workboard prompt）只描述 decision cycle，不涉及 async
    boundary 工具控制，复核即可；若发现 doc 36 里有与 J-tool 冲突的
    单动作假设再行修订。
- **K 系列 script_preference 边界**：
  - **不把偏好改成硬约束**：`scriptPreferenceGuidance` 的 "not a hard
    constraint" 措辞保留；R 在 DESeq2 / edgeR / limma 等任务上确实比
    Python 更成熟，硬约束会反过来坑用户。
  - **不动 reviewer prompt**：`REVIEWER_SYSTEM_PROMPT` 只审 contract
    一致性，不关心 Python vs R 选择，保持中立。
  - **不动前端**：frontend 已经正确存 `script_preference` 到
    `ProjectRuntimePreferences` 并通过 `selected_context` 下发。

## 实施顺序建议

0. **P-1 后端前置：让 `parallel_group` 真正有值**
   - `backend/app/services/asset_timeline_service.py:303`：
     `parallel_batches` 把 `step = batch_index + 1` 写进每个 batch dict
     （或显式加 `parallel_group: "step_N"` 字段）。
   - 回归：`test_asset_timeline_service.py` 现有 parallel_batches case 的
     断言同步更新；`background_workboard_service._parallel_group_for_card`
     能返回非空 `"step_N"`。
   - 这是 A / B / C / F / J prompt 文案里所有 `parallel_group = "step_N"`
     引用的前置条件；不做的话 prompt 在描述一个不存在的信号。

1. **P-1 工具控制：放松 async-boundary guard**（J-tool）
   - `manager-agent/src/server.js:1440-1454`：按 J-tool 描述实现
     `installBeforeRun` 状态、放行非 yielding 工具与 install + run 同 turn。
   - 测试（`manager-agent/test/`）：install + run / 重复 run / install +
     submit_claimed / install + run + run / 中断 stop_card_run 五个 case。
   - 交叉修订 doc 32 / doc 37 里 "one action batch per turn" 的措辞，
     与新 guard 语义对齐。
   - 这是 J 段 prompt 落地的前置条件；只改 prompt 不改 guard，模型按 prompt
     执行会在第二步被硬拒。

2. **P0 prompt 三件套**：A / B / C（manager-agent `server.js`）。
   改完 `node --check src/server.js`。
3. **P0 planner 两处**：D / E（`manager_planner.py`）。跑现有
   auto-episode / fuel-buffer 测试看有没有回归。
4. **P0 wake + system prompt 单任务歧义**：F / J
   （`manager_auto_service.py` + `manager-agent/src/server.js:172`）。
   这两条是解除后台任务串行化的核心。
5. **P0 auto 授权阻塞收束**：J-auto
   - `manager-agent/src/server.js:185-186`：对 dependency blocked /
     resolver blocked 判断增加 auto-mode 例外，auto 下不询问用户授权。
   - `manager-agent/src/server.js:3309`：auto episode instruction 增加
     "无法通过则 skip/defer/block/convert，不等待用户确认"。
   - `backend/app/services/manager_auto_service.py:563`：wake prompt 同步
     增加该规则。
   - 测试：auto 下人工阻塞会消费/转换 fuel；非 auto 下仍可询问用户。
6. **P0 script_preference 传导**：K1 / K4 / K5
   （`configure_card_execution` 加字段、system prompt 加落卡指令、
   `install_runtime_dependencies.ecosystem` 加偏好 hint）。
7. **P1 script_preference 持久化槽**：K2 / K3 / K6
   （`ExecutorContext.script_preference`、`worker_service` 从
   `graph.metadata["runtime_preferences"]` 读偏好自动 append instruction
   block、wake turn smoke 验证）。
8. **P1 校验 warning**：G / H / I。把 `validate_card` 签名改为三元组
   `(Card, errors, warnings)`，更新 `manager_blueprint_tools.py:635` /
   `:730` 及其他调用点，在成功响应里新增 `step_alignment_warnings`
   字段。补 `test_asset_timeline_service.py` 和
   `test_manager_blueprint_tools.py` 两个 case。
9. **P2 OAA-2 实测验证**：用 OAA-2 同类蓝图下发规划指令，确认模型
   输出 step=[1,1,...,1,2,...] 而不是严格递增；同时下发 install + run
   验证后台不再串行化；在 `prefer_r` 项目下验证偏好落卡 + ecosystem
   默认 R。

## Acceptance

- Manager system prompt 明确写出 "step 是并行层、不是序号"。
- Manager system prompt 的 "at most one" 改成以 async boundary 为
  单位的 action batch 表述，并显式允许 install + run 同 turn。
- `create_card` / `revise_card_plan` 工具描述显式要求对齐同层。
- Legacy planner + harness prompt 不再压制同层多卡。
- Wake prompt 提到 `parallel_group`，且与 system prompt 的单任务口径一致。
- `validate_card` 对同层散落返回 warning（非 error）。
- OAA-2 同类场景下，模型一次建 3+ 张无依赖卡时 `step` 相等。
- 后台并行 smoke：模型在一个 turn 里同时发起 install + run 两件独立
  后台工作，不再串行化。
- Auto 模式下遇到需要用户授权、runtime 手动准备、fallback 批准或脚本
  绑定的阻塞时，不询问用户等待确认；Manager 显式 skip/defer/block/
  convert 当前 fuel，必要时创建 planned follow-up card，并记录原因。
- `configure_card_execution` 接受 `instruction_blocks` 参数，Manager
  显式调用它把 `script_preference` 写进 `card.executor_context`。
- `ExecutorContext.script_preference` 字段存在，`worker_service` 据此
  自动 append 一条 "prefer R/Python when practical" instruction block。
- `install_runtime_dependencies.ecosystem` 描述包含偏好默认 hint。
- `prefer_r` 项目下跑完整 smoke：卡片落卡 / 依赖安装 / executor prompt
  三处都能看到 R 偏好生效。
