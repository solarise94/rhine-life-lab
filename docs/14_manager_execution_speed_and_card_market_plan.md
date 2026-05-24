# Manager 执行提速 / Thinking 修复 / Run Control / Card Market 执行方案

## 目标

本方案规划四项连续改造：

1. 把 pi CLI executor wrapper 默认模型切到 `deepseek-v4-flash`，把 reviewer 也切到 flash，提高执行与复核速度。
2. 修复 Manager chat 中 thinking 计时错误，避免第一个 thinking block 吞掉后续时长。
3. 给 Manager 增加直接启动、停止、重跑 card 的 tool，形成从“改图”到“执行”的闭环。
4. 引入 Manager-only 的 Card Market / Template Library，让 Manager 能保存稳定 card 模板并在后续项目中复用。

本轮是设计与实施方案，不包含代码落地。

## 设计原则

- Manager、executor、reviewer 的职责要继续分开，不做“一个模型包打天下”。
- project memory 只保存偏好和纠错，不保存可执行工作流模板。
- 模板库是“方法复用层”，蓝图和 card 仍然是“项目事实层”。
- 所有会改变执行状态或模板库内容的动作，都应由 Manager tool call 驱动，不做用户直接操作入口。
- 先修模型和时序基础，再做复用能力，避免把错误行为沉淀成模板。

## 当前基线

当前实现中：

- `scripts/blueprint_pi_launch.sh` 的 executor wrapper 直接使用 `BLUEPRINT_MANAGER_MODEL`，默认 `deepseek-v4-pro`。
- `backend/app/services/executor_reviewer_worker.py` 复用同一套 DeepSeek 配置，没有 reviewer 专用模型位。
- `ManagerChatPanel.tsx` 已有 thinking/tool/compact timeline，但 thinking item 的生命周期仍可能被 heartbeat 或后续事件复用。
- backend 已有 `start_run`、`cancel_run`、`rerun`、`review_run` 等能力，但 Manager sidecar 暂未暴露对应 tool。
- 已有 `project_memory`，但没有“可复用 card 模板库”。

## 总体实施顺序

按以下顺序实施：

1. 模型位拆分
2. thinking 生命周期修复
3. reviewer 可观测性与 prompt 收敛优化
4. Manager run control tools
5. Card interaction order and Manager runtime/dependency cognition
6. Card Market / Template Library

原因：

- `3` 要先补监控，否则 reviewer 失败时只能看到聚合报错，无法精确定位。
- `4` 依赖清晰的执行模型与权限策略。
- `6` 依赖稳定的 card 运行与复核流程，否则模板质量不可控。

---

## 一、模型配置拆分与提速方案

### 目标

把“规划模型”和“执行/复核模型”拆开：

- Manager 继续使用 `deepseek-v4-pro`
- executor wrapper 默认使用 `deepseek-v4-flash`
- reviewer 默认使用 `deepseek-v4-flash`

### 不建议的做法

不要直接把 `BLUEPRINT_MANAGER_MODEL` 全局改成 flash。那会同时影响：

- Manager 规划质量
- Manager tool use 稳定性
- card 修改与模板选择判断

这会把“执行提速”变成“整体能力降级”。

### 建议新增配置位

```bash
BLUEPRINT_MANAGER_MODEL=deepseek-v4-pro
BLUEPRINT_EXECUTOR_MODEL=deepseek-v4-flash
BLUEPRINT_REVIEWER_MODEL=deepseek-v4-flash
```

建议保留同一套 key/base URL，不额外拆 provider：

```bash
BLUEPRINT_DEEPSEEK_API_KEY=...
BLUEPRINT_DEEPSEEK_API_BASE_URL=...
BLUEPRINT_PI_DEEPSEEK_BASE_URL=...
```

### 影响范围

#### Executor Wrapper

文件：

- `scripts/blueprint_pi_launch.sh`
- `backend/app/workers/pi_worker.py`

调整目标：

- wrapper 不再读 `BLUEPRINT_MANAGER_MODEL`
- 改为优先读 `BLUEPRINT_EXECUTOR_MODEL`
- 若未配置，再 fallback 到 `BLUEPRINT_MANAGER_MODEL`

#### Reviewer

文件：

- `backend/app/services/executor_reviewer_worker.py`
- `backend/app/services/executor_validation_service.py`

调整目标：

- reviewer 不再隐式复用 manager model
- 增加 `BLUEPRINT_REVIEWER_MODEL`
- reviewer 的 max turns / max tokens 可以比 manager 更保守

### 可选增强

- `BLUEPRINT_REVIEWER_MAX_TURNS`
- `BLUEPRINT_REVIEWER_MAX_TOKENS`
- `BLUEPRINT_EXECUTOR_THINKING_EFFORT`
- `BLUEPRINT_REVIEWER_THINKING_EFFORT`

### 验收标准

- Manager 仍显示使用 `deepseek-v4-pro`
- executor wrapper 日志显示使用 `deepseek-v4-flash`
- reviewer payload / summary 显示使用 `deepseek-v4-flash`
- 不影响现有 run 启动、review 与验收流程

---

## 二、Thinking 计时修复方案

### 问题定义

当前 thinking 展示存在 item 生命周期混淆：

- 第一个 thinking block 结束后，时长仍继续累计
- 后续 thinking 内容可能被写回前一个 item
- heartbeat 或 synthetic thinking 占位可能污染真实 thinking block

### 根因判断

高概率不是单纯的格式问题，而是“timeline item identity 不稳定”：

- `thinking_start` / `thinking_end` 没有严格绑定同一个 item key
- heartbeat fallback 在已有 thinking item 时继续更新错误对象
- 计时逻辑可能按“消息级”算，而不是“timeline item 级”算

### 目标状态

每个 thinking block 都应成为独立 timeline item：

- 有独立 `id`
- 有独立 `started_at`
- 有独立 `ended_at`
- 结束后不可被后续 block 复用

### 建议状态模型

thinking item 的唯一键：

```text
assistant_turn_index + content_index
```

状态迁移：

1. `thinking_start`
2. `thinking_delta` x N
3. `thinking_end`

严格规则：

- `thinking_start` 创建 item，并记录 `started_at`
- `thinking_delta` 只能命中这个 item
- `thinking_end` 写入 `ended_at`
- `ended_at` 一旦写入，该 item 只读
- heartbeat 只能更新“明确处于 running 的 thinking item”
- synthetic thinking 不得和真实 thinking item 复用 id

### 前端展示规则

运行中：

```text
思考中 xx 分 xx 秒
```

已完成：

```text
已思考 xx 分 xx 秒
```

时间来源：

- running: `now - started_at`
- done: `ended_at - started_at`

刷新后：

- 不再依赖 `Date.now()` 重算历史结束时长
- 直接使用持久化的 `started_at` / `ended_at`

### 影响范围

文件：

- `manager-agent/src/server.js`
- `frontend/components/manager-chat/ManagerChatPanel.tsx`
- 如需要：`backend/app/models/chat.py`

### 验收标准

- 一轮中多个 thinking block 时长互不串扰
- 第一块结束后时长冻结
- heartbeat-only 场景不会制造重复 thinking item
- 刷新页面后历史时长保持稳定

---

## 三、Reviewer 可观测性与 Prompt 收敛优化

### 目标

解决当前 reviewer 失败时只能看到聚合错误，无法回答“这 8 次 tool call 分别为什么失败”的问题，并同时降低 reviewer 卡在 8 turn 上限的概率。

### 当前问题

现在的 reviewer worker：

- 会在内存中收集每轮 `assistant_content`
- 但不会把逐轮 transcript、tool_use 输入、tool_result 输出、protocol error 落盘
- 超过 `MAX_REVIEW_TURNS` 后，只返回一个总括错误

因此当前只能看到：

- `reviewer_protocol_not_satisfied`
- `reviewer_worker_max_turns`

但看不到：

- 哪几轮没有 tool call
- 哪几轮调用了错误工具
- 哪几轮调用了 `submit_executor_review` 但 schema 不合法
- 哪些 `tool_result` 明确给了 protocol error

### 方案 A：Reviewer Turn Trace 落盘

#### 目标

为每次 reviewer 执行生成结构化 trace，支持事后复盘。

#### 建议新增产物

```text
runs/<run_id>/reviewer_trace.json
runs/<run_id>/reviewer_trace.jsonl
```

推荐优先做 `jsonl`，便于流式追加和大文件截断。

#### 每轮建议记录字段

- `turn_index`
- `request_model`
- `assistant_content`
- `tool_uses`
- `tool_results`
- `protocol_errors`
- `final_review_candidate`
- `accepted_final_review`
- `timestamp`

#### 每个 tool_use 建议记录

- `tool_use_id`
- `name`
- `input`
- `validated`
- `validation_error`

#### 每个 tool_result 建议记录

- `tool_use_id`
- `ok`
- `content`
- `error`

### 方案 B：Manager Brief 摘要增强

不建议把完整 trace 塞进 `manager_brief.json`，太大。

建议只追加摘要字段：

- `reviewer.turns`
- `reviewer.tool_calls_total`
- `reviewer.submit_attempts`
- `reviewer.submit_schema_failures`
- `reviewer.missing_tool_call_turns`
- `reviewer.last_protocol_error_code`

这样 UI 和 manager 汇报能快速读到问题，但详细 trace 仍去单独文件看。

### 方案 C：Frontend / Advanced View 可选接入

这不是本轮必须项，但建议预留：

- 在 `Advanced` 或 run detail 中增加 reviewer trace 文件入口
- 至少允许下载或预览 `reviewer_trace.jsonl`

### Prompt / Tool Contract 优化

#### 当前问题类型

reviewer 的失败很可能不是“不会看文件”，而是“不会收尾”：

- 一直做 inspection，不提交 final review
- 提交了 `submit_executor_review`，但字段不齐
- 在 protocol error 后仍重复错误行为

#### 改造目标

让 reviewer 更像一个有限状态机，而不是开放式 agent。

#### 建议优化点 1：System Prompt 更强约束

当前 prompt 应加强为：

- 最多允许少量 inspection，再必须结束
- 如果证据已经足够，立即 `submit_executor_review`
- 若收到 protocol error，下一轮优先修正该错误，不再重复无关 inspection
- 不要在同一轮提交多个互相冲突的 final verdict

可以明确加上：

```text
You must finish by calling submit_executor_review.
Do not continue exploring once you already have enough evidence.
If the previous tool_result reports a protocol_error, your next tool call must correct that protocol error.
Prefer one final submit_executor_review call over multiple competing final submissions.
```

#### 建议优化点 2：给 reviewer 明确“最短成功路径”

在初始上下文里告诉 reviewer：

1. 先 `list_review_files`
2. 选择少量关键文件检查
3. 一旦能判断，就提交 `submit_executor_review`

不要让它自己无限发散探索。

#### 建议优化点 3：把 final schema 要求前置为 checklist

在 prompt 中明确列出 final submit 必填项：

- `verdict`
- `summary`
- `issues`
- `repair_hints`
- `inspected_files`

并说明：

- `issues` 可为空数组
- `repair_hints` 可为空数组
- `inspected_files` 必须是实际检查过的文件路径

#### 建议优化点 4：Protocol Error 回灌要更显式

当前 protocol error 虽然会回给 reviewer，但可以更结构化：

- 明确写出上一轮失败的字段名
- 明确告诉它“下一轮禁止调用别的 inspection tool，先修 final submit”

### 非 prompt 改造：状态机保护

如果想进一步提高稳定性，可以在 worker 侧加一个软保护：

- 当 reviewer 已经调用过 `submit_executor_review` 且只差 schema 修正时
- 下一轮若没有再次调用 `submit_executor_review`
- 直接回更强的 protocol error，要求它立即修 final submit

这比纯靠 prompt 更稳。

### 影响范围

文件：

- `backend/app/services/executor_reviewer_worker.py`
- `backend/app/services/executor_validation_service.py`
- 可选：`backend/app/services/manifest_service.py`
- 可选：frontend advanced / run detail 入口

### 验收标准

- reviewer 失败时，可在 run 目录看到逐轮 `reviewer_trace`
- 能定位每一轮 tool_use 是否缺失、schema 是否失败
- 再次出现类似 TF run 问题时，可以准确回答“8 次分别卡在哪”
- prompt 调整后，`reviewer_protocol_not_satisfied` 频率明显下降

---

## 四、Manager 直接运行 / 停止 Card 的 Tool 方案

### 目标

让 Manager 不再只是“改 blueprint 的 planner”，而是能闭环执行：

- 启动 card
- 停止运行中的 card
- 重跑 card
- 必要时触发 review

### 当前能力复用

backend 已有能力：

- `start_run`
- `cancel_run`
- `rerun`
- `review_run`
- `configure_card_execution`

因此不需要新造执行引擎，只需要包装为 Manager tools。

### 建议新增工具

#### 1. `start_card_run`

用途：

- 启动指定 card

入参建议：

- `card_id`
- `worker_type?`
- `python_runtime?`
- `r_runtime?`

返回建议：

- `run_id`
- `status`
- `worker_type`
- `pending_approvals`
- `rejected_approvals`
- `block_reasons`
- `can_start`

#### 2. `stop_card_run`

用途：

- 停止运行中的 card 或 run

入参建议：

- `run_id?`
- `card_id?`
- `reason?`

约束：

- 至少提供 `run_id` 或 `card_id`
- 若只给 `card_id`，backend 需要先解析其当前 active run

#### 3. `rerun_card`

用途：

- 重新执行某个 card

入参建议：

- `card_id`
- `worker_type?`
- `python_runtime?`
- `r_runtime?`

#### 4. 可选 `review_card_run`

用途：

- Manager 对结果做 accept / reject

### Tool 使用规则

Manager 的执行策略应写进 system prompt：

- 如果 card 因权限缺失无法运行，先调用 `configure_card_execution`
- 如果 run 会卡在 prompt approval，不要直接启动
- 如果 work-order 表示当前不能启动，应先解释 blocker，而不是盲目 start
- 如果已有 active run，不要重复启动第二个 run

### 推荐执行链

1. `get_project_context` / `list_data_assets`
2. 如有权限缺口：`configure_card_execution`
3. `start_card_run`
4. 如需中止：`stop_card_run`
5. 如需重跑：`rerun_card`

### 影响范围

文件：

- `backend/app/api/manager_tools.py`
- `backend/app/services/manager_blueprint_tools.py`
- `backend/app/services/worker_service.py`
- `manager-agent/src/server.js`

### 风险点

- card 级别查 active run 的逻辑要一致，避免停止错 run
- run control tool 不能绕过 work-order gate
- runtime approval 相关返回结构要标准化，不然 Manager 很难稳定决策

### 验收标准

- Manager 可直接启动一个满足条件的 planned card
- Manager 可在权限不足时先改权限再启动
- Manager 可停止一个 running run
- Manager 不会在 blocked / approval-required 场景进入死循环

---

## 五、Card Interaction Order And Manager Runtime Cognition

### 目标

补两类交互和认知问题：

1. 卡片详情 / 展开层的显示顺序应按用户点击顺序，而不是固定从左到右。
2. Manager 必须明确知道 card 子代理运行在受限环境中，不能主动安装 R / Python 包。

### A. Card 显示顺序按点击顺序

#### 当前问题

现在 card 区域如果按画布从左到右的 DOM / layout 顺序决定层级，用户点击右侧 card 后，再点击左侧 card，左侧内容可能覆盖右侧内容。

实际使用中，用户可能想：

- 先看某张 card
- 再点另一张 card 对比
- 然后把刚看过的信息写给 Manager

如果层级按左到右固定排序，后点击的上下文可能被前面的左侧 card 覆盖，用户需要重新找信息。

#### 目标行为

显示层级按“最近点击顺序”决定：

- 最近点击的 card 永远在最上层
- 第二近点击的 card 在下一层
- 不再由 card 在画布中的横向位置决定覆盖关系

#### 建议状态模型

前端维护一个 per-project 的 `cardInteractionOrder`：

```ts
cardInteractionOrderByProject: Record<string, string[]>
```

规则：

- 点击 card 时，把 `card_id` 移到数组末尾
- 渲染 z-index 时，数组越靠后 z-index 越高
- 关闭或取消选择 card 时，可以保留顺序，也可以移除，建议保留最近访问历史
- session / project 切换时按 project 独立持久化

#### 影响范围

文件：

- `frontend/lib/stores/workspace-ui-store.ts`
- `frontend/components/cards/CardStream.tsx`
- `frontend/components/cards/CardDetailPanel.tsx`
- 可选：`frontend/app/globals.css`

#### 验收标准

- 连续点击多张 card 时，最后点击的 card 不会被左侧 card 覆盖
- 刷新页面后，如果 UI 状态持久化，最近点击顺序可恢复
- 移动端不引入额外遮挡问题

### B. Manager 对受限运行环境和依赖修复的认知

#### 当前问题

Manager / card 子代理容易把缺包问题当作“子代理自己可以安装”的问题处理，例如：

- 安装 R 包
- 安装 Python 包
- 下载系统依赖
- 修改全局 runtime

但实际执行环境是受限的：

- card 子代理不能主动向用户询问权限
- card 子代理不应自行安装缺失的 R / Python 包
- 依赖缺失应报告给 Manager，由 Manager 决定调整 card、切 runtime、配置权限、尝试安装到选定 runtime，或让用户处理环境

#### 简化后的依赖处理策略

现阶段不引入完整环境解决器，也不自动创建新环境。采用最小可行策略：

- executor 仍然不能安装包，只能通过 `report_dependency_issue.py` 报告缺失依赖。
- Manager 读取 dependency issue 后，如果包名明确且用户已选择非系统 Python/R runtime，可以调用 `install_runtime_dependencies`。
- Python 支持 `pip`，可选 `conda`；R 支持 `bioconductor`，可选 `cran`。
- 禁止安装到 `__system__` runtime，避免污染系统环境。
- 只接受明确包名或简单 Python 版本约束，不接受任意 shell 命令。
- 安装失败、系统工具缺失、环境不存在、包名不明确时，Manager 直接告诉用户需要手动准备哪个 runtime dependency。

这个方案的边界很清楚：它是“Manager 代用户在已选环境里装几个明确包”的 tool，不是 SAT solver、conda-lock、renv、micromamba 环境编排器。

#### Prompt 认知更新

Manager system prompt 应明确：

```text
Card executor agents run in a constrained runtime. They must not install missing R or Python packages on their own. If runtime packages are missing and a specific non-system runtime is selected, Manager may use install_runtime_dependencies with explicit package names. If installation fails or the missing dependency is a system tool, tell the user exactly what dependency must be prepared.
```

建议同时写入 executor prompt / task packet guidance：

```text
Do not install missing runtime packages. If required packages/tools are unavailable, use report_dependency_issue.py and stop.
```

这条目前 task packet 里已有类似约束，但 Manager prompt 也需要同步，避免 Manager 继续规划“让子代理自己装包”的方案。

#### Manager 行为规则

Manager 应遵守：

- 不要告诉用户“card agent 会自己安装依赖”
- 不要在 card instruction 里要求 executor 安装包
- 如果缺 R / Python 包，优先查可用 runtime、换实现方式、调整 card 的 `executor_context`。
- 如果包名明确且已有选定非系统 runtime，使用 `install_runtime_dependencies` 尝试安装。
- 如果 `install_runtime_dependencies` 返回失败，或缺的是系统工具/外部数据库/复杂环境编译问题，明确告诉用户需要准备哪个 runtime dependency。
- 如果网络下载数据库是分析必要条件，使用 `configure_card_execution` 设置 network/tool policy，而不是让 card agent 临场询问用户

#### Tool 设计

Backend internal API：

```text
POST /internal/manager-tools/projects/{project_id}/runtime-dependencies/install
```

Manager sidecar tool：

```json
{
  "name": "install_runtime_dependencies",
  "parameters": {
    "ecosystem": "python | R",
    "runtime": "selected non-system runtime",
    "packages": ["scanpy", "GSVA"],
    "manager": "pip | conda | bioconductor | cran",
    "timeout_seconds": 600
  }
}
```

返回结构应包含：

- `ok`
- `ecosystem`
- `runtime`
- `resolved_runtime`
- `packages`
- `manager`
- `returncode`
- `stdout_tail`
- `stderr_tail`
- `message`

Manager 只根据结构化结果继续决策，不解析完整安装日志。

#### 影响范围

文件：

- `manager-agent/src/server.js`
- `backend/app/api/manager_tools.py`
- `backend/app/services/manager_blueprint_tools.py`
- `backend/app/services/worker_service.py`
- `backend/app/services/manager_planner.py`
- `backend/app/workers/pi_agent_executor.py`
- 相关 executor prompt / reviewer prompt 文档

#### 验收标准

- Manager 不再建议 card 子代理自行安装 R / Python 包
- 缺包时 card 输出 dependency issue，而不是尝试安装
- Manager 可在明确缺包、选中非系统 runtime 时尝试安装依赖
- Manager 能解释“运行环境受限，需要配置 runtime、安装失败后手动预装依赖，或更换实现方式”

---

## 六、Card Market / Template Library 方案

### 目标

建立一个 Manager-only 的 card 模板库，用于沉淀“已经调试稳定的执行方法”，并让 Manager 在后续项目中复用。

这不是用户直接操作的“市场 UI”，而是一个受控的复用层。

### 设计边界

用户不能直接：

- 保存模板
- 查询模板
- 套用模板

这些动作全部由 Manager tool call 触发。

原因：

- 模板质量需要由 Manager 根据上下文判断
- 模板复用需要映射当前项目的输入、输出、step、runtime
- 用户直接点“套模板”容易绕开蓝图一致性检查

### 模板与 project memory 的边界

#### Project Memory

用途：

- 用户明确要求记住的长期偏好
- 纠错规则
- 绘图风格
- 默认报告表达偏好

不适合保存：

- 可执行 card 结构
- prompt 片段
- runtime 配置
- 依赖文件关系

#### Card Template

用途：

- 稳定可复用的方法
- 脱敏后的执行结构
- runtime / tool policy / prompt 模板

### 建议数据模型

#### `CardTemplate`

模板元信息：

- `template_id`
- `title`
- `summary`
- `tags`
- `domain`
- `card_type`
- `source_card_type`
- `created_at`
- `updated_at`
- `last_verified_at`
- `reuse_count`
- `confidence_score`
- `status`

#### `TemplateSpec`

模板核心结构：

- `card_title_pattern`
- `summary_template`
- `why_template`
- `inputs_schema`
- `outputs_schema`
- `executor_context`
- `tool_policy`
- `runtime_bindings`
- `instruction_blocks`
- `prompt_blocks`
- `expected_artifacts`
- `success_signals`
- `failure_signals`

#### `TemplateBundle`

关联文件和依赖：

- `files`
- `relative_dependency_graph`
- `path_rewrites`
- `parameter_bindings`
- `script_asset_requirements`
- `script_asset_bindings`

脚本资产可以随模板保存，但必须分两层处理：

- `script_asset_requirements` 描述模板需要哪些脚本角色，例如 `main_analysis_script`、`plotting_helpers`、`reference_database_downloader`。
- `script_asset_bindings` 只在实例化后的项目 card 上保存，绑定当前项目里的真实 asset/file，不写入可复用模板本体。

这样模板能保留文件依赖关系和运行结构，但不会把某个项目的真实脚本 asset_id 固化进模板库。

### 模板脱敏规则

必须移除：

- 真实项目名
- 样本名
- 真实 asset_id
- 绝对路径
- 用户原始聊天文本
- 私有数据内容

可以保留：

- 文件结构
- 依赖关系
- runtime 选择
- tool policy
- prompt 模板
- 输入输出契约
- 成功运行模式

### 建议存储方式

先做本地模板库，不做外部市场：

```text
workspace/_card_templates/
  templates.json
  bundles/
    <template_id>/
      spec.json
      files/
```

不要和 `workspace/<project_id>/memory/project_memory.json` 混放。

### 建议新增 Manager Tools

#### 1. `search_card_templates`

用途：

- 根据任务语义、输入类型、输出需求查模板

入参建议：

- `query`
- `tags?`
- `card_type?`
- `limit?`

返回：

- 匹配模板
- 适用条件
- 信心分

#### 2. `save_card_template`

用途：

- 从一个稳定 card + 最近成功 run 生成模板

入参建议：

- `card_id`
- `title?`
- `summary?`
- `tags?`

触发条件建议：

- card 至少 `accepted` 或最近 run 已 reviewer pass
- Manager 主动判断“这张卡值得复用”

#### 3. `instantiate_card_template`

用途：

- 用模板创建新的 card

入参建议：

- `template_id`
- `card_id`
- `title?`
- `step`
- `input_bindings`
- `output_bindings`
- `script_asset_bindings?`
- `runtime_overrides?`

约束：

- 如果模板声明了 `script_asset_requirements`，Manager 在调用此 tool 前必须先在对话中询问用户要绑定哪些项目脚本资产。
- 用户确认后，Manager 才能把确认后的 `script_asset_bindings` 传给 tool。
- 如果用户没有确认脚本资产，tool 应允许创建未绑定脚本的 planned card，但 card 必须带有清晰的 missing binding 标记，不能直接运行。
- 询问应聚焦在“绑定哪个已有脚本资产/是否暂不绑定”，不要把权限审批、包安装或运行时交互推给 card 子代理。

### Manager 侧行为规则

推荐写进 system prompt：

- 创建新分析 card 前，如任务明显可复用，先查模板库
- 命中高质量模板时，优先实例化模板，再小幅修改
- 仅在 card 已经稳定且值得复用时保存模板
- 不要把项目事实写进模板
- 不要把模板当作项目记忆
- 当模板需要脚本资产时，先向用户确认脚本绑定关系，再实例化或运行 card
- 不要让 card 子代理自己询问用户选择脚本，脚本绑定必须由 Manager 在组装阶段完成

### 与蓝图系统的关系

模板库不是直接执行对象。

真正的执行链仍然是：

1. Manager 查模板
2. Manager 检查模板是否需要脚本资产绑定
3. 如需要，Manager 在对话中询问用户并确认绑定关系
4. Manager 实例化为 card
5. card 进入 blueprint
6. card 再走 run / review / accept 流程

### 风险点

- 模板过早保存会把实验性错误固化
- 脱敏不彻底会泄漏项目特征
- 文件模板与输入输出契约如果不同步，会造成“模板看起来能用，运行时却断裂”
- 脚本资产如果在模板保存时绑定真实 asset_id，会把项目私有结构泄漏到模板库
- Manager 如果跳过确认直接绑定脚本，容易把旧项目脚本误用于新项目

### 最低质量门槛

建议只有满足以下条件才允许保存模板：

- card 状态稳定
- 最近 run 成功
- reviewer 未报 fail
- 关键输入输出关系明确
- executor_context 不是临时拼凑状态

### 验收标准

- Manager 能搜索模板
- Manager 能从稳定 card 保存模板
- Manager 能实例化模板生成新 card
- 生成的新 card 仍通过现有蓝图校验与 step / asset 校验

---

## 分阶段实施计划

### Phase 1: 模型位拆分

输出：

- manager / executor / reviewer 三套模型位
- executor 与 reviewer 默认 flash

验收：

- 保持现有 run 流程可用

### Phase 2: Thinking 生命周期修复

输出：

- thinking item identity 稳定
- 计时从消息级改为 item 级

验收：

- 多 thinking block 时长独立

### Phase 3: Reviewer Observability And Prompt Hardening

输出：

- `reviewer_trace.jsonl`
- reviewer protocol failure 摘要字段
- reviewer prompt / protocol error 收敛优化

验收：

- reviewer 失败可精确定位到逐轮原因

### Phase 4: Manager Run Control

输出：

- `start_card_run`
- `stop_card_run`
- `rerun_card`
- 可选 `review_card_run`

验收：

- Manager 可直接发起和停止 card 运行

### Phase 5: Card Interaction Order And Runtime Cognition

输出：

- card z-index / 展开层按点击顺序排序
- Manager prompt 明确 card 子代理不能主动安装 R/Python 包

验收：

- 最近点击 card 始终在最上层
- Manager 不再规划“让子代理自己装包”

### Phase 6: Card Market

输出：

- 本地模板库存储
- `search_card_templates`
- `save_card_template`
- `instantiate_card_template`
- 模板可声明脚本资产需求
- Manager 实例化模板前确认脚本资产绑定

验收：

- Manager 能复用稳定模板创建新 card
- 需要脚本资产的模板不会静默绑定旧项目 asset_id
- 未确认脚本绑定的 card 保持 planned / missing binding 状态，不会直接运行

---

## 不在本轮范围内

以下内容不建议和本轮一起做：

- 用户可视化操作模板市场 UI
- 多人共享模板仓库
- 模板评分与推荐系统
- 在线发布/订阅模板
- 模板版本审批流

这些都建立在本地 Manager-only 模板库跑顺之后再说。

## 最终建议

这五项里，最先该做的是模型位拆分、thinking 修复和 reviewer 可观测性。

原因：

- 模型位拆分会立刻降低执行和 reviewer 延迟。
- thinking 修复会让后续调试 run control 和模板复用时，timeline 更可信。
- reviewer trace 会让后续所有 reviewer 失败都可诊断，而不是只看到聚合报错。
- run control 完成后，Manager 才算真正具备“项目执行器”能力。
- card 点击层级和 Manager runtime 认知属于体验与执行安全底座，应在模板库之前稳定。
- 模板库应建立在稳定执行行为之上，而不是相反。
