# 01. 前端 UI 蓝图与页面结构草图

## 1. UI 总目标

默认界面要像传统 specialist / manager AI 对话产品，而不是 workflow IDE。

用户应该感觉：

> 我在和一个生信项目 Manager AI 对话，它帮我推进分析项目；  
> 我可以看到任务卡片和阶段结果；  
> 我可以要求修改、重跑、回退、加入报告；  
> 复杂版本管理和 Graph IR 都在后台自动处理。

---

## 2. 信息架构

默认主导航建议保持极简：

```text
Project
Tasks
Results
Report
```

高级入口隐藏在：

```text
Advanced
  ├── Active Graph
  ├── Assets
  ├── Claims
  ├── Git History
  ├── Raw Manifests
  └── Cleanup
```

默认用户不需要看到：

- 完整 Graph IR
- 完整 Asset lineage
- Git commit hash
- raw manifest
- patch ops
- artifact storage URI

---

## 3. 主界面布局

推荐三栏布局：

```text
┌──────────────────────┬──────────────────────────────┬──────────────────────┐
│ Manager AI Chat       │ Task / Module Cards           │ Selected Detail       │
│                      │                              │                      │
│ 用户提需求            │ 当前项目进展                  │ 当前选中 Card 的详情   │
│ Manager 提案          │ 待确认 / 进行中 / 已完成       │ 结果预览 / 操作按钮    │
│ 快捷操作              │                              │                      │
└──────────────────────┴──────────────────────────────┴──────────────────────┘
```

移动端可以改为：

```text
顶部：Project Header
Tab 1: Chat
Tab 2: Tasks
Tab 3: Results
Tab 4: Report
```

---

## 4. 页面结构草图

### 4.1 Project 页面

用途：项目概览。

```text
┌────────────────────────────────────────────────────────────┐
│ Header: 项目名 / 状态 / 最近保存 / 导出按钮                  │
├──────────────────────┬─────────────────────────────────────┤
│ Manager AI Chat       │ Project Overview                    │
│                      │                                     │
│                      │ 当前进度：RNA-seq 分析进行中         │
│                      │ 当前有效结果：DEG 表、火山图          │
│                      │ 待处理：GSEA/KEGG 待执行              │
│                      │ 最近变化：新增免疫浸润模块            │
│                      │ 下一步建议：执行功能富集分析          │
└──────────────────────┴─────────────────────────────────────┘
```

Project Overview 卡片建议：

- Current Goal
- Recent Accepted Work
- Pending Proposals
- Next Suggested Actions
- Risk / Attention

---

### 4.2 Tasks 页面

用途：核心工作界面。

```text
┌──────────────────────┬──────────────────────────────┬──────────────────────┐
│ Manager AI Chat       │ Task Stream                   │ Card Detail           │
│                      │                              │                      │
│ 用户：增加 GSEA KEGG  │ [待确认] 功能富集分析 Group     │ 标题：功能富集分析     │
│ AI：建议新增模块组     │   ├─ GSEA planned             │ 目的                  │
│ [接受] [修改] [影响]  │   └─ KEGG planned             │ 输入                  │
│                      │ [已完成] 差异表达分析           │ 预计输出              │
│                      │ [已完成] 重新 QC                │ Manager 说明          │
│                      │ [已完成] Trimming              │ 操作按钮              │
└──────────────────────┴──────────────────────────────┴──────────────────────┘
```

Tasks 页面是 MVP 最重要页面。

---

### 4.3 Results 页面

用途：只展示当前有效结果，不展示所有历史垃圾。

```text
┌──────────────────────┬─────────────────────────────────────┐
│ Manager AI Chat       │ Results                             │
│                      │                                     │
│                      │ Accepted Results                     │
│                      │ - DEG table                          │
│                      │ - Volcano plot                       │
│                      │ - QC report                          │
│                      │                                     │
│                      │ Candidate Results                    │
│                      │ - immune_heatmap planned             │
│                      │                                     │
│                      │ Stale / Superseded 默认折叠           │
└──────────────────────┴─────────────────────────────────────┘
```

Result Card 显示：

- 名称
- 类型
- 来自哪个 Task
- 当前状态
- 关键摘要
- 是否进入报告
- 查看来源链路（高级）

---

### 4.4 Report 页面

用途：最终交付内容。

```text
┌──────────────────────┬─────────────────────────────────────┐
│ Manager AI Chat       │ Report Builder                       │
│                      │                                     │
│                      │ 章节：                               │
│                      │ 1. 数据质控                           │
│                      │ 2. 差异表达分析                        │
│                      │ 3. 功能富集分析                        │
│                      │ 4. 免疫浸润分析                        │
│                      │                                     │
│                      │ [生成报告] [导出 Word/PDF]            │
└──────────────────────┴─────────────────────────────────────┘
```

Report item 由 Manager AI 从 valid assets / claims 中选择。

---

### 4.5 Advanced 页面

默认折叠，仅给高级用户和开发者。

```text
Advanced
├── Active Graph
├── Asset Lineage
├── Claims
├── Git History
├── Raw Manifests
├── Graph Patches
└── Cleanup Plan
```

Advanced 只读为主，不建议开放直接编辑。

---

## 5. Card 设计

### 5.1 Card 类型

建议先做 3 类：

```text
ModuleCard
RunCard
ResultCard
```

#### ModuleCard

表示分析方向或模块。

例如：

- 差异表达分析
- 功能富集分析
- 免疫浸润分析
- 批次效应检查

#### RunCard

表示一次实际执行。

例如：

- run_007: DESeq2 差异分析
- run_008: GSEA 分析
- run_009: KEGG 分析

#### ResultCard

表示一个用户可见成果。

例如：

- DEG table
- Volcano plot
- GSEA plot
- immune heatmap

---

### 5.2 Card 默认字段

用户默认看到：

```json
{
  "title": "功能富集分析",
  "status": "planned",
  "summary": "基于差异表达结果进行 GSEA 和 KEGG 分析。",
  "why": "用于解释差异基因涉及的生物学通路。",
  "inputs": ["deg_table_v1", "ranked_gene_list_v1"],
  "outputs": ["gsea_result", "kegg_result", "enrichment_plots"],
  "key_findings": [],
  "manager_review": "待执行。",
  "next_actions": ["开始执行", "修改方案", "取消模块"]
}
```

默认不显示：

- graph_node_id
- raw patch
- hash
- storage uri
- full command
- manifest json

---

### 5.3 Card 状态

```text
proposed     Manager AI 提议，等待用户确认
planned      已加入蓝图，未执行
running      Worker 正在执行
needs_review Worker 已完成，等待 Manager 审核
accepted     Manager 接受结果
rejected     结果被拒绝
stale        上游变化导致过期
superseded   被新版本替代
cancelled    用户取消
failed       执行失败
```

颜色建议：

```text
proposed: purple
planned: blue
running: orange
needs_review: amber
accepted: green
rejected: red
stale: gray/yellow
superseded: gray
cancelled: gray
failed: red
```

---

### 5.4 Module Group

用于分叉任务，例如 DEG 后的 GSEA + KEGG + GO。

```text
ModuleGroup: 功能富集分析
├── SubModule: GSEA
├── SubModule: KEGG
└── SubModule: GO
```

主界面默认只显示 Group Card，点击后展开子任务。

Group Card 的 `status` 仍必须使用统一 Card 状态枚举。

子任务汇总结果放在单独字段 `aggregate_status`，不要混入 `status`。

`aggregate_status` 是派生的 UI projection 字段，可以由子模块状态实时计算；Graph IR 不应把它作为语义真相。

```text
all accepted       → status: accepted, aggregate_status: all_accepted
some running       → status: running, aggregate_status: has_running
some failed        → status: failed, aggregate_status: has_failed
some planned       → status: planned, aggregate_status: partially_planned
mixed child states → status: planned/running/failed by priority, aggregate_status: mixed
upstream stale     → status: stale, aggregate_status: stale
```

显示层可以把 `aggregate_status: all_accepted` 渲染为“completed”，但后端不要把 `completed` 写入 Card 的 `status`。

---

## 6. Manager AI Chat 设计

### 6.1 Chat 是主控制入口

用户通过自然语言向 Manager AI 提需求。

常见用户输入：

```text
客户想增加免疫浸润分析模块
DEG 出来了，同时做 GSEA 和 KEGG
这个结果不认可，重跑
把这个图加入报告
回退到差异表达之前
清理无用结果
解释一下这个模块为什么要做
```

### 6.2 Manager AI 回复模式

Manager AI 不应直接悄悄修改项目。

优先采用：

```text
解释 → 提案 → 影响 → 等待确认
```

示例：

```text
我建议新增“功能富集分析”模块组，依赖当前已接受的 DEG 结果。
包含 GSEA 和 KEGG 两个子模块。
这不会影响现有差异表达结果，但会新增两个下游结果和报告段落。

是否加入当前蓝图？
[接受提案] [修改提案] [查看影响]
```

### 6.3 Quick Actions

每条提案可带按钮：

- 接受提案
- 修改提案
- 查看影响
- 开始执行
- 重跑
- 回退
- 加入报告
- 查看来源
- 取消模块

按钮本质上向 Manager AI 发送结构化 intent。

---

## 7. 前端模块拆分

推荐目录：

```text
frontend/
├── app/
│   ├── projects/[projectId]/page.tsx
│   ├── projects/[projectId]/tasks/page.tsx
│   ├── projects/[projectId]/results/page.tsx
│   ├── projects/[projectId]/report/page.tsx
│   └── projects/[projectId]/advanced/page.tsx
├── components/
│   ├── layout/
│   │   ├── AppShell.tsx
│   │   ├── ProjectHeader.tsx
│   │   └── SideNav.tsx
│   ├── manager-chat/
│   │   ├── ManagerChatPanel.tsx
│   │   ├── ChatMessage.tsx
│   │   ├── ProposalActions.tsx
│   │   └── ChatInput.tsx
│   ├── cards/
│   │   ├── CardStream.tsx
│   │   ├── ModuleCard.tsx
│   │   ├── ModuleGroupCard.tsx
│   │   ├── RunCard.tsx
│   │   ├── ResultCard.tsx
│   │   └── CardStatusBadge.tsx
│   ├── detail/
│   │   ├── CardDetailPanel.tsx
│   │   ├── ResultPreview.tsx
│   │   ├── ManagerReviewBlock.tsx
│   │   └── TechnicalDetailsCollapse.tsx
│   ├── graph/
│   │   ├── MiniLineageGraph.tsx
│   │   └── AdvancedGraphView.tsx
│   ├── results/
│   │   ├── ResultsGrid.tsx
│   │   └── ResultViewer.tsx
│   └── report/
│       ├── ReportBuilder.tsx
│       └── ReportSectionCard.tsx
├── lib/
│   ├── api.ts
│   ├── types.ts
│   └── status.ts
└── styles/
```

---

## 8. 推荐前端技术栈

MVP 建议：

```text
Next.js / React / TypeScript
Tailwind CSS
shadcn/ui
TanStack Query
Zustand 或 React Context
React Markdown
Monaco Editor（高级详情 / JSON 查看）
React Flow（高级 Graph）
ECharts / Plotly（结果图表预览）
```

不要一开始用复杂图编辑器。

React Flow 只用于高级只读图或局部 lineage 预览。

---

## 9. 容易出错的 UI 节点，需要 AI Review

### 9.1 不要把高级字段默认展示给用户

Review 检查：

- Card 是否暴露 raw graph id / hash / storage uri？
- 是否把 manifest json 直接放到默认卡片？
- 是否把 Graph IR 编辑入口暴露给普通用户？

### 9.2 不要让用户直接编辑蓝图

Review 检查：

- 是否存在直接编辑 graph.json 的 UI？
- 是否允许拖拽连线直接修改后端？
- 是否绕过 Manager AI 和 Patch Validator？

### 9.3 Card 状态是否和后端状态一致

Review 检查：

- Worker 完成后是否进入 needs_review，而不是直接 accepted？
- 上游 stale 后，下游 Card 是否显示 stale？
- rejected run 是否不进入 Results 默认列表？

### 9.4 Advanced 页面要只读优先

Review 检查：

- Advanced Graph 是否只读？
- Raw JSON 是否只读？
- 编辑操作是否仍然通过 Manager AI proposal？
