# 45. Auto Dependency Failure Signal Consumption Review

## 背景

当前 auto 模式下，Manager 可以在 workboard 驱动的回合中为某个 card/run 发起后台依赖安装任务：

1. card 因缺失 Python/R 依赖失败；
2. workboard 派生出 `runtime_dependency_missing` 的 manager 处理项；
3. Manager 调用 `install_runtime_dependencies`，随后该 turn 以 async boundary 结束；
4. 后台依赖安装任务在稍后发出 terminal 事件，触发 auto 再次 evaluate。

本 review 只关注一个问题：

> 如果依赖安装失败，系统是否会把这个失败信号正确消费掉，而不是让 auto 反复 wake 在同一个 failed dependency item 上？

这里不要求“自动重跑原 card”。只要求“不在 fail 上卡住”。

## 当前实现行为

### 成功路径

- 依赖安装 job 成功后，`runtime_dependency_job_service` 会发 terminal project event，并调用 `notify_background_task_terminal(...)`。
- `ManagerAutoService` 会据此再次 evaluate workboard。
- `FlowService.get_work_order(...)` 允许状态为 `planned`、`failed`、`stale`、`superseded` 的 card 在 blocker 消失后重新进入 `can_start=true`。
- 因此，如果原 card 之前只是被 runtime dependency blocker 挡住，那么安装成功后它会重新出现在 `ready_to_start`。

结论：

- 当前系统具备“安装成功后，failed card 再次变为可启动”的能力。
- 但这不等于“必须自动重跑”。是否真的再提交该 card，取决于后续 auto turn 的 Manager 决策。

### 失败路径

- 依赖安装 job 失败后，workboard 会派生 `runtime_dependency_install_failed` 项。
- 该项位于 `needs_manager` lane，会作为 manager-actionable fuel 参与 wake。
- auto 被唤醒后，Manager 可以：
  - `get_runtime_dependency_install_status`
  - `complete_workboard_item`
  - `defer_workboard_item`
  - `block_workboard_item_for_user`
  - 或者创建其它 repair/follow-up 动作

结论：

- 当前架构已经把“依赖安装失败”建模成可被 Manager 消费的 workboard 信号。
- 但它不会自动消失，必须被 Manager 显式消费。

## 现有风险

### 风险 1：Manager 只汇报失败，不消费 workboard item

如果 Manager 在 wake turn 中只是输出一段文字，例如：

- “edgeR 安装失败，请用户检查环境”

但没有调用：

- `complete_workboard_item`
- `defer_workboard_item`
- `block_workboard_item_for_user`

那么 `runtime_dependency_install_failed` 仍然留在 `needs_manager` fuel 中。

结果是：

- 下一次 evaluate 仍会看到相同 fuel；
- auto 可能再次 wake 到同一个 failed dependency item；
- 最终靠 wake storm / chain limit / loop flush 一类保护机制停下。

这不属于“永久死锁”，但属于“失败信号没有被正确消费”。

### 风险 2：loop flush 只是防无限循环，不是正确业务闭环

如果系统完全依赖 loop flush/wake storm/chain limit 来终止这种场景，那么行为上会变成：

1. auto 被失败依赖信号反复唤醒；
2. Manager 重复看到同一个 failed item；
3. 若模型没有主动消费该 item，最终被保护机制截断。

这有两个问题：

- 用户看到的是“auto 停了”或“似乎没推进”，而不是一个明确的 `blocked_for_user` 处理项；
- workboard 里没有留下清晰的终态说明，不利于之后人工恢复。

### 风险 3：失败信号未消费时，后续继续 auto 仍可能再次命中同一问题

如果 failed dependency item 没被 `done/deferred/blocked_for_user`，之后用户再次继续 auto，系统仍可能重新面对同一个 manager-actionable item。

这会造成：

- 同一错误反复被解释；
- 用户无法从 workboard 视角明确知道“这里已经需要人工处理”。

## 评估结论

当前逻辑的结论应分成两部分：

### 1. 是否会自动重跑原 card

不保证。

这点可以接受，因为本问题的目标不是自动 rerun。

### 2. 是否保证不会卡在 failed dependency signal 上

目前不完全保证。

更准确地说：

- 从系统保护角度看，不太会无限卡死；
- 从 workboard 语义闭环看，如果 Manager 不显式消费 `runtime_dependency_install_failed`，就仍然存在“反复 wake 同一失败项，直到被 loop/chain/wake 保护截断”的风险。

## 建议的目标行为

如果产品目标只是：

> “依赖安装失败后，不自动重跑，但也不要卡住 auto”

那么最小闭环应该是：

### 成功

- 允许只展示 receipt；
- 不要求自动 rerun 原 card；
- 若该 card 重新变为 `ready_to_start`，Manager 可继续，也可不继续。

### 失败

- Manager 必须显式消费 `runtime_dependency_install_failed`；
- 推荐动作是 `block_workboard_item_for_user`；
- block message 应至少包含：
  - runtime
  - packages
  - error_code
  - retry_hint 或 stderr 摘要

### running

- Manager 不应 foreground 轮询；
- 等 terminal wake 即可。

## 推荐修复方向

本问题不需要优先改后端状态机，先补 prompt / tool-consumption contract 就够了。

建议新增一条硬规则：

> 在 auto 模式中，若当前 manager-actionable item 的 kind 是 `runtime_dependency_install_failed`，Manager 不得只用文字汇报；必须显式调用 `complete_workboard_item`、`defer_workboard_item` 或 `block_workboard_item_for_user` 之一来消费该信号。

推荐优先级：

1. `block_workboard_item_for_user`
2. `defer_workboard_item`
3. `complete_workboard_item`

其中：

- `block_workboard_item_for_user` 最符合“需要用户查看环境问题”的语义；
- `complete_workboard_item` 只适合“失败已被记录，后续不再追这个信号”的场景；
- `defer_workboard_item` 适合明确还会有其它非交互修复动作跟进的情况。

## 测试建议

至少补以下行为测试：

1. auto 模式下依赖安装失败后，若 Manager 仅返回文本、不消费 failed item，系统是否会重复 wake，直到 protection stop。
2. auto 模式下依赖安装失败后，若 Manager 调用 `block_workboard_item_for_user`，该 failed item 不再继续作为 actionable fuel。
3. auto 模式下依赖安装成功后，原 `failed` card 在 blocker 消失后重新进入 `ready_to_start`。
4. 用户之后重新继续 auto 时，已被 block/done 的 dependency failed item 不会再次成为同一个 wake fuel。

## 最终判断

当前系统：

- 成功路径基本成立；
- 失败路径具备建模能力，但缺少“Manager 必须消费失败信号”的强约束。

所以现状更接近：

> “不会轻易无限卡死，但可能把失败项留在 workboard 里反复 wake，最后靠 loop flush / wake storm / chain limit 截断。”

如果目标是更干净的行为闭环，应该把“消费失败依赖信号”升级为 auto prompt 的明确强规则，而不是依赖兜底保护。
