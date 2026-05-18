from pathlib import Path
import shutil
import tempfile
import time
import unittest

from app.core.config import get_settings
from app.models.chat import ChatRequest
from app.models.patches import GraphPatch
from app.services.manifest_service import ManifestService
from app.services.manager_planner import DeepSeekManagerPlanner, ManagerPlanDraft, ManagerPlanningError
from app.services.manager_service import ManagerService
from app.services.patch_apply import PatchApplyService
from app.services.patch_validator import PatchValidator
from app.services.project_service import ProjectService
from app.services.runtime_approval_service import RuntimeApprovalService
from app.services.result_asset_service import ResultAssetService
from app.services.worker_service import WorkerService


class StubPlanner:
    def __init__(self) -> None:
        self.turns = 0

    def agent_turn(self, messages: list[dict], tools: list[dict]) -> dict:
        self.turns += 1
        if self.turns == 1:
            return {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_planner",
                        "name": "planner_patch",
                        "input": {"instruction": "客户想增加免疫浸润分析模块"},
                    }
                ]
            }
        return {"content": [{"type": "text", "text": "我已生成一个可审核的免疫浸润分析 proposal。"}]}

    def plan(self, snapshot: dict, chat_request: ChatRequest, extra_context: dict | None = None) -> ManagerPlanDraft:
        return ManagerPlanDraft.model_validate(
            {
                "response_type": "proposal",
                "message": "我建议新增免疫浸润分析模块。",
                "title": "新增免疫浸润分析",
                "summary": "我建议新增“免疫浸润分析”模块，依赖当前已接受的 DEG 结果。",
                "impact_summary": "会新增一个下游模块和卡片，不影响现有差异表达结果。",
                "patch_type": "add_module",
                "reason": chat_request.message,
                "ops": [
                    {
                        "op": "create_module",
                        "payload": {
                            "module_id": "module_immune_infiltration_test",
                            "title": "免疫浸润分析",
                            "status": "planned",
                            "summary": "基于已接受的 DEG 结果进行免疫浸润分析。",
                            "depends_on_assets": ["deg_table_v1"],
                            "expected_outputs": ["immune_score_table", "immune_heatmap"],
                            "linked_cards": ["card_immune_infiltration_test"],
                        },
                    },
                    {
                        "op": "create_card",
                        "payload": {
                            "card_id": "card_immune_infiltration_test",
                            "card_type": "module",
                            "title": "免疫浸润分析",
                            "status": "planned",
                            "summary": "基于差异表达结果新增免疫浸润分析模块。",
                            "why": "用于解释免疫相关微环境变化。",
                            "inputs": [],
                            "outputs": [],
                            "key_findings": [],
                            "manager_review": "待执行。",
                            "next_actions": ["开始执行", "修改方案", "取消模块"],
                            "linked_modules": ["module_immune_infiltration_test"],
                            "linked_runs": [],
                            "linked_assets": ["deg_table_v1"],
                        },
                    },
                ],
            }
        )


class GoLikePlanner:
    def __init__(self) -> None:
        self.turns = 0

    def agent_turn(self, messages: list[dict], tools: list[dict]) -> dict:
        self.turns += 1
        if self.turns == 1:
            return {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_planner",
                        "name": "planner_patch",
                        "input": {"instruction": "增加 GO 富集分析"},
                    }
                ]
            }
        return {"content": [{"type": "text", "text": "我已生成一个 GO 富集分析 proposal。"}]}

    def plan(self, snapshot: dict, chat_request: ChatRequest, extra_context: dict | None = None) -> ManagerPlanDraft:
        return ManagerPlanDraft.model_validate(
            {
                "response_type": "proposal",
                "message": "建议在功能富集分析下新增 GO 子模块。",
                "title": "新增 GO 富集分析子模块",
                "summary": "在现有功能富集分析模块组下新增 GO 富集分析子模块。",
                "impact_summary": "会新增 GO 富集分析 card，并挂到现有模块组下。",
                "patch_type": "add_module",
                "reason": chat_request.message,
                "ops": [
                    {
                        "op": "create_module",
                        "payload": {
                            "module_id": "module_go_enrichment",
                            "title": "GO 富集分析",
                            "status": "planned",
                            "summary": "基于 DEG 结果进行 GO 富集分析。",
                            "depends_on_assets": ["deg_table_v1", "ranked_gene_list_v1"],
                            "expected_outputs": ["go_enrichment_table", "go_dot_plot"],
                        },
                    },
                    {
                        "op": "add_submodule",
                        "payload": {
                            "parent_module_id": "module_group_enrichment",
                            "child_module_id": "module_go_enrichment",
                        },
                    },
                    {
                        "op": "create_card",
                        "payload": {
                            "card_id": "card_go_enrichment",
                            "card_type": "module",
                            "title": "GO 富集分析",
                            "status": "proposed",
                            "summary": "基于 DEG 结果进行 GO 富集分析。",
                            "linked_modules": ["module_go_enrichment"],
                            "linked_assets": ["deg_table_v1", "ranked_gene_list_v1"],
                        },
                    },
                ],
            }
        )


class ModifyLikePlanner:
    def __init__(self) -> None:
        self.turns = 0

    def agent_turn(self, messages: list[dict], tools: list[dict]) -> dict:
        self.turns += 1
        if self.turns == 1:
            return {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_planner",
                        "name": "planner_patch",
                        "input": {"instruction": "强调免疫浸润分析依赖 DEG"},
                    }
                ]
            }
        return {"content": [{"type": "text", "text": "我已生成一个修改免疫浸润说明的 proposal。"}]}

    def plan(self, snapshot: dict, chat_request: ChatRequest, extra_context: dict | None = None) -> ManagerPlanDraft:
        return ManagerPlanDraft.model_validate(
            {
                "response_type": "proposal",
                "message": "更新免疫浸润分析描述，强调 DEG 依赖。",
                "title": "修改免疫浸润分析提案",
                "summary": "更新免疫浸润分析 card 与 module 的描述，强调 DEG 依赖。",
                "impact_summary": "会更新现有 card 与 module 的说明文案。",
                "patch_type": "update_card",
                "reason": chat_request.message,
                "ops": [
                    {
                        "op": "update_card",
                        "payload": {
                            "card_id": "card_immune_module",
                            "title": "免疫浸润分析（依赖 DEG 结果）",
                            "summary": "该模块严格依赖上游 DEG 结果。",
                            "linked_assets": ["deg_table_v1"],
                            "next_actions": ["接受提案", "修改提案", "查看影响"],
                        },
                    },
                    {
                        "op": "update_card",
                        "payload": {
                            "card_id": "module_immune_infiltration",
                            "summary": "严格依赖上游差异表达分析模块的 DEG 结果表。",
                            "depends_on_assets": ["deg_table_v1"],
                            "expected_outputs": ["immune_score_table", "immune_heatmap"],
                        },
                    },
                ],
            }
        )


class FailingPlanner:
    def agent_turn(self, messages: list[dict], tools: list[dict]) -> dict:
        raise ManagerPlanningError("agent llm failed")

    def plan(self, snapshot: dict, chat_request: ChatRequest, extra_context: dict | None = None) -> ManagerPlanDraft:
        raise ManagerPlanningError("planner failed")


class ToolDecisionPlanner:
    def __init__(self) -> None:
        self.turns = 0

    def agent_turn(self, messages: list[dict], tools: list[dict]) -> dict:
        self.turns += 1
        if self.turns == 1:
            return {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_add",
                        "name": "draft_add_module",
                        "input": {"instruction": "请新增一个 GO 富集分析模块并生成对应 card"},
                    }
                ]
            }
        return {"content": [{"type": "text", "text": "我已通过工具生成 GO 富集分析 proposal。"}]}

    def plan(self, snapshot: dict, chat_request: ChatRequest, extra_context: dict | None = None) -> ManagerPlanDraft:
        raise AssertionError("Planner should not be called when harness selected a backend tool")


class AnswerOnlyPlanner:
    def agent_turn(self, messages: list[dict], tools: list[dict]) -> dict:
        return {"content": [{"type": "text", "text": "这是 DeepSeek 普通聊天回复。"}]}

    def plan(self, snapshot: dict, chat_request: ChatRequest, extra_context: dict | None = None) -> ManagerPlanDraft:
        raise AssertionError("Planner should not be called for ordinary chat")


class ManagerFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="blueprint-re-test-")
        settings = get_settings()
        settings.data_root = Path(self.tmpdir)
        self.project_service = ProjectService()
        self.project_service.create_project(
            project_id="test-project",
            name="Test Project",
            current_goal="Test flow",
            seed_demo=True,
        )
        self.manager = ManagerService(self.project_service, planner=StubPlanner())
        self.validator = PatchValidator(self.project_service)
        self.apply = PatchApplyService(self.project_service, self.validator)
        self.manifest_service = ManifestService(self.project_service)
        self.runtime_approval_service = RuntimeApprovalService(self.project_service)
        self.result_asset_service = ResultAssetService(self.project_service)
        self.worker = WorkerService(self.project_service, self.manifest_service, self.runtime_approval_service)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_chat_accept_patch(self) -> None:
        response = self.manager.chat("test-project", ChatRequest(message="客户想增加免疫浸润分析模块"))
        self.assertIsNotNone(response.proposal)
        proposal = self.manager.accept_proposal("test-project", response.proposal.proposal_id)
        patch_payload = self.project_service.graph_store("test-project").load_patch(proposal.patch_id)
        result = self.apply.apply_patch("test-project", GraphPatch.model_validate(patch_payload))
        snapshot = self.project_service.get_project_snapshot("test-project")
        self.assertTrue(result.commit_hash)
        self.assertTrue(any(card.title == "免疫浸润分析" and card.status == "planned" for card in snapshot["cards"]))

    def test_run_and_review(self) -> None:
        run = self.worker.start_run("test-project", "card_enrichment_group")
        self._wait_for_run("test-project", run["run_id"])
        snapshot = self.project_service.get_project_snapshot("test-project")
        card = next(item for item in snapshot["cards"] if item.card_id == "card_enrichment_group")
        self.assertEqual(card.status, "needs_review")
        self.worker.review_run("test-project", run["run_id"], accept=True)
        snapshot = self.project_service.get_project_snapshot("test-project")
        card = next(item for item in snapshot["cards"] if item.card_id == "card_enrichment_group")
        self.assertEqual(card.status, "accepted")
        self.assertTrue(any(asset.created_by_run == run["run_id"] for asset in snapshot["graph"].assets))
        preview_asset = next(asset for asset in snapshot["graph"].assets if asset.created_by_run == run["run_id"] and asset.asset_type == "figure")
        detail = self.result_asset_service.get_asset_detail("test-project", preview_asset.asset_id)
        self.assertEqual(detail["preview"]["kind"], "image")

    def test_modify_proposal(self) -> None:
        response = self.manager.chat("test-project", ChatRequest(message="客户想增加免疫浸润分析模块"))
        proposal = self.manager.modify_proposal("test-project", response.proposal.proposal_id, ChatRequest(message="把说明改得更强调 DEG 依赖"))
        self.assertEqual(proposal.proposal_id, response.proposal.proposal_id)
        self.assertTrue(proposal.patch_id.startswith("patch_"))

    def test_accept_go_like_proposal_creates_card(self) -> None:
        manager = ManagerService(self.project_service, planner=GoLikePlanner())
        response = manager.chat("test-project", ChatRequest(message="增加 GO 富集分析"))
        proposal = manager.get_proposal("test-project", response.proposal.proposal_id)
        patch_payload = self.project_service.graph_store("test-project").load_patch(proposal.patch_id)
        result = self.apply.apply_patch("test-project", GraphPatch.model_validate(patch_payload))
        manager.mark_proposal_status("test-project", proposal.proposal_id, "accepted")
        snapshot = self.project_service.get_project_snapshot("test-project")
        self.assertTrue(result.commit_hash)
        self.assertTrue(any(card.card_id == "card_go_enrichment" for card in snapshot["cards"]))
        group = next(module for module in snapshot["graph"].modules if module.module_id == "module_group_enrichment")
        self.assertTrue(any(item.module_id == "module_go_enrichment" for item in group.submodules))

    def test_accept_modify_like_proposal_updates_existing_card_and_module(self) -> None:
        manager = ManagerService(self.project_service, planner=ModifyLikePlanner())
        response = manager.chat("test-project", ChatRequest(message="强调免疫浸润分析依赖 DEG"))
        proposal = manager.get_proposal("test-project", response.proposal.proposal_id)
        patch_payload = self.project_service.graph_store("test-project").load_patch(proposal.patch_id)
        self.apply.apply_patch("test-project", GraphPatch.model_validate(patch_payload))
        manager.mark_proposal_status("test-project", proposal.proposal_id, "accepted")
        snapshot = self.project_service.get_project_snapshot("test-project")
        card = next(item for item in snapshot["cards"] if item.card_id == "card_immune_module")
        module = next(item for item in snapshot["graph"].modules if item.module_id == "module_immune_infiltration")
        self.assertIn("DEG", card.title)
        self.assertIn("DEG", card.summary)
        self.assertIn("DEG", module.summary)

    def test_plain_chat_calls_llm_answer_and_does_not_create_proposal(self) -> None:
        manager = ManagerService(self.project_service, planner=AnswerOnlyPlanner())
        before = self.project_service.graph_store("test-project").load_proposals()
        response = manager.chat("test-project", ChatRequest(message="你好，先帮我解释一下现在项目里有哪些分析模块"))
        after = self.project_service.graph_store("test-project").load_proposals()
        self.assertIsNone(response.proposal)
        self.assertEqual(len(before), len(after))
        self.assertEqual("这是 DeepSeek 普通聊天回复。", response.message)

    def test_plain_chat_llm_error_is_not_hidden(self) -> None:
        manager = ManagerService(self.project_service, planner=FailingPlanner())
        with self.assertRaises(ManagerPlanningError):
            manager.chat("test-project", ChatRequest(message="hi"))

    def test_analysis_suggestion_question_stays_plain_chat(self) -> None:
        manager = ManagerService(self.project_service, planner=AnswerOnlyPlanner())
        response = manager.chat("test-project", ChatRequest(message="帮我看看我现在的分析流程，有没有还可以补充的"))
        self.assertIsNone(response.proposal)
        self.assertEqual("这是 DeepSeek 普通聊天回复。", response.message)

    def test_tool_layer_go_request_creates_acceptible_proposal_and_card(self) -> None:
        manager = ManagerService(self.project_service, planner=ToolDecisionPlanner())
        response = manager.chat("test-project", ChatRequest(message="请新增一个 GO 富集分析模块并生成对应 card"))
        self.assertIsNotNone(response.proposal)
        self.assertIn("GO 富集分析", response.proposal.title)
        patch_payload = self.project_service.graph_store("test-project").load_patch(response.proposal.patch_id)
        result = self.apply.apply_patch("test-project", GraphPatch.model_validate(patch_payload))
        manager.mark_proposal_status("test-project", response.proposal.proposal_id, "accepted")
        snapshot = self.project_service.get_project_snapshot("test-project")
        self.assertTrue(result.commit_hash)
        self.assertTrue(any(card.card_id == "card_go_enrichment" for card in snapshot["cards"]))
        self.assertTrue(any(module.module_id == "module_go_enrichment" for module in snapshot["graph"].modules))

    def test_delete_and_restore_module_tools_create_applyable_proposals(self) -> None:
        manager = ManagerService(self.project_service, planner=AnswerOnlyPlanner())
        delete_response = manager.blueprint_tools.delete_module_proposal(
            "test-project",
            {"module_id": "module_immune_infiltration", "reason": "用户要删除免疫浸润分析"},
        )
        self.assertIsNotNone(delete_response.proposal)
        patch_payload = self.project_service.graph_store("test-project").load_patch(delete_response.proposal.patch_id)
        self.apply.apply_patch("test-project", GraphPatch.model_validate(patch_payload))
        manager.mark_proposal_status("test-project", delete_response.proposal.proposal_id, "accepted")
        snapshot = self.project_service.get_project_snapshot("test-project")
        module = next(item for item in snapshot["graph"].modules if item.module_id == "module_immune_infiltration")
        card = next(item for item in snapshot["cards"] if item.card_id == "card_immune_module")
        self.assertEqual(module.status, "cancelled")
        self.assertEqual(card.status, "cancelled")

        restore_response = manager.blueprint_tools.restore_module_proposal(
            "test-project",
            {"module_id": "module_immune_infiltration", "reason": "用户要恢复免疫浸润分析"},
        )
        patch_payload = self.project_service.graph_store("test-project").load_patch(restore_response.proposal.patch_id)
        self.apply.apply_patch("test-project", GraphPatch.model_validate(patch_payload))
        manager.mark_proposal_status("test-project", restore_response.proposal.proposal_id, "accepted")
        snapshot = self.project_service.get_project_snapshot("test-project")
        module = next(item for item in snapshot["graph"].modules if item.module_id == "module_immune_infiltration")
        card = next(item for item in snapshot["cards"] if item.card_id == "card_immune_module")
        self.assertEqual(module.status, "planned")
        self.assertEqual(card.status, "planned")

    def test_read_result_asset_tool_returns_preview(self) -> None:
        manager = ManagerService(self.project_service, planner=AnswerOnlyPlanner())
        detail = manager.blueprint_tools.read_result_asset("test-project", "deg_table_v1")
        self.assertEqual(detail["asset"].asset_id, "deg_table_v1")
        self.assertIn("preview", detail)

    def _wait_for_run(self, project_id: str, run_id: str) -> None:
        deadline = time.time() + 5
        while time.time() < deadline:
            snapshot = self.project_service.get_project_snapshot(project_id)
            run = next(item for item in snapshot["graph"].runs if item.run_id == run_id)
            if run.status in {"success", "failed"}:
                return
            time.sleep(0.05)
        self.fail(f"run {run_id} did not finish in time")


class ManagerPlannerCompatibilityTest(unittest.TestCase):
    def test_resolve_legacy_reasoner_model_for_tool_use(self) -> None:
        self.assertEqual(DeepSeekManagerPlanner.resolve_tool_model("deepseek-reasoner"), "deepseek-v4-pro")

    def test_http_error_message_includes_tool_use_guidance(self) -> None:
        message = DeepSeekManagerPlanner._build_http_error_message(
            status_code=400,
            detail='{"error":{"message":"deepseek-reasoner does not support this tool_choice"}}',
            configured_model="deepseek-reasoner",
            resolved_model="deepseek-v4-pro",
        )
        self.assertIn("deepseek-v4-pro", message)
        self.assertIn("tool-use requests require a DeepSeek v4 model", message)


if __name__ == "__main__":
    unittest.main()
