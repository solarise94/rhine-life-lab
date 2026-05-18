# 04. Vibe Coding 实现步骤与任务拆分

## 1. 总体实现策略

先做一个能跑通的端到端最小闭环，不要一开始接真实 LLM / OpenCode / Claude Code。

推荐顺序：

```text
Phase 1: 静态 UI + mock 数据
Phase 2: 后端项目文件读写
Phase 3: Manager AI mock proposal
Phase 4: Patch apply + Git commit
Phase 5: Fake Worker run + manifest
Phase 6: Manager review mock
Phase 7: Artifact pointer
Phase 8: 接真实 Worker Agent
Phase 9: Advanced view
```

每个 Phase 都要能独立运行和演示。

---

## 2. Phase 1：静态 UI + mock 数据

目标：

- 做出接近最终产品体验的 UI
- 用户看到 Manager Chat + Task Cards + Detail Panel
- 不需要后端

实现：

```text
Next.js 页面
Mock cards.json
Mock chat messages
Mock project overview
Mock result assets
```

页面：

```text
/projects/demo/tasks
```

组件：

- AppShell
- ManagerChatPanel
- CardStream
- ModuleCard
- ModuleGroupCard
- CardDetailPanel
- ResultPreview

验收：

- 能展示一个 RNA-seq 项目
- 左侧有 Manager AI chat
- 中间有任务卡片
- 右侧有卡片详情
- 有一个 planned 的“免疫浸润分析”
- 有一个 ModuleGroup “功能富集分析”，可展开 GSEA / KEGG

Review：

```text
请检查 UI 是否过度暴露 Graph IR 细节。
默认页面中不应出现 raw JSON、hash、storage_uri、GraphPatch ops。
```

---

## 3. Phase 2：后端项目文件读写

目标：

- 后端能创建项目
- 能读取 / 写入 graph/cards/modules/assets JSON
- 前端从 API 获取数据

实现：

```text
FastAPI
ProjectService
GraphStore
```

API：

```http
POST /api/projects
GET /api/projects/{project_id}
GET /api/projects/{project_id}/cards
GET /api/projects/{project_id}/results
```

初始项目模板：

```text
graph/
  graph.json
  cards.json
  modules.json
  assets.json
  claims.json
  proposals.json
  patches/
runs/
scripts/
results/
artifacts/pointers/
artifact_store/
```

验收：

- 创建项目后目录存在
- Git repo 初始化
- cards API 返回卡片
- 前端能读取后端数据

Review：

```text
请检查文件写入是否 atomic。
请检查初始化模板是否包含 schema_version。
请检查 proposals.json 和 graph/patches/ 是否已初始化。
```

---

## 4. Phase 3：Manager AI mock proposal

目标：

- 用户在 chat 输入需求
- 后端返回 proposal
- 前端显示提案和 quick actions

实现：

```text
ManagerService mock
Chat API
Proposal store
```

Proposal store 最小落盘：

```text
graph/proposals.json
graph/patches/{patch_id}.json
```

前端 accept proposal 时只传 `proposal_id`；后端从 store 解析到 `patch_id`，重新 schema validate 后再 apply。

Proposal/Patch 同步策略：

```text
Manager 修改 proposal → 同步修改原 patch，或生成新 patch_id
accept proposal → 后端读取当前 patch_id
弱验证 proposal 摘要与 patch ops 是否大体一致
弱验证 warning → 返回给 Manager/前端展示或解释
patch 缺失 / schema invalid / 危险 op → 阻断 apply
```

规则示例：

```text
包含“免疫浸润” → proposal_add_immune_module
包含“GSEA”或“KEGG” → proposal_add_enrichment_group
包含“回退” → proposal_semantic_rollback
```

API：

```http
POST /api/projects/{project_id}/chat
POST /api/projects/{project_id}/proposals/{proposal_id}/accept
```

验收：

- 输入“客户想增加免疫浸润分析模块”
- Manager 返回解释和提案
- 前端出现“接受提案 / 修改提案 / 查看影响”
- 点击接受后先可以 mock 成功

Review：

```text
请检查 Proposal 不应该直接修改项目。
只有 accept_proposal 后才能 apply patch。
请检查 Manager 修改 proposal 时是否同步更新 patch 或切换到新 patch_id。
Proposal 与 Patch 不一致时优先产生 warning，不应因为轻微摘要差异中断流程。
```

---

## 5. Phase 4：Patch apply + Git commit

目标：

- 接受 proposal 后生成/应用 patch
- 更新 cards/modules/graph
- 自动 git commit
- 前端刷新显示新 card

实现：

```text
PatchValidator
PatchApplyService
GitService
```

流程：

```text
accept proposal
  ↓
load patch
  ↓
validate patch
  ↓
apply patch
  ↓
write JSON files
  ↓
git add
  ↓
git commit
  ↓
return updated cards
```

失败恢复：

```text
apply 前获取项目写锁
写入前创建内存副本和临时文件
schema validate 失败 → 不写入
git commit 失败 → 优先恢复写入前快照
自动恢复失败 → 标记 dirty/recovery_required 并阻止继续 apply
commit 成功 → 返回 accepted snapshot
```

验收：

- 接受“免疫浸润分析”后 cards.json 增加 card
- modules.json 增加 module
- git log 有 commit
- UI 中出现 planned card

Review：

```text
请重点检查 PatchValidator。
禁止 patch 删除 valid asset、覆盖 artifact hash、移除 run history。
```

---

## 6. Phase 5：Fake Worker run + manifest

目标：

- 点击 Card 的“开始执行”
- 生成 run directory
- 生成 task_packet.json
- fake worker 写 manifest.json
- 前端显示 running → needs_review

实现：

```text
WorkerService
FakeWorker
ManifestService
```

FakeWorker 行为：

```text
1. 读取 task_packet.json
2. 写 commands.log
3. 创建几个小型结果文件
4. 写 manifest.json
```

API：

```http
POST /api/projects/{project_id}/cards/{card_id}/start-run
GET /api/projects/{project_id}/runs/{run_id}
GET /api/projects/{project_id}/runs/{run_id}/events
```

验收：

- run_xxx 目录生成
- task_packet.json 存在
- manifest.json 存在
- card 状态变 needs_review

Review：

```text
请检查 fake worker 的输出路径必须在 allowed_paths 中。
请检查 manifest 中声明的文件实际存在。
请检查 worker 结束后是否扫描了越界写入，并将越界输出失败或隔离到 quarantine。
```

---

## 7. Phase 6：Manager review mock

目标：

- Manager AI 审核 manifest
- 接受结果后更新 card / assets / claims
- Git commit

实现：

```text
ManagerService.review_manifest()
ManifestService.validate()
PatchApplyService.apply_review_patch()
```

流程：

```text
manifest validated
  ↓
manager review
  ↓
graph/card patch
  ↓
apply
  ↓
git commit
```

验收：

- Card 从 needs_review → accepted
- Results 页面出现新结果
- assets.json 出现新 asset
- git log 出现 accept run commit

Review：

```text
请检查 Worker 不能直接 accepted。
必须经过 Manager review。
```

---

## 8. Phase 7：Artifact pointer

目标：

- 大文件不进入 Git
- accepted artifact 生成 pointer JSON
- small result 可以直接进 Git 或 results

实现：

```text
ArtifactStore
compute hash
write pointer
.gitignore
```

`.gitignore` 建议：

```gitignore
data/**
results/**/*.h5ad
results/**/*.bam
results/**/*.fastq
results/**/*.fq
results/**/*.cram
artifact_store/**
!artifacts/pointers/*.json
```

验收：

- h5ad / 大矩阵不会被 git add
- pointer JSON 进入 Git
- asset 关联 artifact_id

Review：

```text
请检查 GitService commit 前不会把大文件加入 Git。
请检查 accepted artifact 有 full sha256。
```

---

## 9. Phase 8：接真实 Worker Agent

目标：

- 支持 OpenCode / Claude Code / Kimi / shell
- 保持统一 WorkerAdapter 接口

接口：

```python
class WorkerAdapter:
    async def start(self, task_packet_path: str, workspace: str) -> AsyncIterator[WorkerEvent]:
        ...
```

实现顺序：

```text
1. ShellWorker
2. PTYWorker
3. OpenCodeWorker
4. ClaudeCodeWorker
5. KimiWorker
```

注意：

- 先接 CLI，不要一开始做 ACP。
- 所有 Worker 输出都要落 transcript.md。
- Worker 必须生成 manifest。
- 无 manifest 则 run failed。
- WorkerAdapter 负责把 TaskPacket 的统一策略翻译成 OpenCode / Claude Code / Codex / shell 等执行器自己的 sandbox、permission、approval、cwd、writable roots 和环境变量配置。
- 环境变量只作为策略提示，不作为唯一安全边界。
- 默认采用渐进权限模式：

```text
audit   MVP 默认：宽松执行 + 执行后文件变更扫描。
guarded 真实 Worker 默认：使用执行器原生权限机制 + 执行后审计。
strict  高风险任务：隔离 workspace / 容器 / 更强 sandbox。
```

Review：

```text
请检查 WorkerAdapter 是否把 TaskPacket policy 翻译为执行器原生权限配置。
请检查没有把环境变量当成唯一安全边界。
请检查 guarded/strict 模式是否仍保留执行后文件变更扫描。
```

---

## 10. Phase 9：Advanced View

目标：

- 给开发者查看 Graph / Git / Manifest / Patch
- 默认只读

页面：

```text
/projects/{id}/advanced/graph
/projects/{id}/advanced/git
/projects/{id}/advanced/runs/{run_id}/manifest
```

实现：

- React Flow 只读 Graph
- Monaco 只读 JSON
- Git log table
- Diff viewer

Review：

```text
请检查 Advanced 页面是否默认只读。
编辑仍然需要通过 Manager AI proposal。
```

---

## 11. 最小验收场景

AI 编程助手最终应该跑通以下剧本：

1. 打开 demo project
2. 用户输入：“客户想增加免疫浸润分析模块”
3. Manager AI 返回提案
4. 用户点击接受
5. UI 增加 planned card
6. Git 生成 commit
7. 用户点击开始执行
8. FakeWorker 生成 manifest 和结果文件
9. Manager review 接受
10. Card 变 accepted
11. Results 出现 immune_score_table
12. 用户输入：“DEG 出来了，同时做 GSEA 和 KEGG”
13. Manager 增加 ModuleGroup
14. UI 显示功能富集分析 group，内含 GSEA / KEGG
15. 用户点击 Report 页面，把 accepted result 加入报告

---

## 12. 代码质量要求

请 AI 编程助手遵守：

1. 所有类型定义集中在 `types.ts` / Pydantic models。
2. 所有 JSON 写入前 schema validate。
3. 所有 patch 必须通过 PatchValidator。
4. 所有外部命令调用必须记录 log。
5. 所有 run 必须有 run_id。
6. 所有 accepted change 必须 Git commit。
7. 所有大文件必须通过 artifact pointer。
8. UI 默认不要暴露高级字段。
9. Advanced 只读优先。
10. 测试覆盖 PatchValidator / GitService / ManifestService。

---

## 13. 建议测试用例

### PatchValidator

- create_module 正常通过
- create_module 引用不存在 asset 应失败
- delete_valid_asset 应失败
- overwrite_hash 应失败
- cycle dependency 应失败
- unknown op 应失败

### ManifestService

- manifest 缺 status 应失败
- created file 不存在应失败
- path 越界应失败
- 大文件未走 artifact pointer 应警告或失败

### GitService

- apply patch 后有 commit
- commit message 包含 run/proposal 信息
- rollback 生成新 commit，而不是 reset

### Frontend

- proposed card 显示正确
- accepted card 不显示 technical fields
- group card 可展开
- card detail 操作按钮可用
- Advanced 页面只读

---

## 14. 开发优先级

P0：

- UI mock
- Project create/load
- Cards API
- Chat mock
- Proposal accept
- Patch apply
- Git commit

P1：

- Fake worker
- Manifest validation
- Manager review mock
- Results view
- Artifact pointer

P2：

- Report builder
- Semantic rollback
- ModuleGroup branch
- Advanced read-only graph

P3：

- Real worker adapter
- ACP / CLI / PTY
- DVC / OpenLineage optional
- remote artifact sync
