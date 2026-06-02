# Step 并行分组 Prompt 契约审阅

Status: design review.

Date: 2026-06-02

## Summary

OAA-2 及近期多步规划场景暴露了一个 Manager 行为问题：模型在批量建卡时
几乎不做并行分组，习惯给每张卡片分配严格递增的 `step`（1 / 2 / 3 / 4），
而不是把无依赖关系、同属一层的卡片放到同一个 `step`（1 / 1 / 1 / 2）。

后端实际上已经算好了并行分组信号：

```text
AssetTimelineService.parallel_batches(...)
    -> background_workboard_service: ready_to_start.payload.parallel_group = "step_N"
    -> background_workboard_service: ready_to_start.payload.safe_to_batch_start = true
```

但 Manager 侧的 system prompt、工具描述、planner prompt 都没有告诉模型
"step 是并行层，不是串行序号"。校验器也只卡下界（`step < min_step`），
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

7. **Wake prompt** (`backend/app/services/manager_wake_processor.py:229-248`)
   - 只说 "Consume at most one actionable workboard item or one claimed run
     batch in this turn."
   - 对 frontier 规划没有 "对齐 parallel_group" 的激励。

### 校验侧

8. **`AssetTimelineService.validate_card`**
   (`backend/app/services/asset_timeline_service.py:116-170`)
   - Line 159-165: 只校验 `candidate.step < min_step`（下界），
     不校验 `candidate.step > min_step` 的上界散落。
   - 模型给 step=5 而 min_step=1，校验通过。

9. **`_recommended_step`**
   (`backend/app/services/manager_blueprint_tools.py:2677-2687`)
   - 只返回下界 `min_step = max(asset.step + 1 for each input)`，
     没有 "同层对齐" 逻辑，也没有返回 "当前同层已有 N 张卡" 这类参考。

10. **create_card 成功返回**：只返回 `{ ok, card_id, asset_ids, ... }`，
    不回传 `parallel_group` 或 `step_alignment_hint`，模型建完卡也不知道
    自己的 step 是不是和同层其他卡对齐了。

### 行为后果

- 同层卡片被拆成多个 step → UI 时间轴出现假的串行化。
- workboard 的 `parallel_group` 信号和卡上的 `step` 字段语义分裂：
  workboard 说这两张卡可并行（`step_1`），但卡上 step 分别是 1 和 2。
- auto 模式下 `submit_claimed_workboard_items` 按 workboard batch 提交，
  实际跑起来确实是并行的；但用户从蓝图上看到的是串行序号，认知负担高。
- 模型在 revise_card_plan 修卡时也不会主动 "拉齐" 同层卡片的 step。

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

**F. Wake prompt 增加 batch 对齐信号**
位置：`backend/app/services/manager_wake_processor.py:246`。

原文：

```text
Call get_background_workboard first. Consume at most one actionable
workboard item or one claimed run batch in this turn.
```

改为：

```text
Call get_background_workboard first. Consume at most one actionable
workboard item or one claimed run batch in this turn. When planning new
cards from a frontier wake, align new cards to the parallel_group of
existing ready_to_start items that share their input layer.
```

### 校验层（辅助，不替代 prompt）

**G. `validate_card` 加上界 warning**
位置：`backend/app/services/asset_timeline_service.py:161-165`。
不阻断写入，但在成功路径上返回 `warnings` 字段，模型可通过 retry hint 看到。

建议追加：

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

- `backend/tests/test_manager_flow.py`：补一个 case 验证同层两张无依赖卡
  建出来 `step` 相等。
- `backend/tests/test_asset_timeline_service.py`：补一个 case 验证
  `validate_card` 在 `step > min_step` 且存在同层 sibling 时返回 warning
  但不阻断。
- Manager 端 smoke：给 OAA-2 同类蓝图下发 "建 3 张独立 QC 卡 + 1 张汇总卡"，
  期望模型返回 step=[1,1,1,2]，不再是 [1,2,3,4]。

## 边界与不做什么

- **不动 `AssetTimelineService.parallel_batches` 本身**：算法正确，
  只需让 prompt 与它对齐。
- **不把 step 从 Optional 改 Required**：保留模型省略 step 让后端取
  `min_step` 的路径；只是当模型显式传 step 时必须对齐。
- **不引入"并行层管理器"新服务**：所有改动都在 prompt 层和现有校验
  warning 层，不新增运行时组件。
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

## 实施顺序建议

1. **P0 prompt 三件套**：A / B / C（manager-agent `server.js`）。
   改完 `node --check src/server.js`。
2. **P0 planner 两处**：D / E（`manager_planner.py`）。跑现有
   `tests/test_manager_flow.py` 看有没有回归。
3. **P0 wake prompt**：F（`manager_wake_processor.py`）。
4. **P1 校验 warning**：G / H / I。补 `test_asset_timeline_service.py`
   和 `test_manager_flow.py` 两个 case。
5. **P1 OAA-2 实测验证**：用 OAA-2 同类蓝图下发规划指令，确认模型
   输出 step=[1,1,...,1,2,...] 而不是严格递增。

## Acceptance

- Manager system prompt 明确写出 "step 是并行层、不是序号"。
- `create_card` / `revise_card_plan` 工具描述显式要求对齐同层。
- Legacy planner + harness prompt 不再压制同层多卡。
- Wake prompt 提到 `parallel_group`。
- `validate_card` 对同层散落返回 warning（非 error）。
- OAA-2 同类场景下，模型一次建 3+ 张无依赖卡时 `step` 相等。
