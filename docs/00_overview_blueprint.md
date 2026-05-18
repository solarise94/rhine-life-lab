# 00. Git-native Agentic Bioinformatics Analysis Repo 总体蓝图

## 1. 产品定位

本项目不是传统 workflow editor，也不是 Dify / Langflow 式节点编排器。

它的产品形态是：

> 前台像一个简单的 specialist / manager AI 对话产品；  
> 后台像一个严谨的 Git-native 版本化生信分析项目系统。

用户主要通过 Manager AI 提需求：

- “客户想增加免疫浸润分析模块”
- “这个结果不认可，重跑”
- “把这张图加入报告”
- “回退到差异表达之前”
- “基于 DEG 同时做 GSEA 和 KEGG”

用户不直接编辑 Graph IR / YAML / JSON 蓝图。

Manager AI 负责把用户意图转换成结构化 patch，并通过后端校验后写入项目文件与 Git。

---

## 2. 核心原则

### 2.1 用户只编辑意图，不直接编辑蓝图

用户权限停留在 Intent Level：

```text
用户提出需求
  ↓
Manager AI 解释 / 拟定方案
  ↓
用户确认或修改
  ↓
Manager AI 生成 patch
  ↓
后端校验并应用
```

用户不直接修改：

- Graph edge
- Asset ID
- Method schema
- YAML 字段
- stale 传播规则
- artifact hash
- Git commit

---

### 2.2 Worker Agent 尽量自由，Graph Update 必须严格

Worker Agent 可以自由：

- 读项目文件
- 写脚本
- 调用 Python / R / Shell / 生信工具
- 生成结果
- 写 manifest

但 Worker Agent 不直接更新 Graph IR，不直接接受结果。

Manager AI + 后端负责：

- 审核 manifest
- 生成 GraphPatch / CardPatch
- 校验 patch
- 应用 patch
- Git commit
- 更新 UI projection

---

### 2.3 Card 是用户视角，Graph IR 是机器语义

Card 展示用户关心的信息：

- 这一步做什么
- 为什么做
- 当前状态
- 输入输出
- 关键结果
- Manager 评价
- 下一步建议

Graph IR 存储机器语义：

- asset_id
- method_id
- dependency edges
- artifact hash
- stale / superseded / valid
- claim dependency
- cleanup status

默认 UI 不暴露复杂 Graph IR。

---

### 2.4 Git 是版本时间轴，Graph IR 是语义真相

```text
Git:
  保存每次 accepted patch / run 的项目快照

Graph IR:
  保存当前项目语义状态

Manifest:
  保存 Worker Agent 的执行事实

Card:
  保存用户可读的阶段成果摘要

Artifact pointer:
  保存大文件身份与存储位置
```

一句话：

> Git 负责“能回到过去”；Graph IR 负责“知道过去是什么意思”。

---

## 3. 推荐系统形态

```text
User
  ↓ natural language intent
Manager AI
  ↓ proposal / patch
Patch Validator
  ↓ apply
Graph IR + Cards + Runs
  ↓ commit
Git Repository
  ↓ projection
Frontend UI
```

执行任务时：

```text
Manager AI
  ↓ task_packet.json
Worker Agent Adapter
  ↓ OpenCode / Claude Code / Kimi / CLI
Worker Agent
  ↓ manifest.json + artifacts
Manager AI Review
  ↓ graph_patch.json + card_patch.json
Patch Validator
  ↓ apply
Git Commit
  ↓ UI refresh
```

---

## 4. 推荐仓库结构

```text
project/
├── .git/
├── graph/
│   ├── graph.json
│   ├── modules.json
│   ├── cards.json
│   ├── assets.json
│   ├── methods.json
│   ├── claims.json
│   ├── cleanup.json
│   ├── proposals.json
│   └── patches/
│       ├── patch_0001.json
│       └── patch_0002.json
├── runs/
│   ├── run_001/
│   │   ├── task_packet.json
│   │   ├── transcript.md
│   │   ├── commands.log
│   │   ├── manifest.json
│   │   ├── manager_review.md
│   │   └── graph_patch.json
│   └── run_002/
├── scripts/
│   ├── generated/
│   └── curated/
├── configs/
│   └── params.yaml
├── reports/
│   ├── summaries/
│   └── final/
├── artifacts/
│   └── pointers/
│       ├── art_0001.json
│       └── art_0002.json
├── artifact_store/
│   └── sha256/
├── results/
│   ├── qc/
│   ├── counts/
│   ├── de/
│   └── plots/
└── data/
    └── README.md
```

说明：

- `artifacts/pointers/` 保存进入 Git 的 artifact pointer JSON。
- `artifact_store/` 保存不进入 Git 的大文件实体，按 content hash 组织。
- `graph/proposals.json` 保存待确认 proposal 的元数据。
- `graph/patches/` 保存可校验的结构化 patch，proposal accept 时按 `patch_id` 读取。

---

## 5. 最小 MVP 范围

MVP 不做复杂 workflow runtime。

MVP 做：

1. Manager AI Chat
2. Task / Module Cards
3. Card detail panel
4. Simple Results view
5. Report draft view
6. Graph IR JSON files
7. Patch validation and apply
8. Worker Agent adapter skeleton
9. Run directory generation
10. Manifest parsing
11. Git commit per accepted change
12. Artifact pointer JSON
13. Basic rollback by semantic patch
14. Advanced details hidden behind collapsible panels

MVP 暂不做：

- 用户自由拖拽 graph
- 完整 DAG scheduler
- 多 worker 并行调度
- HPC / Kubernetes
- DVC / OpenLineage
- 多用户权限系统
- 实时协作编辑
- 完整数据湖管理

---

## 6. 未来可选增强

当痛点出现后再引入：

| 痛点 | 增强 |
|---|---|
| 大文件版本管理复杂 | DVC / git-annex |
| 标准血缘交换 | OpenLineage |
| 复杂 pipeline 重跑 | Snakemake / Nextflow |
| Manager 状态机复杂 | LangGraph |
| 大任务排队 | Queue / Celery / Temporal |
| HPC / 云计算 | Slurm / Kubernetes / Nextflow |
| 远端 artifact | S3 / MinIO / WebDAV / NAS |

---

## 7. AI 编程助手执行建议

实现时请先完成最小闭环：

```text
1. 新建项目
2. 用户通过 chat 请求新增模块
3. Manager mock 生成 proposal
4. 用户接受
5. 后端生成 Card + Module + GraphPatch
6. Git commit
7. UI 显示新 Card
8. 点击 Card 查看详情
```

然后再加入 Worker Agent 执行闭环：

```text
1. Card 点击“开始执行”
2. 生成 runs/run_xxx/task_packet.json
3. 调用 fake worker 或 shell worker
4. worker 生成 manifest.json
5. Manager review mock 接受
6. 生成 assets / key findings / graph_patch
7. Git commit
8. Card 状态更新为 accepted
```

先 mock LLM 和 worker，跑通架构，再接真实 OpenCode / Claude Code / Kimi。
