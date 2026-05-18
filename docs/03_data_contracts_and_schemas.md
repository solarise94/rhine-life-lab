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
  "constraints": [
    "Do not overwrite existing valid assets.",
    "Write all outputs under results/enrichment/run_008/.",
    "Record commands in runs/run_008/commands.log.",
    "Write final manifest to runs/run_008/manifest.json."
  ],
  "worker_instructions": "You are a bioinformatics worker agent. You may write scripts and run commands. Produce a complete manifest."
}
```

---

## 10. Manifest

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

## 11. ManagerReview

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

## 12. ArtifactPointer

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

## 13. ChatResponse

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

## 14. Schema Review 要点

AI 编程助手实现 schema 时请重点检查：

1. 所有 ID 字段必须稳定，不要每次刷新变化。
2. 所有 status 必须来自对应实体枚举，不要把 Card / Asset / Run / Manifest 状态混用。
3. GraphPatch ops 必须 allowlist。
4. Manifest 的 created_assets.path 必须在 allowed_paths 内。
5. Asset 的 hash 不能被普通 update_card patch 修改。
6. Card 是 UI projection，不是唯一语义真相。
7. 大文件必须通过 ArtifactPointer，不应直接纳入 Git。
8. Worker 输出只能推荐 graph updates，不能直接 accepted。
