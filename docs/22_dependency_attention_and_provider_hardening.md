# Executor Provider Hardening and Dependency Attention Notes

本轮修改把执行器配置链路从“尽量 fallback”收紧为“配置不一致就明确报错”，同时明确了后续卡片依赖中断的处理策略：只对高风险资产依赖破坏做 `stale`，普通叙述字段修改不传播。

## 已完成的执行器配置收紧

### Provider 配置模型

API Settings 从单个 DeepSeek 配置拆成 provider registry：

- 用户可以新建 provider，填写显示名称、协议、模型名、Base URL 和 API key。
- provider 协议当前支持 `anthropic_compatible` 和 `openai_compatible`。
- Manager、Reviewer、Pi executor、OpenCode executor 等 role 通过 provider binding 选择已保存 provider。
- API key 不回传前端，只暴露 `api_key_configured` 状态。
- provider model test 成功后，UI 使用绿色状态点表示该模型当前可用。

### Role 与协议兼容

后端保存设置时会做严格校验：

- provider list 不能为空。
- role binding 不能引用不存在的 provider。
- role binding 必须满足当前后端支持的协议集合。

当前保守策略：

- `manager`: `anthropic_compatible`
- `reviewer`: `anthropic_compatible`
- `pi_executor`: `anthropic_compatible`
- `opencode_executor`: `anthropic_compatible`
- `library_summarizer`: `anthropic_compatible`

这样避免 Reviewer/Pi/OpenCode 在运行时拿到不支持的 provider 后又悄悄退回旧 DeepSeek 配置或空 key。

### 默认执行器与 profile

执行器选择也改为显式失败：

- 默认执行器未配置时，运行直接报错。
- 不再从 `pi -> opencode -> codex -> claude_code` 横向扫描 fallback。
- `profile_id` 会从 start-run/rerun API、前端 hook、Manager internal tool payload 一路传到 backend。
- `executor_profile` 保持 worker 名语义，例如 `pi_worker`、`opencode_worker`。
- 真正选择的 profile id 放在 `executor_profile_id`。
- 本次显式传入 `profile_id` 时，会覆盖卡片旧 `executor_context.executor_profile_id`，避免 UI 已切换 profile 但后端仍使用旧 profile。

### Pi key 前置校验

Pi executor 启动前会检查项目 API key：

- 优先使用 role binding 注入的 `pi_api_key`。
- 再兼容旧的 `deepseek_api_key`。
- 两者都不存在时直接抛出清晰错误，提示用户配置 Anthropic-compatible provider 并绑定到 `pi_executor`。

这样可以避免 Pi wrapper 启动后才在 shell 或 CLI 内部失败。

### Manager sidecar provider 透传

Manager sidecar 接收 backend 传入的：

- `selected_provider_id`
- `provider_protocol`
- `model`
- provider base URL
- API key

sidecar 会先尝试 pi-ai registry；registry miss 时，根据协议构造 Anthropic-compatible 或 OpenAI-compatible model 对象。当前 UI 和 backend role 绑定仍按 Anthropic-compatible 保守开放。

## OpenCode 与项目 API 注入

当前 UI 策略是让 OpenCode project_api 使用 Anthropic-compatible provider。

OpenCode renderer 会：

- 解析 provider model/base URL/key。
- 缺 model/base URL/key 时提前返回 unsupported error。
- 生成 run-scoped OpenCode config。
- 把 skill/MCP/tool policy 写入 run-scoped capability config。

`cli_native` 模式仍使用本机 CLI 登录态，不注入项目 API key。

## Dependency Attention 策略

### 设计目标

卡片链路里，中间卡片发生高风险依赖变更时，需要提醒用户和 Manager：下游结果可能不再可信，需要检查是否重跑。

但不是所有上游修改都应该打断下游。例如只改实验叙述、解释文本、下一步建议时，不应该把整条数据分析链标成 stale。

因此依赖处理收敛为：

- 普通文本字段修改不传播。
- 只有高风险资产依赖破坏才传播。
- 传播结果先做明确、可解释的 stale/attention 标记，不做复杂版本语义判断。

### 当前依赖判断基础

现有系统主要按资产依赖判断，而不是字段级 diff。

核心依赖字段：

- `card.inputs[].asset_id`
- `card.outputs[].asset_id`
- `card.linked_assets`
- `module.depends_on_assets`
- `asset.depends_on`
- `claim.depends_on_assets`

已有基础包括：

- `WorkerService._rebind_downstream_inputs()` 会在上游重跑并产生替换资产时更新下游输入，并把 accepted/rejected 下游标成 `stale`。
- `PatchApplyService.mark_downstream_stale` 已支持按 `asset_ids` 标记下游资产/claim stale。

缺口是：Manager 或 patch 修改 accepted card 的高风险字段后，还没有统一的依赖影响传播服务。

### 高风险 stale 触发条件

建议只在以下场景触发 downstream stale：

- accepted card 的 `outputs[].asset_id` 被删除。
- accepted card 的 `outputs[].asset_id` 被替换。
- accepted card 的 `linked_assets` 移除了已被下游引用的资产。
- asset 被标记为 `stale`、`superseded`、`rejected`、`archived` 或 `missing`，且有下游 card 输入依赖它。
- card 被 `cancelled`、`rejected` 或 `superseded`，且它曾产生被下游使用的资产。

不触发传播的低风险字段：

- `title`
- `summary`
- `why`
- `manager_review`
- `next_actions`
- `key_findings`
- `progress_note`
- 纯 UI 或说明性字段

这些修改可以写审计记录，但不应该自动影响下游。

### 状态语义

短期不新增复杂版本系统。

推荐语义：

- `stale`: 后端已经判定下游依赖被高风险破坏，结果需要重新检查或重跑。
- `progress_note`: 写明 stale 原因，例如“上游输出资产 asset_x 已被替换，下游依赖需要复核。”
- Manager prompt: 要求 Manager 在破坏依赖链前优先创建新分支。

如果后续需要更细粒度体验，可以再加 `needs_attention` overlay，但本轮不作为必要实现。

## Manager Prompt 策略

Manager 应被明确约束不要轻易改断已接受的分析链路。

建议加入系统提示或工具说明：

```text
Do not mutate accepted upstream outputs that downstream cards depend on unless the user explicitly asks to revise that dependency chain.
If a new analysis direction or corrected upstream result is needed, prefer creating a new card/output branch with new asset_ids instead of rewriting or removing existing accepted outputs.
Before changing inputs, outputs, or linked_assets of an accepted card, inspect downstream dependencies and explain the impact.
When a change would invalidate downstream cards, either create a parallel branch or explicitly mark downstream cards stale with a reason.
```

中文语义：

- 不要轻易改掉已有 accepted 主链路。
- 如果是新增分析方向，优先创建新 card / 新 output branch。
- 如果确实要修改上游输出，先检查下游依赖，并说明影响。
- 如果修改会让下游结果不可信，要显式标记下游 stale，而不是静默修改。

## 后续落地建议

### 1. 提取 DependencyImpactService

集中处理依赖影响，不要把逻辑散在 patch apply、Manager tool、worker review 里。

建议入口：

- `ManagerBlueprintTools.update_card`
- `PatchApplyService.update_card`
- `PatchApplyService.set_card_status`
- `PatchApplyService.set_asset_status`
- `WorkerService._rebind_downstream_inputs`
- semantic rollback / cleanup 相关路径

### 2. 对比修改前后高风险字段

服务输入：

- `previous_card`
- `updated_card`
- `graph.assets`
- `graph.claims`
- `graph.modules`
- `cards`

输出：

- affected asset ids
- affected downstream card ids
- affected claims/modules/assets
- human-readable reason

### 3. 传播 stale

传播规则：

- 引用受影响资产的 accepted/rejected downstream card 标记为 `stale`。
- 引用受影响资产的 valid asset/claim 标记为 `stale`。
- module 依赖受影响资产且已 accepted/rejected 时标记为 `stale`。
- running/reviewing card 不强制中断，只记录 warning 或 progress note，避免破坏正在运行的 executor。

### 4. UI 显示

短期 UI 可以继续使用 `Stale` badge。

建议额外显示：

- stale 原因
- 来源 card / asset
- “Ask Manager to inspect”
- “Rerun downstream”

如果后续加入 `needs_attention` overlay，UI 再把它显示为 `Need Attention`，但不急于扩展状态枚举。

### 5. 测试覆盖

需要补测试：

- 修改 accepted card 的 `summary` 不影响下游。
- 删除 accepted card 的 output asset id，会把下游 accepted card 标成 `stale`。
- 替换 accepted card 的 output asset id，会把下游 accepted card 标成 `stale`。
- 新增 output asset 不影响已有下游。
- 移除被下游引用的 `linked_assets` 会传播 stale。
- running/reviewing downstream 不被强制改状态。
- Manager tool update 和 patch apply 两条路径行为一致。

## 当前验证记录

本轮相关验证：

- `backend.tests.test_executor_profiles`: 41 tests OK
- `backend.tests.test_manager_flow`: 120 tests OK
- `cd frontend && npm run build`: OK
- `cd manager-agent && node --check src/server.js`: OK
- `git diff --check`: OK

`test_manager_flow` 中出现的 reviewer 401 日志来自测试用假 key 覆盖 reviewer 基础设施失败路径，不代表本轮修改失败。
