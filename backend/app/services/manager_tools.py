from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
import re

from app.models.cards import Card
from app.models.graph import Asset, Module
from app.models.patches import PatchOp
from app.models.chat import ChatRequest
from app.services.manager_intent import ManagerIntent
from app.services.manager_planner import ManagerPlanDraft


@dataclass(frozen=True)
class AnalysisSpec:
    key: str
    title: str
    module_id: str
    card_id: str
    summary: str
    expected_outputs: list[str]
    required_asset_ids: list[str]
    parent_group_hint: str | None = None


class ManagerToolLayer:
    """Backend-owned tools that produce valid blueprint patch drafts."""

    SPECS = (
        AnalysisSpec(
            key="go",
            title="GO 富集分析",
            module_id="module_go_enrichment",
            card_id="card_go_enrichment",
            summary="基于差异表达结果进行 GO 富集分析。",
            expected_outputs=["go_enrichment_table", "go_dot_plot"],
            required_asset_ids=["deg_table_v1", "ranked_gene_list_v1"],
            parent_group_hint="富集",
        ),
        AnalysisSpec(
            key="gsea",
            title="GSEA 分析",
            module_id="module_gsea",
            card_id="card_gsea",
            summary="基于排序基因列表进行 GSEA 分析。",
            expected_outputs=["gsea_result", "gsea_enrichment_plot"],
            required_asset_ids=["ranked_gene_list_v1", "deg_table_v1"],
            parent_group_hint="富集",
        ),
        AnalysisSpec(
            key="kegg",
            title="KEGG 富集",
            module_id="module_kegg",
            card_id="card_kegg_enrichment",
            summary="基于差异表达结果进行 KEGG 通路富集分析。",
            expected_outputs=["kegg_enrichment_table", "kegg_pathway_plot"],
            required_asset_ids=["deg_table_v1", "ranked_gene_list_v1"],
            parent_group_hint="富集",
        ),
        AnalysisSpec(
            key="immune",
            title="免疫浸润分析",
            module_id="module_immune_infiltration",
            card_id="card_immune_module",
            summary="基于差异表达结果进行免疫浸润分析。",
            expected_outputs=["immune_score_table", "immune_heatmap"],
            required_asset_ids=["deg_table_v1"],
        ),
        AnalysisSpec(
            key="pca",
            title="PCA 分析",
            module_id="module_pca",
            card_id="card_pca",
            summary="基于表达矩阵进行 PCA 降维和样本结构检查。",
            expected_outputs=["pca_scores_table", "pca_plot"],
            required_asset_ids=["count_matrix_v1", "sample_metadata_v1"],
        ),
        AnalysisSpec(
            key="wgcna",
            title="WGCNA 共表达网络分析",
            module_id="module_wgcna",
            card_id="card_wgcna",
            summary="基于表达矩阵构建共表达网络并识别模块。",
            expected_outputs=["wgcna_module_table", "wgcna_network_plot"],
            required_asset_ids=["count_matrix_v1", "sample_metadata_v1"],
        ),
    )

    def try_build_plan(self, snapshot: dict, request: ChatRequest, intent: ManagerIntent) -> ManagerPlanDraft | None:
        if intent.kind != "mutation":
            return None
        if intent.action == "add_module":
            return self._build_add_module_plan(snapshot, request)
        if intent.action == "update_existing":
            return self._build_update_existing_plan(snapshot, request)
        return None

    def answer(self, snapshot: dict, request: ChatRequest) -> str:
        selected_card = self._selected_card(snapshot, request)
        if selected_card:
            return (
                f"当前选中的 card 是「{selected_card.title}」，状态为 {selected_card.status}。"
                f"{selected_card.summary} 关联模块：{', '.join(selected_card.linked_modules) or '无'}；"
                f"关联资产：{', '.join(selected_card.linked_assets) or '无'}。"
            )

        modules = snapshot["graph"].modules
        cards = snapshot["cards"]
        proposals = [item for item in snapshot["proposals"] if item.status == "proposed"]
        module_lines = [f"{module.title}({module.status})" for module in modules[:6]]
        card_counts: dict[str, int] = {}
        for card in cards:
            card_counts[card.status] = card_counts.get(card.status, 0) + 1
        counts = ", ".join(f"{status}: {count}" for status, count in sorted(card_counts.items())) or "暂无 card"
        open_proposals = f"当前有 {len(proposals)} 个待处理 proposal。" if proposals else "当前没有待处理 proposal。"
        return (
            f"项目目标：{snapshot['project'].current_goal}。"
            f"现有模块：{'; '.join(module_lines) or '暂无模块'}。"
            f"Card 状态分布：{counts}。{open_proposals} "
            "如果要调整蓝图，请明确说“新增/修改/删除/重跑”，我会通过后端工具生成可审核的 proposal。"
        )

    def _build_add_module_plan(self, snapshot: dict, request: ChatRequest) -> ManagerPlanDraft | None:
        spec = self._resolve_spec(request.message)
        if not spec:
            return None
        existing_module = self._find_module_by_title_or_id(snapshot, spec.title, spec.module_id)
        if existing_module:
            return self._build_promote_existing_module_plan(snapshot, request, spec, existing_module)

        modules = snapshot["graph"].modules
        cards = snapshot["cards"]
        assets = {asset.asset_id: asset for asset in snapshot["graph"].assets}
        existing_module_ids = self._module_ids(snapshot)
        existing_card_ids = {card.card_id for card in cards}
        module_id = self._unique_id(spec.module_id, existing_module_ids)
        card_id = self._unique_id(spec.card_id, existing_card_ids)
        dependency_assets = [asset_id for asset_id in spec.required_asset_ids if asset_id in assets]
        output_refs = [{"label": self._output_label(output), "status": "planned"} for output in spec.expected_outputs]
        input_refs = [self._asset_ref(assets[asset_id]) for asset_id in dependency_assets]

        ops = [
            PatchOp(
                op="create_module",
                payload={
                    "module_id": module_id,
                    "title": spec.title,
                    "status": "planned",
                    "summary": spec.summary,
                    "depends_on_assets": dependency_assets,
                    "expected_outputs": spec.expected_outputs,
                    "linked_cards": [card_id],
                },
            )
        ]
        parent_group = self._find_parent_group(snapshot, spec)
        if parent_group:
            ops.append(
                PatchOp(
                    op="add_submodule",
                    payload={
                        "parent_module_id": parent_group.module_id,
                        "module_id": module_id,
                        "title": spec.title,
                        "status": "planned",
                    },
                )
            )
        ops.append(
            PatchOp(
                op="create_card",
                payload={
                    "card_id": card_id,
                    "card_type": "module",
                    "title": spec.title,
                    "status": "planned",
                    "summary": spec.summary,
                    "why": "用户要求将该分析纳入当前蓝图，由 Manager 工具层生成可审核变更。",
                    "inputs": input_refs,
                    "outputs": output_refs,
                    "key_findings": [],
                    "manager_review": "待执行。接受 proposal 后会写入蓝图。",
                    "next_actions": ["开始执行", "修改方案", "取消模块"],
                    "linked_modules": [module_id],
                    "linked_runs": [],
                    "linked_assets": dependency_assets,
                },
            )
        )

        return ManagerPlanDraft(
            response_type="proposal",
            message=f"我会新增「{spec.title}」模块，并生成对应 card。",
            title=f"新增 {spec.title}",
            summary=f"新增「{spec.title}」模块和对应 card。",
            impact_summary=self._impact_summary(spec, dependency_assets, parent_group),
            patch_type="add_module",
            reason=request.message,
            ops=ops,
        )

    def _build_promote_existing_module_plan(
        self,
        snapshot: dict,
        request: ChatRequest,
        spec: AnalysisSpec,
        module: Module,
    ) -> ManagerPlanDraft:
        card = self._find_card_for_module(snapshot, module.module_id) or self._find_card_by_title(snapshot, spec.title)
        dependency_assets = self._existing_asset_ids(snapshot, spec.required_asset_ids)
        ops = [
            PatchOp(
                op="update_module",
                payload={
                    "module_id": module.module_id,
                    "status": "planned",
                    "summary": spec.summary,
                    "depends_on_assets": dependency_assets or module.depends_on_assets,
                    "expected_outputs": spec.expected_outputs or module.expected_outputs,
                    "linked_cards": [card.card_id] if card else module.linked_cards,
                },
            )
        ]
        if card:
            ops.append(
                PatchOp(
                    op="update_card",
                    payload={
                        "card_id": card.card_id,
                        "status": "planned",
                        "summary": spec.summary,
                        "manager_review": "用户要求纳入蓝图，接受 proposal 后该 card 会进入 planned 状态。",
                        "next_actions": ["开始执行", "修改方案", "取消模块"],
                        "linked_modules": [module.module_id],
                        "linked_assets": dependency_assets or card.linked_assets,
                    },
                )
            )
        return ManagerPlanDraft(
            response_type="proposal",
            message=f"蓝图中已存在「{spec.title}」，我会生成 proposal 将它确认纳入计划。",
            title=f"确认纳入 {spec.title}",
            summary=f"复用现有「{spec.title}」模块/card，并将状态更新为 planned。",
            impact_summary="不会创建重复模块，只会更新现有模块/card 的状态和说明。",
            patch_type="update_card",
            reason=request.message,
            ops=ops,
        )

    def _build_update_existing_plan(self, snapshot: dict, request: ChatRequest) -> ManagerPlanDraft | None:
        target_card = self._selected_card(snapshot, request)
        spec = self._resolve_spec(request.message)
        target_module = self._find_module_by_title_or_id(snapshot, spec.title, spec.module_id) if spec else None
        if not target_card and target_module:
            target_card = self._find_card_for_module(snapshot, target_module.module_id)
        if not target_card and spec:
            target_card = self._find_card_by_title(snapshot, spec.title)
        if not target_card and not target_module:
            return None
        if not target_module and target_card and target_card.linked_modules:
            target_module = self._find_module_by_id(snapshot, target_card.linked_modules[0])

        note = request.message.strip()
        summary = self._updated_summary(target_card, target_module, note)
        ops: list[PatchOp] = []
        if target_module:
            payload = {
                "module_id": target_module.module_id,
                "summary": summary,
            }
            if "deg" in note.lower() and "deg_table_v1" in self._asset_ids(snapshot):
                payload["depends_on_assets"] = self._merge_unique(target_module.depends_on_assets, ["deg_table_v1"])
            ops.append(PatchOp(op="update_module", payload=payload))
        if target_card:
            linked_assets = list(target_card.linked_assets)
            if "deg" in note.lower() and "deg_table_v1" in self._asset_ids(snapshot):
                linked_assets = self._merge_unique(linked_assets, ["deg_table_v1"])
            card_payload = {
                "card_id": target_card.card_id,
                "summary": summary,
                "manager_review": f"按用户要求更新说明：{note}",
                "linked_assets": linked_assets,
            }
            if "deg" in note.lower() and "DEG" not in target_card.title:
                card_payload["title"] = f"{target_card.title}（依赖 DEG 结果）"
            ops.append(PatchOp(op="update_card", payload=card_payload))
        if not ops:
            return None

        title = target_card.title if target_card else target_module.title
        return ManagerPlanDraft(
            response_type="proposal",
            message=f"我会更新「{title}」的蓝图说明。",
            title=f"修改 {title}",
            summary=f"更新「{title}」的说明和关联上下文。",
            impact_summary="只修改现有模块/card 的元数据，不创建新模块或执行结果。",
            patch_type="update_card",
            reason=request.message,
            ops=ops,
        )

    def _resolve_spec(self, message: str) -> AnalysisSpec | None:
        lowered = message.lower()
        if "go" in lowered or "gene ontology" in lowered or "go 富集" in lowered:
            return self.SPECS[0]
        if "gsea" in lowered:
            return self.SPECS[1]
        if "kegg" in lowered:
            return self.SPECS[2]
        if "免疫" in message or "immune" in lowered:
            return self.SPECS[3]
        if "pca" in lowered:
            return self.SPECS[4]
        if "wgcna" in lowered:
            return self.SPECS[5]
        title = self._extract_custom_title(message)
        if not title:
            return None
        slug = self._slug(title)
        return AnalysisSpec(
            key=slug,
            title=title,
            module_id=f"module_{slug}",
            card_id=f"card_{slug}",
            summary=f"按用户要求新增「{title}」模块。",
            expected_outputs=[f"{slug}_result"],
            required_asset_ids=[],
        )

    @staticmethod
    def _extract_custom_title(message: str) -> str | None:
        match = re.search(r"(?:新增|增加|添加|新建|创建)(?:一个)?(.{2,40}?)(?:模块|card|卡片)", message, re.IGNORECASE)
        if not match:
            return None
        title = match.group(1).strip(" ：:，,。.")
        if not title:
            return None
        if not title.endswith("分析") and "分析" not in title:
            title = f"{title}分析"
        return title

    @staticmethod
    def _slug(title: str) -> str:
        ascii_tokens = re.findall(r"[a-zA-Z0-9]+", title.lower())
        if ascii_tokens:
            return "_".join(ascii_tokens)
        digest = sha1(title.encode("utf-8")).hexdigest()[:8]
        return f"custom_{digest}"

    @staticmethod
    def _unique_id(preferred: str, existing: set[str]) -> str:
        if preferred not in existing:
            return preferred
        index = 2
        while f"{preferred}_{index}" in existing:
            index += 1
        return f"{preferred}_{index}"

    @staticmethod
    def _output_label(output: str) -> str:
        return output.replace("_", " ")

    @staticmethod
    def _asset_ref(asset: Asset) -> dict:
        return {"label": asset.title, "asset_id": asset.asset_id}

    @staticmethod
    def _merge_unique(current: list[str], additions: list[str]) -> list[str]:
        merged = list(current)
        for item in additions:
            if item not in merged:
                merged.append(item)
        return merged

    @staticmethod
    def _updated_summary(card: Card | None, module: Module | None, note: str) -> str:
        base = card.summary if card else module.summary
        if "deg" in note.lower():
            return f"{base} 重点强调：该分析依赖上游 DEG 结果。"
        return f"{base} 更新说明：{note}"

    @staticmethod
    def _impact_summary(spec: AnalysisSpec, dependency_assets: list[str], parent_group: Module | None) -> str:
        deps = f"依赖资产：{', '.join(dependency_assets)}。" if dependency_assets else "暂未绑定具体输入资产。"
        parent = f"会挂载到「{parent_group.title}」模块组。" if parent_group else "不会改变现有模块层级。"
        return f"会新增一个 {spec.title} 模块和一个 module card。{deps}{parent}"

    @staticmethod
    def _selected_card(snapshot: dict, request: ChatRequest) -> Card | None:
        selected_card_id = request.context.selected_card_id
        if not selected_card_id:
            return None
        return next((card for card in snapshot["cards"] if card.card_id == selected_card_id), None)

    @staticmethod
    def _module_ids(snapshot: dict) -> set[str]:
        module_ids = {module.module_id for module in snapshot["graph"].modules}
        for module in snapshot["graph"].modules:
            module_ids.update(item.module_id for item in module.submodules)
        return module_ids

    @staticmethod
    def _asset_ids(snapshot: dict) -> set[str]:
        return {asset.asset_id for asset in snapshot["graph"].assets}

    def _existing_asset_ids(self, snapshot: dict, candidates: list[str]) -> list[str]:
        asset_ids = self._asset_ids(snapshot)
        return [asset_id for asset_id in candidates if asset_id in asset_ids]

    @staticmethod
    def _find_module_by_id(snapshot: dict, module_id: str) -> Module | None:
        return next((module for module in snapshot["graph"].modules if module.module_id == module_id), None)

    def _find_module_by_title_or_id(self, snapshot: dict, title: str, module_id: str) -> Module | None:
        lowered_title = title.lower()
        return next(
            (
                module
                for module in snapshot["graph"].modules
                if module.module_id == module_id or module.title.lower() == lowered_title
            ),
            None,
        )

    @staticmethod
    def _find_card_by_title(snapshot: dict, title: str) -> Card | None:
        lowered_title = title.lower()
        return next((card for card in snapshot["cards"] if card.title.lower() == lowered_title), None)

    @staticmethod
    def _find_card_for_module(snapshot: dict, module_id: str) -> Card | None:
        return next((card for card in snapshot["cards"] if module_id in card.linked_modules), None)

    @staticmethod
    def _find_parent_group(snapshot: dict, spec: AnalysisSpec) -> Module | None:
        if not spec.parent_group_hint:
            return None
        return next(
            (
                module
                for module in snapshot["graph"].modules
                if module.type == "module_group" and spec.parent_group_hint in module.title
            ),
            None,
        )
