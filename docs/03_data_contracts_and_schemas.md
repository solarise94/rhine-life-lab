# 03. 数据契约、JSON Schema 与示例

## 1. 设计原则

所有 Manager AI / Worker Agent / 后端之间的关键交互都使用结构化 JSON。

LLM 可以生成自然语言解释，但后端只接受结构化 patch / manifest / task_packet。

必须做到：

```text
自然语言用于沟通
JSON 用于执行
Schema 用于校验
Git 用于保存
```

### 1.1 状态枚举边界

不同实体的 `status` 不共用一个枚举：

```text
Project status: active, archived, error
Card/Module status: proposed, planned, running, needs_review, accepted, rejected, stale, superseded, cancelled, failed
Asset/Claim status: candidate, valid, stale, superseded, rejected, archived, missing
Run status: queued, running, success, failed, cancelled
Manifest status: success, failed, partial
```

ModuleGroup 的汇总显示状态必须放在派生字段 `aggregate_status`，不要写入 Card/Module 的 `status`。推荐枚举：`all_accepted`、`has_running`、`has_failed`、`partially_planned`、`mixed`、`stale`。

### 1.2 时间戳规则

所有持久化 JSON 中的时间戳统一使用 UTC ISO 8601，格式为 `YYYY-MM-DDTHH:MM:SSZ`。

前端可以按用户或项目时区显示本地时间，但不得把本地时区时间写回 Graph IR / Manifest / ArtifactPointer 作为持久化值。

---

## 2. ProjectState

```json
{
  "project_id": "proj_rnaseq_001",
  "name": "RNA-seq 分析项目",
  "status": "active",
  "schema_version": "0.1.0",
  "current_goal": "完成 RNA-seq 差异表达与下游解释分析",
  "created_at": "2026-05-18T02:00:00Z",
  "updated_at": "2026-05-18T02:20:00Z"
}
```

---

## 3. Module

```json
{
  "module_id": "module_de_analysis",
  "title": "差异表达分析",
  "type": "analysis_module",
  "status": "accepted",
  "summary": "比较 Treatment 和 Control 的表达差异。",
  "depends_on_assets": ["count_matrix_v1", "sample_metadata_v1"],
  "expected_outputs": ["deg_table", "volcano_plot", "ma_plot"],
  "linked_cards": ["card_de_analysis"],
  "linked_runs": ["run_004"],
  "created_by": "manager_ai",
  "created_at": "2026-05-18T02:00:00Z"
}
```

---

## 4. ModuleGroup

```json
{
  "module_id": "module_group_enrichment",
  "title": "功能富集分析",
  "type": "module_group",
  "status": "planned",
  "summary": "基于 DEG 结果进行多个下游富集分析。",
  "depends_on_assets": ["deg_table_v1", "ranked_gene_list_v1"],
  "submodules": [
    {
      "module_id": "module_gsea",
      "title": "GSEA 分析",
      "status": "planned"
    },
    {
      "module_id": "module_kegg",
      "title": "KEGG 富集",
      "status": "planned"
    }
  ],
  "created_by": "manager_ai"
}
```

---

## 5. Card

```json
{
  "card_id": "card_enrichment_group",
  "card_type": "module_group",
  "title": "功能富集分析",
  "status": "planned",
  "aggregate_status": "partially_planned",
  "summary": "基于差异表达结果进行 GSEA 和 KEGG 分析。",
  "why": "用于解释差异基因涉及的生物学通路。",
  "inputs": [
    {
      "label": "差异表达结果",
      "asset_id": "deg_table_v1"
    }
  ],
  "outputs": [
    {
      "label": "GSEA 结果",
      "status": "planned"
    },
    {
      "label": "KEGG 结果",
      "status": "planned"
    }
  ],
  "key_findings": [],
  "manager_review": "待执行。",
  "next_actions": [
    "开始执行",
    "修改方案",
    "取消模块"
  ],
  "linked_modules": ["module_group_enrichment"],
  "linked_runs": [],
  "linked_assets": [],
  "technical_refs": {
    "graph_nodes": ["module_group_enrichment"],
    "patches": ["patch_001"]
  }
}
```

`aggregate_status` 只用于 ModuleGroup / Group Card。它可以写入 `cards.json` 作为 projection cache，但必须可由 graph/modules/runs/assets 重新计算；普通 patch 不应直接把它作为语义真相修改。

---

## 6. Asset

```json
{
  "asset_id": "deg_table_v1",
  "asset_type": "deg_table",
  "title": "差异表达结果表",
  "status": "valid",
  "created_by_run": "run_004",
  "path": "results/de/run_004/deg_table.tsv",
  "artifact_id": "art_deg_table_v1",
  "depends_on": ["count_matrix_v1", "sample_metadata_v1"],
  "summary": "Treatment vs Control 差异表达结果。",
  "metadata": {
    "num_significant_fdr_0_05": 1324
  }
}
```

---

## 7. Claim

```json
{
  "claim_id": "claim_ifn_activation",
  "text": "Treatment 组显示 interferon signaling 激活趋势。",
  "status": "valid",
  "depends_on_assets": ["deg_table_v1", "gsea_result_v1"],
  "created_by_run": "run_008",
  "report_selected": true
}
```

---

## 8. GraphPatch

GraphPatch 是 Manager AI 修改蓝图和 Graph IR 的唯一结构化入口。

```json
{
  "patch_id": "patch_add_enrichment_001",
  "patch_type": "add_module_group",
  "source": "manager_ai",
  "reason": "用户要求基于 DEG 结果同时进行 GSEA 和 KEGG 分析。",
  "requires_user_confirmation": true,
  "ops": [
    {
      "op": "create_module_group",
      "module_id": "module_group_enrichment",
      "title": "功能富集分析",
      "depends_on_assets": ["deg_table_v1"],
      "summary": "基于差异表达结果进行多个富集分析。"
    },
    {
      "op": "add_submodule",
      "parent_module_id": "module_group_enrichment",
      "module_id": "module_gsea",
      "title": "GSEA 分析",
      "expected_outputs": ["gsea_result_table", "gsea_plot"]
    },
    {
      "op": "add_submodule",
      "parent_module_id": "module_group_enrichment",
      "module_id": "module_kegg",
      "title": "KEGG 富集",
      "expected_outputs": ["kegg_result_table", "kegg_bubble_plot"]
    },
    {
      "op": "create_card",
      "card_id": "card_enrichment_group",
      "title": "功能富集分析",
      "card_type": "module_group",
      "status": "planned",
      "summary": "基于 DEG 结果进行 GSEA 和 KEGG 分析。"
    }
  ]
}
```

---

## 9. TaskPacket

Manager AI 给 Worker Agent 的任务包。

```json
{
  "task_id": "run_008",
  "project_id": "proj_rnaseq_001",
  "card_id": "card_enrichment_group",
  "goal": "基于已接受的 DEG 结果完成 GSEA 和 KEGG 分析。",
  "input_assets": [
    {
      "asset_id": "deg_table_v1",
      "path": "results/de/run_004/deg_table.tsv",
      "type": "deg_table"
    }
  ],
  "expected_outputs": [
    {
      "role": "gsea_result_table",
      "path_hint": "results/enrichment/run_008/gsea_result.tsv"
    },
    {
      "role": "kegg_result_table",
      "path_hint": "results/enrichment/run_008/kegg_result.tsv"
    },
    {
      "role": "enrichment_plots",
      "path_hint": "results/enrichment/run_008/plots/"
    },
    {
      "role": "summary",
      "path_hint": "results/enrichment/run_008/summary.md"
    }
  ],
  "allowed_paths": [
    "runs/run_008/",
    "scripts/generated/",
    "results/enrichment/run_008/"
  ],
  "readonly_paths": [
    "results/de/run_004/deg_table.tsv"
  ],
  "forbidden_paths": [
    ".git/",
    "graph/"
  ],
  "execution_policy": {
    "mode": "audit",
    "network": "prompt",
    "write_policy": "allowed_paths_with_post_run_audit",
    "on_policy_violation": "fail_or_quarantine"
  },
  "constraints": [
    "Do not overwrite existing valid assets.",
    "Write all outputs under results/enrichment/run_008/.",
    "Record commands in runs/run_008/commands.log.",
    "Write final manifest to runs/run_008/manifest.json."
  ],
  "worker_instructions": "You are a bioinformatics worker agent. You may write scripts and run commands. Produce a complete manifest."
}
```

`execution_policy.mode` 推荐枚举：

```text
audit    宽松执行，通过 task_packet/env/prompt 告知边界，执行后扫描变更。
guarded  WorkerAdapter 尽量翻译为执行器原生 sandbox / permission / approval 配置，仍保留执行后审计。
strict   高风险任务使用隔离 workspace、容器或更强 sandbox。
```

环境变量可以作为兼容层提示，例如 `BIOINFO_ALLOWED_PATHS`、`BIOINFO_READONLY_PATHS`、`BIOINFO_RUN_ID`，但不能作为唯一安全边界。

---

## 10. PermissionRequest / RuntimeApproval

执行器运行过程中如果需要扩大权限，WorkerAdapter 应把执行器原生请求归一化为 PermissionRequest。

```json
{
  "request_id": "perm_req_001",
  "run_id": "run_008",
  "executor": "codex",
  "request_type": "write_path",
  "target": "results/enrichment/run_008/cache/",
  "reason": "Worker needs a cache directory for intermediate enrichment tables.",
  "risk_level": "low",
  "policy_context": {
    "mode": "guarded",
    "allowed_paths": ["results/enrichment/run_008/"],
    "forbidden_paths": [".git/", "graph/"]
  },
  "created_at": "2026-05-18T04:30:00Z"
}
```

RuntimeApprovalDecision 示例：

```json
{
  "request_id": "perm_req_001",
  "decision": "auto_approved",
  "decided_by": "manager_ai",
  "user_required": false,
  "reason": "Requested path is under the current run result directory.",
  "created_at": "2026-05-18T04:30:10Z"
}
```

风险分级建议：

```text
low       当前 run 目录内的合理临时文件、已声明 input asset 的读取。
medium    安装依赖、联网下载公开数据库、读取未声明但位于项目 data/ 的文件。
high      扩大 writable roots、访问敏感路径、修改 readonly path。
dangerous 写 .git/、写 graph/、覆盖 valid asset、删除历史 run、上传客户数据。
```

Manager AI 可以自动审查低风险运行期权限，但不能用 RuntimeApproval 绕过 GraphPatch / PatchValidator。

---

## 11. WorkerEvent / RunEvent

WorkerAdapter 应把 ACP hook、CLI 输出、tool use、权限请求等归一化为 RunEvent，供前端 stream 和 run 审计使用。

```json
{
  "event_id": "evt_run_008_001",
  "run_id": "run_008",
  "card_id": "card_enrichment_group",
  "source": "executor",
  "event_type": "progress_note",
  "visibility": "bubble",
  "preview_id": "bubble_card_enrichment_group",
  "utterance_id": "utt_run_008_001",
  "stream_state": "delta",
  "message": "现在开始分析 DEG 了",
  "created_at": "2026-05-18T04:31:00Z"
}
```

流式输出规则：

```text
delta     同一个 utterance 的增量片段，前端更新当前气泡内容。
complete  当前 utterance 完成，前端保留这一条气泡直到下一条替换或超时消失。
snapshot  已组装好的完整消息，可直接替换当前气泡内容。
```

同一 Card 同一时间只显示一个 bubble visibility 的当前气泡。完整流式内容和历史事件保存在 run event stream / transcript 中。

参考 OpenClaw 的 preview streaming 模式，`preview_id` 表示同一个可更新预览面：后续事件更新同一个气泡，而不是追加新气泡。block/chunk 输出可以进入 transcript 或详情面板；Card bubble 只承载当前进度预览。

推荐事件类型：

```text
progress_note       用户可读进度，可显示为 Card 气泡。
assistant_message   执行器产出的自然语言消息，可按内容筛选后显示。
tool_use            工具/命令调用，默认进入状态行或折叠详情。
permission_request  进入 RuntimeApproval UI。
warning             显示在 Card 状态区和详情。
error               显示在 Card 状态区和详情，并影响 run 状态。
artifact_created    结果候选文件事件，等待 manifest/manager review。
```

`thinking` / internal reasoning 不进入默认 UI；如果执行器暴露相关事件，默认只落 transcript 或丢弃，不渲染为气泡。

---

## 12. Manifest

Worker Agent 执行完成后必须输出。

```json
{
  "run_id": "run_008",
  "status": "success",
  "summary": "GSEA and KEGG analysis completed successfully.",
  "inputs_used": [
    {
      "asset_id": "deg_table_v1",
      "path": "results/de/run_004/deg_table.tsv"
    }
  ],
  "created_assets": [
    {
      "role": "gsea_result_table",
      "type": "table",
      "path": "results/enrichment/run_008/gsea_result.tsv",
      "description": "GSEA enrichment result table."
    },
    {
      "role": "kegg_result_table",
      "type": "table",
      "path": "results/enrichment/run_008/kegg_result.tsv",
      "description": "KEGG pathway enrichment result table."
    },
    {
      "role": "gsea_plot",
      "type": "figure",
      "path": "results/enrichment/run_008/plots/gsea_top.png"
    }
  ],
  "commands_executed": [
    "Rscript scripts/generated/run_gsea_kegg.R --deg results/de/run_004/deg_table.tsv --out results/enrichment/run_008/"
  ],
  "metrics": {
    "gsea_significant_terms_fdr_0_25": 18,
    "kegg_significant_pathways_fdr_0_05": 7
  },
  "key_findings": [
    "GSEA suggests immune response pathways are enriched.",
    "KEGG identifies cytokine-cytokine receptor interaction among significant pathways."
  ],
  "recommended_graph_updates": [
    {
      "op": "create_asset",
      "asset_type": "gsea_result",
      "path": "results/enrichment/run_008/gsea_result.tsv"
    },
    {
      "op": "create_asset",
      "asset_type": "kegg_result",
      "path": "results/enrichment/run_008/kegg_result.tsv"
    }
  ],
  "warnings": []
}
```

---

## 13. ManagerReview

Manager AI 审核 manifest 后输出。

```json
{
  "run_id": "run_008",
  "decision": "accept",
  "summary": "结果完整，输出文件存在，指标合理，可作为下游报告依据。",
  "accepted_assets": [
    "gsea_result_v1",
    "kegg_result_v1",
    "gsea_plot_v1"
  ],
  "new_claims": [
    {
      "text": "免疫相关通路在 Treatment 组差异基因中显著富集。",
      "depends_on_assets": ["gsea_result_v1", "kegg_result_v1"]
    }
  ],
  "card_updates": [
    {
      "card_id": "card_enrichment_group",
      "status": "accepted",
      "key_findings": [
        "GSEA 发现 18 个显著富集通路。",
        "KEGG 发现 7 个显著通路。"
      ]
    }
  ],
  "downstream_effects": [],
  "needs_user_attention": false
}
```

---

## 14. ArtifactPointer

大文件用 pointer，不直接进 Git。

```json
{
  "artifact_id": "art_scrna_h5ad_001",
  "logical_name": "filtered_scrna_run_012",
  "asset_type": "h5ad",
  "format": "h5ad",
  "hash": {
    "algo": "sha256",
    "value": "abcdef123456..."
  },
  "quick_fingerprint": {
    "size_bytes": 4829310021,
    "mtime_ns": 1770000000000000000,
    "head_sha256_4mb": "aaa...",
    "tail_sha256_4mb": "bbb..."
  },
  "storage": {
    "local_uri": "artifact_store://sha256/ab/abcdef123456.h5ad",
    "remote_uri": null
  },
  "provenance": {
    "created_by_run": "run_012",
    "source_manifest": "runs/run_012/manifest.json",
    "created_at": "2026-05-18T04:30:00Z"
  },
  "biological_metadata": {
    "organism": "human",
    "assay": "scRNA-seq",
    "n_obs": 48321,
    "n_vars": 19842
  },
  "status": "valid"
}
```

---

## 15. ChatResponse

```json
{
  "message_id": "msg_001",
  "role": "manager_ai",
  "content": "我建议新增“功能富集分析”模块组，包含 GSEA 和 KEGG。",
  "proposal": {
    "proposal_id": "proposal_001",
    "title": "新增功能富集分析",
    "impact_summary": "新增 DEG 下游分析，不影响已有结果。",
    "patch_id": "patch_add_enrichment_001"
  },
  "actions": [
    {
      "label": "接受提案",
      "intent": "accept_proposal",
      "payload": {
        "proposal_id": "proposal_001"
      }
    },
    {
      "label": "修改提案",
      "intent": "modify_proposal",
      "payload": {
        "proposal_id": "proposal_001"
      }
    },
    {
      "label": "查看影响",
      "intent": "view_impact",
      "payload": {
        "proposal_id": "proposal_001"
      }
    }
  ]
}
```

Proposal 与 Patch 的绑定规则：

```json
{
  "proposal_id": "proposal_001",
  "patch_id": "patch_add_enrichment_001",
  "status": "pending",
  "consistency_warnings": []
}
```

Manager AI 修改 proposal 时，应同步修改 patch，或生成新 patch 并更新 `patch_id`。后端只做弱验证：检查 patch 是否存在、schema 是否有效、主要标题/模块类型是否大体匹配。弱验证不一致时记录 `consistency_warnings`，不因轻微摘要差异中断流程；只有 patch 缺失、schema 无效或危险 op 才阻断执行。

---

## 16. Schema Review 要点

AI 编程助手实现 schema 时请重点检查：

1. 所有 ID 字段必须稳定，不要每次刷新变化。
2. 所有 status 必须来自对应实体枚举，不要把 Card / Asset / Run / Manifest 状态混用。
3. GraphPatch ops 必须 allowlist。
4. Manifest 的 created_assets.path 必须在 allowed_paths 内。
5. Asset 的 hash 不能被普通 update_card patch 修改。
6. Card 是 UI projection，不是唯一语义真相。
7. 大文件必须通过 ArtifactPointer，不应直接纳入 Git。
8. Worker 输出只能推荐 graph updates，不能直接 accepted。
