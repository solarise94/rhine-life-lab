# 02. 后端实现蓝图

## 1. 后端定位

后端不是重型 workflow runtime。

后端职责：

1. 管理项目目录
2. 读写 Graph IR / Cards / Runs / Assets
3. 校验并应用 Patch
4. 调用 Worker Agent Adapter
5. 解析 Manifest
6. 生成 Git commit
7. 管理 artifact pointer
8. 提供 API 给前端
9. 隐藏复杂底层实现

后端暂不负责：

- 复杂 DAG 调度
- 用户自由编辑 Graph
- 多 worker 并行资源调度
- HPC 队列
- DVC / OpenLineage 全量实现

---

## 2. 推荐技术栈

### Python 后端方案

```text
FastAPI
Pydantic
Uvicorn
GitPython 或 subprocess git
SQLite / PostgreSQL
watchfiles
orjson
python-multipart
```

Worker 相关：

```text
subprocess
ptyprocess / pexpect
asyncio
websocket streaming
```

未来可选：

```text
Celery / RQ / Dramatiq
LangGraph
DVC
OpenLineage
```

---

## 3. 后端模块结构

```text
backend/
├── app/
│   ├── main.py
│   ├── api/
│   │   ├── projects.py
│   │   ├── chat.py
│   │   ├── cards.py
│   │   ├── runs.py
│   │   ├── results.py
│   │   ├── report.py
│   │   ├── advanced.py
│   │   └── artifacts.py
│   ├── core/
│   │   ├── config.py
│   │   ├── paths.py
│   │   ├── errors.py
│   │   └── logging.py
│   ├── models/
│   │   ├── graph.py
│   │   ├── cards.py
│   │   ├── patches.py
│   │   ├── runs.py
│   │   ├── artifacts.py
│   │   └── chat.py
│   ├── services/
│   │   ├── project_service.py
│   │   ├── graph_store.py
│   │   ├── card_projection.py
│   │   ├── patch_validator.py
│   │   ├── patch_apply.py
│   │   ├── git_service.py
│   │   ├── artifact_store.py
│   │   ├── manager_service.py
│   │   ├── worker_service.py
│   │   ├── manifest_service.py
│   │   └── report_service.py
│   ├── workers/
│   │   ├── base.py
│   │   ├── fake_worker.py
│   │   ├── shell_worker.py
│   │   ├── opencode_worker.py
│   │   ├── claude_code_worker.py
│   │   └── kimi_worker.py
│   └── schemas/
│       ├── graph.schema.json
│       ├── card.schema.json
│       ├── patch.schema.json
│       ├── manifest.schema.json
│       └── task_packet.schema.json
└── tests/
```

---

## 4. 核心服务职责

### 4.1 ProjectService

职责：

- 创建项目目录
- 初始化 Git repo
- 初始化 graph / cards / runs / scripts 目录
- 返回项目状态
- 加载项目配置

接口：

```python
create_project(name: str) -> Project
get_project(project_id: str) -> Project
list_projects() -> list[Project]
```

---

### 4.2 GraphStore

职责：

- 读取 graph JSON 文件
- 写入 graph JSON 文件
- 提供 atomic write
- 维护 schema version
- 提供备份

接口：

```python
load_graph(project_id) -> GraphState
save_graph(project_id, graph_state) -> None
load_cards(project_id) -> list[Card]
save_cards(project_id, cards) -> None
```

注意：

- 写文件必须使用临时文件 + rename，避免写一半损坏。
- 保存前必须 schema validate。

---

### 4.3 PatchValidator

职责：

校验 Manager AI 生成的 patch 是否安全。

必须检查：

- op 是否在 allowlist
- 引用的 asset / module 是否存在
- 是否会造成循环依赖
- 是否覆盖 valid asset
- 是否删除当前 valid result
- 是否修改只读字段
- 是否 schema valid
- 是否需要用户确认

接口：

```python
validate_patch(project_id, patch) -> ValidationResult
```

---

### 4.4 PatchApplyService

职责：

- 应用 GraphPatch
- 应用 CardPatch
- 生成 UI projection
- 写入文件
- 调用 Git commit

接口：

```python
apply_patch(project_id, patch, actor="manager_ai") -> ApplyResult
```

应用流程：

```text
1. load current graph/cards
2. validate patch
3. clone in memory
4. apply ops
5. validate resulting graph/cards
6. atomic write
7. git add
8. git commit
9. return updated project snapshot
```

事务要求：

- patch apply 必须持有项目级写锁，避免两个请求同时修改 graph/cards。
- 写入前保留当前文件快照或临时备份。
- 如果 schema validate 失败，不得写入任何项目文件。
- 如果 git commit 失败，优先自动恢复到写入前状态；只有自动恢复失败时，才把项目标记为 dirty/recovery_required 并阻止继续 apply。
- commit 成功后才能把 apply 结果返回给前端作为 accepted change。

---

### 4.5 GitService

职责：

- git init
- git status
- git add
- git commit
- git diff
- git log
- semantic rollback helper

接口：

```python
init_repo(path)
commit(project_id, message, paths)
get_log(project_id, limit=20)
get_diff(project_id, commit_a, commit_b)
create_rollback_commit(project_id, target_ref, reason)
```

注意：

- Git commit 是后端操作，不直接暴露给普通用户。
- UI 展示“版本历史”，不展示复杂 Git 命令。

---

### 4.6 ManagerService

职责：

- 接收用户 intent
- 构造 Manager prompt
- 调用 LLM 或 mock
- 输出 Proposal 或 Patch
- 生成自然语言解释

MVP 可先 mock：

```python
if user_message contains "免疫浸润":
    return proposal_add_immune_module()
if user_message contains "GSEA" and "KEGG":
    return proposal_add_enrichment_group()
```

未来再接真实模型。

输出类型：

```text
ChatResponse
Proposal
Patch
RunRequest
Explanation
```

---

### 4.7 WorkerService

职责：

- 根据 Card / Module 生成 task_packet
- 创建 run directory
- 调用 Worker Adapter
- streaming transcript
- 等待 manifest
- 返回 run status

接口：

```python
start_run(project_id, card_id, worker_type) -> Run
get_run(project_id, run_id) -> Run
stream_run_events(project_id, run_id)
```

---

### 4.8 ManifestService

职责：

- 读取 manifest.json
- 校验 schema
- 确认输出文件存在
- 计算 artifact quick fingerprint / full hash
- 转换为 Manager Review 输入

接口：

```python
load_manifest(project_id, run_id) -> Manifest
validate_manifest(project_id, run_id) -> ValidationResult
manifest_to_review_context(manifest) -> ReviewContext
```

---

### 4.9 ArtifactStore

职责：

- 大文件不进 Git
- accepted artifact 进入 content-addressed store
- Git 保存 pointer JSON
- 维护 artifact index

最小实现：

```text
artifact_store/
└── sha256/
    ├── ab/
    │   └── abcdef....h5ad
    └── 7f/
        └── 7f9912....tsv.gz
```

目录语义：

- `artifact_store/` 保存大文件实体，不进入 Git。
- `artifacts/pointers/` 保存 pointer JSON，进入 Git。
- `assets.json` 只引用 `artifact_id` 和 pointer 路径，不直接引用大文件真实路径作为版本身份。

接口：

```python
register_candidate(path, run_id) -> CandidateArtifact
accept_artifact(candidate, metadata) -> ArtifactPointer
compute_full_hash(path) -> str
write_pointer(project_id, artifact_pointer) -> Path
verify_artifact(artifact_id) -> VerificationResult
```

---

## 5. Patch 操作 allowlist

MVP 允许这些 op：

```text
create_module
update_module_summary
set_module_status
create_module_group
add_submodule
create_card
update_card
set_card_status
create_asset
set_asset_status
connect_dependency
create_claim
set_claim_status
create_run
attach_run_to_card
attach_asset_to_card
add_report_item
remove_report_item
mark_downstream_stale
propose_cleanup
semantic_rollback
```

禁止：

```text
delete_valid_asset
overwrite_artifact_hash
direct_edit_git_commit
remove_run_history
edit_raw_manifest
bypass_manager_review
bypass_validation
```

---

## 6. 推荐 API

### Projects

```http
POST /api/projects
GET /api/projects
GET /api/projects/{project_id}
```

### Chat

```http
POST /api/projects/{project_id}/chat
```

Request:

```json
{
  "message": "客户想增加免疫浸润分析模块",
  "context": {
    "selected_card_id": null,
    "selected_result_id": null
  }
}
```

Response:

```json
{
  "message": "我建议新增“免疫浸润分析”模块...",
  "proposal": {
    "proposal_id": "proposal_001",
    "title": "新增免疫浸润分析",
    "impact_summary": "新增下游模块，不影响已有 DE 结果。",
    "patch_preview": {}
  },
  "actions": [
    {"label": "接受提案", "action": "accept_proposal"},
    {"label": "修改提案", "action": "modify_proposal"},
    {"label": "查看影响", "action": "view_impact"}
  ]
}
```

### Proposal

```http
POST /api/projects/{project_id}/proposals/{proposal_id}/accept
POST /api/projects/{project_id}/proposals/{proposal_id}/reject
POST /api/projects/{project_id}/proposals/{proposal_id}/modify
```

持久化：

- `graph/proposals.json` 保存 proposal 元数据、状态和 `patch_id`。
- `graph/patches/{patch_id}.json` 保存结构化 patch。
- accept proposal 时只能按 `patch_id` 读取并校验 patch，不得从聊天自然语言重新推断操作。
- Manager AI 修改 proposal 时，应同步更新对应 patch，或生成新的 `patch_id` 并让 proposal 指向新 patch。
- 后端只做弱一致性验证：确认 `patch_id` 存在、patch schema valid、关键对象标题/类型大体匹配。弱验证失败时默认生成 warning，交给 Manager AI 解释或修正；只有 patch 缺失、schema 无效或包含危险 op 时才阻断 apply。

### Cards

```http
GET /api/projects/{project_id}/cards
GET /api/projects/{project_id}/cards/{card_id}
POST /api/projects/{project_id}/cards/{card_id}/start-run
POST /api/projects/{project_id}/cards/{card_id}/request-rerun
POST /api/projects/{project_id}/cards/{card_id}/request-rollback
```

### Runs

```http
GET /api/projects/{project_id}/runs
GET /api/projects/{project_id}/runs/{run_id}
GET /api/projects/{project_id}/runs/{run_id}/events
POST /api/projects/{project_id}/runs/{run_id}/accept
POST /api/projects/{project_id}/runs/{run_id}/reject
```

### Results

```http
GET /api/projects/{project_id}/results
GET /api/projects/{project_id}/results/{asset_id}
```

### Advanced

```http
GET /api/projects/{project_id}/advanced/graph
GET /api/projects/{project_id}/advanced/git-log
GET /api/projects/{project_id}/advanced/manifest/{run_id}
GET /api/projects/{project_id}/advanced/patches
```

---

## 7. MVP 运行流程

### 7.1 新增模块

```text
User chat:
  "客户想增加免疫浸润分析模块"

ManagerService:
  生成 proposal + patch preview

Frontend:
  显示提案和按钮

User:
  点击接受

Backend:
  validate patch
  apply patch
  update modules.json/cards.json/graph.json
  git commit
  return updated cards

Frontend:
  新 card 出现在 Tasks 页面
```

---

### 7.2 执行 Card

```text
User:
  点击“开始执行”

WorkerService:
  创建 run directory
  生成 task_packet.json
  调用 fake_worker / shell_worker

Worker:
  写 transcript.md
  写 commands.log
  写 manifest.json

ManifestService:
  校验 manifest
  检查输出文件
  计算 hash

ManagerService:
  review manifest
  生成 graph_patch + card_update

PatchApply:
  apply
  git commit

Frontend:
  Card 变 accepted
  Results 显示新成果
```

---

## 8. 容易出错节点，需要 AI Review

### 8.1 Patch Validator

必须重点 review。

常见错误：

- 允许删除 valid asset
- 允许覆盖 hash
- 允许 orphan edge
- 没检测 cycle
- 未校验 op schema
- 未区分 proposed / accepted

Review 提示：

```text
请检查 patch validator 是否能阻止破坏性修改。
重点检查 valid asset、run history、artifact hash 是否不可被普通 patch 直接覆盖。
```

---

### 8.2 Atomic Write + Git Commit

常见错误：

- 文件写一半失败导致 graph 损坏
- commit 前没有 schema validate
- commit 只加了部分文件
- run 文件和 graph 状态不同步

Review 提示：

```text
请检查 apply_patch 的事务性。
失败时不能留下半更新 graph。
commit 中必须包含 graph/cards/runs/manifest 等相关文件。
```

---

### 8.3 Worker Manifest

常见错误：

- Worker 生成结果但 manifest 缺字段
- manifest 声称文件存在但实际不存在
- 输出写到了不允许路径
- Worker 覆盖了 valid asset
- 大文件进入 Git

Review 提示：

```text
请检查 manifest validation 是否确认文件存在、路径合法、不会覆盖 valid asset，并阻止大文件进入 Git。
```

---

### 8.4 Git Rollback

常见错误：

- 直接 git reset 导致历史丢失
- 回退后没有更新 Card 状态
- 下游 stale 未传播
- 回退没有 manifest/review 记录

Review 提示：

```text
请优先实现 semantic rollback run，而不是直接 reset。
回退动作也必须成为一次可审计 commit。
```

---

### 8.5 Artifact Store

常见错误：

- 用时间戳当版本身份
- 不计算 hash
- accepted artifact 没写 pointer
- cleanup 删除了 valid artifact
- Git 误加入 h5ad / bam / fastq

Review 提示：

```text
请检查大文件是否只进入 artifact store，Git 是否只保存 pointer JSON。
accepted artifact 必须有 full sha256。
```
