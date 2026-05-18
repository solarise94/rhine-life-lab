# 05. 容易出错节点 Review 清单

本文件用于每次让 AI 编程助手实现或修改代码后进行自检。

建议在每个 Pull Request / vibe coding 阶段结束后，让 AI 根据本清单逐项 review。

---

## 1. 产品边界 Review

### 1.1 用户是否直接编辑蓝图？

必须满足：

- 用户通过 Manager AI 提需求
- Manager AI 生成 proposal / patch
- 后端校验 patch
- 后端应用 patch

不允许：

- 用户直接编辑 graph.json
- 用户直接拖拽连线修改后端
- 用户直接修改 asset_id / edge
- 用户直接写 YAML 字段

Review Prompt：

```text
请检查本次改动是否让普通用户绕过 Manager AI 直接编辑蓝图或 Graph IR。
如果有，请改为通过 Manager AI proposal + PatchValidator。
```

---

## 2. UI Review

### 2.1 Card 是否过度暴露后端？

Card 默认不应显示：

- raw graph id
- raw manifest json
- artifact hash
- storage_uri
- graph patch ops
- Git command

Review Prompt：

```text
请检查 Card 默认展示内容是否保持用户友好。
技术字段必须放入折叠的 Technical Details 或 Advanced 页面。
```

### 2.2 Graph 是否成为主编辑界面？

Graph 默认应该是解释层，不是主编辑层。

Review Prompt：

```text
请检查 Graph View 是否只读或辅助展示。
不要让用户在默认界面中直接编辑 Graph。
```

### 2.3 状态是否清楚？

状态必须统一：

```text
proposed
planned
running
needs_review
accepted
rejected
stale
superseded
cancelled
failed
```

如果需要展示 ModuleGroup 的汇总状态，使用派生字段 `aggregate_status`；不要把 `completed`、`has_failed`、`partially_planned` 等展示/汇总状态写入 Card 的 `status`。

Review Prompt：

```text
请检查前端状态 badge 是否只使用统一枚举。
不要出现未定义状态或前后端状态不一致。
```

---

## 3. Patch Review

### 3.1 Patch 是否 allowlist？

所有 op 必须来自 allowlist。

Review Prompt：

```text
请检查 PatchValidator 是否拒绝 unknown op。
请检查所有 op 都有 schema 校验。
```

### 3.2 是否保护 valid asset？

禁止普通 patch：

- 删除 valid asset
- 覆盖 artifact hash
- 修改 created_by_run
- 移除 run history
- 让 accepted result 直接消失

Review Prompt：

```text
请检查 patch 是否可能破坏 valid asset 或历史记录。
如果需要回退，必须生成 semantic rollback patch，而不是删除历史。
```

### 3.3 是否检测依赖关系错误？

必须检查：

- missing node
- orphan edge
- cycle
- downstream stale propagation

Review Prompt：

```text
请检查依赖关系校验。
新增 connect_dependency 时必须确认 from/to 存在，并避免循环依赖。
```

---

## 4. Git Review

### 4.1 Accepted change 是否 commit？

任何 accepted patch / run 必须 Git commit。

Review Prompt：

```text
请检查 accepted proposal、accepted run、rollback 是否都会生成 Git commit。
```

### 4.2 是否避免 reset 丢历史？

产品级回退不应该默认 `git reset --hard`。

Review Prompt：

```text
请检查 rollback 是否作为一次新的 semantic rollback commit 记录，而不是直接丢弃历史。
```

### 4.3 Commit 是否包含完整文件？

Commit 应包含：

- graph/
- cards/modules/assets
- runs/run_xxx/
- manager_review
- manifest
- patch
- scripts/configs if changed
- artifact pointer if any

Review Prompt：

```text
请检查 GitService commit 的文件范围是否完整。
避免 graph 更新了但 run/manifest 未提交。
```

---

## 5. Worker Review

### 5.1 Worker 是否越权写文件？

Worker 只能写 allowed_paths。

实现层面优先使用执行器原生 sandbox / permission / approval 机制；兼容层把 TaskPacket policy 翻译成各执行器的 cwd、writable roots、approval mode、环境变量和提示词。

建议权限模式：

```text
audit   宽松执行，执行后扫描越界写入。
guarded 使用执行器原生权限机制，仍保留执行后审计。
strict  隔离 workspace / 容器 / 更强 sandbox，用于高风险任务。
```

环境变量只能作为策略提示，不能作为唯一安全边界。

Review Prompt：

```text
请检查 Worker 输出路径是否被校验。
请检查 WorkerAdapter 是否使用执行器原生权限机制，而不是只靠 prompt/env。
任何写出 allowed_paths 的结果都应 warning、失败或进入 quarantine。
```

### 5.2 Worker 是否直接改 Graph？

Worker 不得直接修改：

- graph.json
- cards.json
- assets.json
- claims.json

除非是明确允许的临时输出，并且后端不会直接信任。

Review Prompt：

```text
请检查 Worker 是否有机会直接修改 Graph IR。
Worker 只能输出 manifest 和结果文件。
```

### 5.3 Worker 是否必须输出 manifest？

无 manifest 不可 accepted。

Review Prompt：

```text
请检查 run 完成后是否要求 manifest.json。
缺失 manifest 必须标记 failed。
```

---

## 6. Manifest Review

### 6.1 Manifest 文件声明是否真实？

必须检查：

- created_assets.path 存在
- commands.log 存在或有 commands_executed
- metrics 格式正确
- status 合法

Review Prompt：

```text
请检查 ManifestService 是否验证所有声明的输出文件真实存在。
```

### 6.2 Manifest 路径是否安全？

必须检查：

- 不允许 `../`
- 不允许写项目外
- 不允许覆盖 valid asset
- 不允许写 .git

Review Prompt：

```text
请检查 manifest path 安全性，防止路径穿越和覆盖关键文件。
```

---

## 7. Artifact Review

### 7.1 大文件是否进入 Git？

禁止普通 Git 管理：

- h5ad
- bam
- cram
- fastq
- fq
- large tsv
- large html
- large intermediate dir

Review Prompt：

```text
请检查 .gitignore 和 GitService，确保大文件不会被 git add。
大文件必须通过 artifact pointer。
```

### 7.2 Accepted artifact 是否有 hash？

accepted artifact 必须有 full sha256。

Review Prompt：

```text
请检查 ArtifactStore 是否对 accepted artifact 计算 full sha256。
不能只用时间戳作为身份。
```

### 7.3 Cleanup 是否安全？

禁止删除：

- valid asset 本地唯一副本
- report_selected asset
- 无 remote backup 的 archived important asset

Review Prompt：

```text
请检查 cleanup plan 是否保护 valid / report_selected artifact。
```

---

## 8. Manager AI Review

### 8.1 Manager 是否解释影响？

Manager 提案应该包含：

- 为什么新增 / 修改
- 依赖输入
- 预计输出
- 是否影响现有结果
- 是否需要重跑下游

Review Prompt：

```text
请检查 Manager proposal 是否给用户足够信息做确认。
```

### 8.2 Manager 是否生成结构化 patch？

自然语言不能直接执行。

Manager 修改 proposal 时，应同步更新 patch 或生成新的 `patch_id`。后端应做弱一致性验证，把 proposal/patch 摘要差异记录为 warning；只有 patch 缺失、schema 无效或危险 op 才阻断执行。

Review Prompt：

```text
请检查 Manager 输出是否包含结构化 patch。
后端不得从自然语言中直接猜测执行动作。
请检查 proposal.patch_id 指向的 patch 是否存在且 schema valid。
请检查 proposal/patch 不一致时是否以 warning 方式反馈，而不是因轻微文案差异中断流程。
```

### 8.3 Manager 是否误接受结果？

Manager review 至少检查：

- manifest valid
- output files exist
- metrics not empty if expected
- warnings considered
- downstream effect recorded

Review Prompt：

```text
请检查 Manager review 是否基于 manifest 和文件校验，而不是只看 Worker 自述。
```

### 8.4 Manager 是否正确处理执行器权限请求？

执行器权限请求应进入 RuntimeApprovalService，由 Manager AI 做风险分级。

默认策略：

```text
low       可由 Manager AI 自动审查批准。
medium    Manager AI 给出建议，默认请求用户确认。
high      必须用户确认，或默认拒绝。
dangerous 默认拒绝。
```

Review Prompt：

```text
请检查 WorkerAdapter 是否把执行器原生权限请求归一化为 PermissionRequest。
请检查 Manager AI 是否区分运行期权限请求和 GraphPatch 语义变更。
请检查 RuntimeApproval 不能绕过 PatchValidator，也不能允许 Worker 直接修改 graph/.git/valid asset。
```

---

## 9. 数据一致性 Review

### 9.0 时间戳是否统一？

持久化 JSON 必须使用 UTC ISO 8601 `Z` 时间戳。

Review Prompt：

```text
请检查 Graph IR、Manifest、ArtifactPointer 和 run metadata 是否统一写入 UTC 时间戳。
前端本地时区只用于展示，不应写回持久化 JSON。
```

### 9.1 Cards 是否和 Graph IR 同步？

Card 是 UI projection，但不能和 Graph 冲突。

Review Prompt：

```text
请检查 cards.json 中 linked_assets / linked_runs 是否存在于 graph/assets/runs。
```

### 9.2 Stale 是否传播？

上游 asset stale 时，下游：

- module
- card
- asset
- claim
- report item

都应更新或至少标记 warning。

Review Prompt：

```text
请检查 mark_downstream_stale 是否会影响 cards、claims 和 report items。
```

### 9.3 Report 是否只引用 valid 结果？

Review Prompt：

```text
请检查 Report 页面是否阻止引用 stale / rejected / missing asset。
如果引用了，应显示明显警告。
```

---

## 10. 最终上线前 Checklist

- [ ] 用户不能直接编辑 Graph IR
- [ ] Manager proposal 需要确认
- [ ] PatchValidator 覆盖关键破坏性操作
- [ ] 所有 accepted change 有 Git commit
- [ ] Worker 必须输出 manifest
- [ ] Manifest 路径安全
- [ ] 大文件不进入 Git
- [ ] Card 默认隐藏技术细节
- [ ] Advanced 页面只读优先
- [ ] semantic rollback 不丢历史
- [ ] stale propagation 可用
- [ ] Report 不引用 stale result
- [ ] 测试覆盖 PatchValidator / ManifestService / GitService
