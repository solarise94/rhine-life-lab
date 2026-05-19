from pathlib import Path
import shutil
import tempfile
import time
import unittest

from app.core.config import get_settings
from app.models.chat import ChatRequest, ChatSessionMessage
from app.models.graph import Asset, GraphState
from app.models.patches import GraphPatch, ValidationResult
from app.services.chat_session_service import ChatSessionService
from app.services.manifest_service import ManifestService
from app.services.manager_planner import DeepSeekManagerPlanner, ManagerPlanDraft, ManagerPlanningError
from app.services.manager_service import ManagerService
from app.services.flow_service import FlowService
from app.services.patch_apply import PatchApplyService
from app.services.patch_validator import PatchValidator
from app.services.project_file_service import ProjectFileService
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
        self.project_file_service = ProjectFileService(self.project_service)
        self.chat_session_service = ChatSessionService(self.project_service)
        self.worker = WorkerService(self.project_service, self.manifest_service, self.runtime_approval_service)
        self.flow_service = FlowService(self.project_service)

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

    def test_project_files_include_uploads_and_execution_files(self) -> None:
        project_root = self.project_service.project_path("test-project")
        upload_path = project_root / "data" / "uploads" / "upload_demo_notes.txt"
        upload_path.parent.mkdir(parents=True, exist_ok=True)
        upload_path.write_text("demo notes\n", encoding="utf-8")

        graph_store = self.project_service.graph_store("test-project")
        graph = graph_store.load_graph()
        graph.assets.append(
            Asset(
                asset_id="upload_demo_notes",
                asset_type="text",
                title="demo_notes.txt",
                status="candidate",
                path="data/uploads/upload_demo_notes.txt",
                summary="Uploaded notes",
                metadata={"source": "manager_chat_upload"},
            )
        )
        graph_store.save_graph(graph)

        run = self.worker.start_run("test-project", "card_enrichment_group")
        self._wait_for_run("test-project", run["run_id"])

        files = self.project_file_service.list_files("test-project")
        self.assertTrue(any(asset.asset_id == "upload_demo_notes" for asset in files["session_uploads"]))
        self.assertTrue(any(asset.asset_id == "deg_table_v1" for asset in files["data_assets"]))
        self.assertTrue(any(item["category"] == "manifest" for item in files["execution_files"]))
        self.assertTrue(any(item["category"] == "transcript" for item in files["execution_files"]))

    def test_project_files_delete_session_upload_only(self) -> None:
        project_root = self.project_service.project_path("test-project")
        upload_path = project_root / "data" / "uploads" / "upload_demo_notes.txt"
        upload_path.parent.mkdir(parents=True, exist_ok=True)
        upload_path.write_text("demo notes\n", encoding="utf-8")

        graph_store = self.project_service.graph_store("test-project")
        graph = graph_store.load_graph()
        graph.assets.append(
            Asset(
                asset_id="upload_demo_notes",
                asset_type="text",
                title="demo_notes.txt",
                status="candidate",
                path="data/uploads/upload_demo_notes.txt",
                summary="Uploaded notes",
                metadata={"source": "manager_chat_upload"},
            )
        )
        graph_store.save_graph(graph)

        removed = self.project_file_service.delete_session_upload("test-project", "upload_demo_notes")
        self.assertEqual("upload_demo_notes", removed.asset_id)
        self.assertFalse(upload_path.exists())
        files = self.project_file_service.list_files("test-project")
        self.assertFalse(any(asset.asset_id == "upload_demo_notes" for asset in files["session_uploads"]))
        with self.assertRaises(PermissionError):
            self.project_file_service.delete_session_upload("test-project", "deg_table_v1")

    def test_chat_sessions_persist_messages_and_derive_summary(self) -> None:
        session = self.chat_session_service.create_session("test-project")
        self.assertEqual("新会话", session.summary)

        saved = self.chat_session_service.save_session(
            "test-project",
            session.session_id,
            [
                ChatSessionMessage(id="msg_user", role="user", content="帮我重新梳理一下 DEG 之后还缺哪些分析步骤", state="done"),
                ChatSessionMessage(id="msg_manager", role="manager", content="我建议补充功能富集和免疫浸润分析。", state="done"),
            ],
        )

        self.assertEqual("帮我重新梳理一下 DEG 之后还缺哪些分析步骤", saved.summary)
        fetched = self.chat_session_service.get_session("test-project", session.session_id)
        self.assertEqual(2, len(fetched.messages))

        listed = self.chat_session_service.list_sessions("test-project")
        self.assertEqual(session.session_id, listed[0].session_id)
        self.assertEqual(2, listed[0].message_count)

    def test_delete_chat_session_removes_persisted_thread(self) -> None:
        first = self.chat_session_service.create_session("test-project", "第一条")
        second = self.chat_session_service.create_session("test-project", "第二条")

        self.chat_session_service.delete_session("test-project", first.session_id)

        remaining = self.chat_session_service.list_sessions("test-project")
        self.assertEqual([second.session_id], [item.session_id for item in remaining])

    def test_validator_rejects_missing_ids_for_status_and_summary_ops(self) -> None:
        patch = GraphPatch.model_validate(
            {
                "patch_id": "patch_missing_refs",
                "patch_type": "update_card",
                "source": "manager_ai",
                "reason": "test missing refs",
                "ops": [
                    {"op": "set_card_status", "payload": {"card_id": "card_missing", "status": "planned"}},
                    {"op": "set_module_status", "payload": {"module_id": "module_missing", "status": "planned"}},
                    {"op": "update_module_summary", "payload": {"module_id": "module_missing", "summary": "missing"}},
                ],
            }
        )
        result = self.validator.validate_patch("test-project", patch)
        self.assertFalse(result.valid)
        self.assertTrue(any("Card missing: card_missing" in error for error in result.errors))
        self.assertTrue(any("Module missing: module_missing" in error for error in result.errors))

    def test_validator_allows_update_of_entities_created_earlier_in_same_patch(self) -> None:
        patch = GraphPatch.model_validate(
            {
                "patch_id": "patch_create_then_update",
                "patch_type": "add_module",
                "source": "manager_ai",
                "reason": "test create then update",
                "ops": [
                    {
                        "op": "create_module",
                        "payload": {
                            "module_id": "module_tmp",
                            "title": "临时模块",
                            "status": "planned",
                        },
                    },
                    {
                        "op": "update_module",
                        "payload": {
                            "module_id": "module_tmp",
                            "summary": "模块摘要",
                        },
                    },
                    {
                        "op": "create_card",
                        "payload": {
                            "card_id": "card_tmp",
                            "card_type": "module",
                            "title": "临时卡片",
                            "status": "planned",
                            "summary": "初始摘要",
                            "why": "test",
                            "inputs": [],
                            "outputs": [],
                            "key_findings": [],
                            "manager_review": "待执行。",
                            "next_actions": ["开始执行"],
                            "linked_modules": ["module_tmp"],
                            "linked_runs": [],
                            "linked_assets": [],
                        },
                    },
                    {
                        "op": "update_card",
                        "payload": {
                            "card_id": "card_tmp",
                            "summary": "更新后摘要",
                        },
                    },
                ],
            }
        )
        result = self.validator.validate_patch("test-project", patch)
        self.assertTrue(result.valid, result.errors)

    def test_apply_patch_reports_missing_reference_with_op_name(self) -> None:
        patch = GraphPatch.model_validate(
            {
                "patch_id": "patch_missing_runtime_ref",
                "patch_type": "update_card",
                "source": "manager_ai",
                "reason": "test runtime ref failure",
                "ops": [
                    {"op": "set_card_status", "payload": {"card_id": "card_de_analysis", "status": "planned"}},
                ],
            }
        )
        self.project_service.graph_store("test-project").save_cards([])
        original_validate_patch = self.apply.validator.validate_patch
        self.apply.validator.validate_patch = lambda _project_id, _patch: ValidationResult(valid=True, errors=[], warnings=[])
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self.apply.apply_patch("test-project", patch)
        finally:
            self.apply.validator.validate_patch = original_validate_patch
        self.assertIn("set_card_status", str(ctx.exception))
        self.assertIn("card_de_analysis", str(ctx.exception))

    def test_git_commit_failure_does_not_rollback_applied_patch(self) -> None:
        response = self.manager.chat("test-project", ChatRequest(message="客户想增加免疫浸润分析模块"))
        proposal = self.manager.get_proposal("test-project", response.proposal.proposal_id)
        patch_payload = self.project_service.graph_store("test-project").load_patch(proposal.patch_id)

        class BrokenGitService:
            def commit(self, _message: str) -> str:
                raise RuntimeError("git is unavailable")

        original_git_service = self.project_service.git_service
        self.project_service.git_service = lambda _project_id: BrokenGitService()
        try:
            result = self.apply.apply_patch("test-project", GraphPatch.model_validate(patch_payload))
        finally:
            self.project_service.git_service = original_git_service

        snapshot = self.project_service.get_project_snapshot("test-project")
        self.assertIsNone(result.commit_hash)
        self.assertTrue(any("git commit failed" in warning for warning in result.warnings))
        self.assertTrue(any(card.title == "免疫浸润分析" and card.status == "planned" for card in snapshot["cards"]))

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

    def test_asset_flow_and_work_order_are_derived_for_ui(self) -> None:
        asset_flow = self.flow_service.get_asset_flow("test-project")
        self.assertTrue(
            any(
                edge["source_card_id"] == "card_de_analysis"
                and edge["target_card_id"] == "card_enrichment_group"
                and edge["asset_id"] == "deg_table_v1"
                for edge in asset_flow["card_edges"]
            )
        )
        self.assertTrue(
            any(
                edge["source_card_id"] is None
                and edge["target_card_id"] == "card_de_analysis"
                and edge["asset_id"] == "count_matrix_v1"
                for edge in asset_flow["card_edges"]
            )
        )

        work_order = self.flow_service.get_work_order("test-project")
        enrichment = next(item for item in work_order["work_items"] if item["card_id"] == "card_enrichment_group")
        immune = next(item for item in work_order["work_items"] if item["card_id"] == "card_immune_module")
        self.assertIn("card_de_analysis", enrichment["depends_on_card_ids"])
        self.assertTrue(enrichment["can_start"])
        self.assertFalse(immune["can_start"])
        self.assertIn("proposal_not_accepted", immune["block_reasons"])
        self.assertTrue(any("card_enrichment_group" in batch["card_ids"] for batch in work_order["parallel_batches"]))

    def test_single_layer_proposal_returns_asset_sufficiency_metadata(self) -> None:
        self.project_service.create_project(
            project_id="layered-project",
            name="Layered Project",
            current_goal="Build blueprint step by step",
            seed_demo=False,
        )
        store = self.project_service.graph_store("layered-project")
        store.save_graph(
            GraphState(
                assets=[
                    Asset(
                        asset_id="upload_counts_v1",
                        asset_type="count_matrix",
                        title="gene_count_matrix.annotated.txt",
                        status="candidate",
                        path="data/upload/gene_count_matrix.annotated.txt",
                        summary="用户上传的原始计数矩阵。",
                    )
                ]
            )
        )

        manager = ManagerService(self.project_service, planner=AnswerOnlyPlanner())
        response = manager.blueprint_tools.save_patch_proposal(
            "layered-project",
            {
                "title": "新增数据准备层",
                "summary": "先创建数据准备与样本分组模块。",
                "impact_summary": "新增第一层可执行模块。",
                "patch_type": "add_module",
                "reason": "先落第一层",
                "ops": [
                    {
                        "op": "create_module",
                        "payload": {
                            "module_id": "module_data_prep",
                            "title": "数据准备与样本分组",
                            "status": "planned",
                            "summary": "读取上传计数矩阵并生成标准化矩阵和样本元数据。",
                            "depends_on_assets": ["upload_counts_v1"],
                            "expected_outputs": ["normalized_matrix", "sample_metadata"],
                            "linked_cards": ["card_data_prep"],
                        },
                    },
                    {
                        "op": "create_card",
                        "payload": {
                            "card_id": "card_data_prep",
                            "card_type": "module",
                            "title": "数据准备与样本分组",
                            "status": "planned",
                            "summary": "读取原始计数矩阵并生成标准化表达矩阵和样本元数据。",
                            "why": "这是整个流程的起点。",
                            "inputs": [{"label": "原始计数矩阵", "asset_id": "upload_counts_v1", "status": "existing"}],
                            "outputs": [
                                {"label": "标准化表达矩阵", "status": "planned"},
                                {"label": "样本元数据", "status": "planned"},
                            ],
                            "key_findings": [],
                            "manager_review": "先准备上游数据资产。",
                            "next_actions": ["接受提案"],
                            "linked_modules": ["module_data_prep"],
                            "linked_assets": ["upload_counts_v1"],
                        },
                    },
                ],
            },
        )
        sufficiency = response.metadata["proposal_asset_sufficiency"]
        card_entry = next(item for item in sufficiency["cards"] if item["card_id"] == "card_data_prep")
        self.assertTrue(card_entry["ready_now"])
        self.assertTrue(any(item["state"] == "candidate_input_available" for item in card_entry["required_assets"]))
        patch_payload = store.load_patch(response.proposal.patch_id)
        create_card = next(op for op in patch_payload["ops"] if op["op"] == "create_card")
        self.assertTrue(all(item.get("asset_id") for item in create_card["payload"]["outputs"]))
        self.apply.apply_patch("layered-project", GraphPatch.model_validate(patch_payload))
        work_order = self.flow_service.get_work_order("layered-project")
        work_item = next(item for item in work_order["work_items"] if item["card_id"] == "card_data_prep")
        self.assertTrue(work_item["can_start"])

    def test_plan_blueprint_is_read_only_and_reports_layer_dependencies(self) -> None:
        self.project_service.create_project(
            project_id="plan-project",
            name="Plan Project",
            current_goal="Plan complete RNA-seq workflow",
            seed_demo=False,
        )
        store = self.project_service.graph_store("plan-project")
        store.save_graph(
            GraphState(
                assets=[
                    Asset(
                        asset_id="upload_counts_v1",
                        asset_type="count_matrix",
                        title="gene_count_matrix.annotated.txt",
                        status="candidate",
                        path="data/upload/gene_count_matrix.annotated.txt",
                        summary="用户上传的原始计数矩阵。",
                    )
                ]
            )
        )

        manager = ManagerService(self.project_service, planner=AnswerOnlyPlanner())
        before = store.load_proposals()
        plan = manager.blueprint_tools.plan_blueprint(
            "plan-project",
            {
                "objective": "从原始计数矩阵构建完整 RNA-seq 分析蓝图",
                "assumptions": ["前 3 个样本为 OAA，后 3 个样本为 2D-gal"],
                "steps": [
                    {
                        "step_id": "step_data_prep",
                        "title": "数据准备与样本分组",
                        "specialist": "Data Prep Specialist",
                        "module_id": "module_data_prep",
                        "card_id": "card_data_prep",
                        "input_assets": [{"label": "原始计数矩阵", "asset_id": "upload_counts_v1"}],
                        "output_assets": [
                            {"label": "标准化表达矩阵", "asset_id": "asset_normalized_matrix_v1"},
                            {"label": "样本元数据", "asset_id": "asset_sample_metadata_v1"},
                        ],
                    },
                    {
                        "step_id": "step_de",
                        "title": "差异表达分析",
                        "specialist": "DEG Specialist",
                        "module_id": "module_de_analysis",
                        "card_id": "card_de_analysis",
                        "depends_on_step_ids": ["step_data_prep"],
                        "input_assets": [
                            {"label": "标准化表达矩阵", "asset_id": "asset_normalized_matrix_v1"},
                            {"label": "样本元数据", "asset_id": "asset_sample_metadata_v1"},
                        ],
                        "output_assets": [{"label": "DEG 结果表", "asset_id": "asset_deg_table_v1"}],
                    },
                ],
            },
        )
        after = store.load_proposals()

        self.assertEqual(before, after)
        self.assertEqual("none", plan["write_effect"])
        self.assertEqual("step_data_prep", plan["next_executable_step_id"])
        first_step = next(item for item in plan["steps"] if item["step_id"] == "step_data_prep")
        second_step = next(item for item in plan["steps"] if item["step_id"] == "step_de")
        self.assertTrue(first_step["is_currently_executable"])
        self.assertFalse(second_step["is_currently_executable"])
        self.assertTrue(any(item["state"] == "candidate_input_available" for item in first_step["asset_sufficiency"]))
        self.assertTrue(any(item["state"] == "planned_from_workflow" for item in second_step["asset_sufficiency"]))

    def test_review_blueprint_plan_checks_structured_assets_only(self) -> None:
        self.project_service.create_project(
            project_id="review-plan-project",
            name="Review Plan Project",
            current_goal="Review complete RNA-seq workflow",
            seed_demo=False,
        )
        store = self.project_service.graph_store("review-plan-project")
        store.save_graph(
            GraphState(
                assets=[
                    Asset(
                        asset_id="upload_counts_v1",
                        asset_type="count_matrix",
                        title="gene_count_matrix.annotated.txt",
                        status="candidate",
                        path="data/upload/gene_count_matrix.annotated.txt",
                        summary="用户上传的原始计数矩阵。",
                    )
                ]
            )
        )
        manager = ManagerService(self.project_service, planner=AnswerOnlyPlanner())
        before = store.load_proposals()

        review = manager.blueprint_tools.review_blueprint_plan(
            "review-plan-project",
            {
                "plan": {
                    "objective": "从原始计数矩阵构建完整 RNA-seq 分析蓝图",
                    "steps": [
                        {
                            "step_id": "step_data_prep",
                            "title": "数据准备与样本分组",
                            "module_id": "module_data_prep",
                            "card_id": "card_data_prep",
                            "input_assets": [{"label": "原始计数矩阵", "asset_id": "upload_counts_v1"}],
                            "output_assets": [{"label": "标准化表达矩阵", "asset_id": "asset_normalized_matrix_v1"}],
                        },
                        {
                            "step_id": "step_de",
                            "title": "差异表达分析",
                            "module_id": "module_de_analysis",
                            "card_id": "card_de_analysis",
                            "depends_on_step_ids": ["step_data_prep"],
                            "input_assets": [{"label": "标准化表达矩阵", "asset_id": "asset_normalized_matrix_v1"}],
                            "output_assets": [{"label": "DEG 结果表", "asset_id": "asset_deg_table_v1"}],
                        },
                    ],
                }
            },
        )
        after = store.load_proposals()

        self.assertEqual(before, after)
        self.assertTrue(review["approved"])
        self.assertEqual("step_data_prep", review["next_executable_step_id"])
        first_step = next(item for item in review["step_reviews"] if item["step_id"] == "step_data_prep")
        second_step = next(item for item in review["step_reviews"] if item["step_id"] == "step_de")
        self.assertTrue(first_step["is_currently_executable"])
        self.assertFalse(second_step["is_currently_executable"])
        self.assertIn("waiting for planned input", second_step["block_reasons"][0])

        invalid_review = manager.blueprint_tools.review_blueprint_plan(
            "review-plan-project",
            {
                "plan": {
                    "objective": "缺少结构化 asset id 的错误计划",
                    "steps": [
                        {
                            "step_id": "step_bad",
                            "title": "差异表达分析",
                            "module_id": "module_de_analysis",
                            "card_id": "card_de_analysis",
                            "input_assets": [{"label": "标准化表达矩阵"}],
                            "output_assets": [{"label": "DEG 结果表", "asset_id": "asset_deg_table_v1"}],
                        }
                    ],
                }
            },
        )
        self.assertFalse(invalid_review["approved"])
        self.assertTrue(any(item["code"] == "missing_input_asset_id" for item in invalid_review["errors"]))

    def test_stale_proposal_ids_return_planning_errors(self) -> None:
        manager = ManagerService(self.project_service, planner=AnswerOnlyPlanner())
        with self.assertRaisesRegex(ManagerPlanningError, "Proposal not found"):
            manager.modify_proposal("test-project", "proposal_missing", ChatRequest(message="修改不存在的 proposal"))
        with self.assertRaisesRegex(ManagerPlanningError, "Proposal not found"):
            manager.replace_proposal_with_draft(
                "test-project",
                "proposal_missing",
                ManagerPlanDraft(
                    response_type="proposal",
                    message="missing",
                    title="missing",
                    summary="missing",
                    impact_summary="missing",
                    patch_type="update_card",
                    reason="test",
                    ops=[],
                ),
            )

    def test_unknown_update_targets_are_planning_errors(self) -> None:
        manager = ManagerService(self.project_service, planner=AnswerOnlyPlanner())
        with self.assertRaisesRegex(ManagerPlanningError, "unknown card"):
            manager.blueprint_tools.save_patch_proposal(
                "test-project",
                {
                    "title": "更新不存在卡片",
                    "summary": "更新不存在卡片。",
                    "impact_summary": "应返回可读错误。",
                    "patch_type": "update_card",
                    "reason": "test",
                    "ops": [{"op": "update_card", "payload": {"card_id": "card_missing", "summary": "x"}}],
                },
            )
        with self.assertRaisesRegex(ManagerPlanningError, "unknown module"):
            manager.blueprint_tools.save_patch_proposal(
                "test-project",
                {
                    "title": "更新不存在模块",
                    "summary": "更新不存在模块。",
                    "impact_summary": "应返回可读错误。",
                    "patch_type": "update_card",
                    "reason": "test",
                    "ops": [{"op": "update_module", "payload": {"module_id": "module_missing", "summary": "x"}}],
                },
            )

    def test_cycle_in_proposal_dependencies_is_rejected(self) -> None:
        manager = ManagerService(self.project_service, planner=AnswerOnlyPlanner())
        with self.assertRaisesRegex(ManagerPlanningError, "Dependency cycle detected"):
            manager.blueprint_tools.save_patch_proposal(
                "test-project",
                {
                    "title": "循环依赖测试",
                    "summary": "两个 card 互相依赖对方产物。",
                    "impact_summary": "应被拒绝。",
                    "patch_type": "add_module",
                    "reason": "test",
                    "ops": [
                        {
                            "op": "create_card",
                            "payload": {
                                "card_id": "card_cycle_a",
                                "card_type": "module",
                                "title": "Cycle A",
                                "status": "planned",
                                "summary": "A",
                                "why": "test",
                                "inputs": [{"label": "asset b", "asset_id": "asset_cycle_b"}],
                                "outputs": [{"label": "asset a", "asset_id": "asset_cycle_a"}],
                                "key_findings": [],
                                "manager_review": "test",
                                "next_actions": [],
                                "linked_modules": [],
                                "linked_assets": [],
                            },
                        },
                        {
                            "op": "create_card",
                            "payload": {
                                "card_id": "card_cycle_b",
                                "card_type": "module",
                                "title": "Cycle B",
                                "status": "planned",
                                "summary": "B",
                                "why": "test",
                                "inputs": [{"label": "asset a", "asset_id": "asset_cycle_a"}],
                                "outputs": [{"label": "asset b", "asset_id": "asset_cycle_b"}],
                                "key_findings": [],
                                "manager_review": "test",
                                "next_actions": [],
                                "linked_modules": [],
                                "linked_assets": [],
                            },
                        },
                    ],
                },
            )

    def test_go_spec_does_not_match_good_or_going(self) -> None:
        manager = ManagerService(self.project_service, planner=AnswerOnlyPlanner())
        self.assertIsNone(manager.tool_layer._resolve_spec("please create a good module"))
        self.assertIsNone(manager.tool_layer._resolve_spec("we are going to discuss the plan"))

    def test_multi_layer_proposal_is_rejected_until_split_by_layer(self) -> None:
        self.project_service.create_project(
            project_id="layered-project-multi",
            name="Layered Multi Project",
            current_goal="Build blueprint step by step",
            seed_demo=False,
        )
        store = self.project_service.graph_store("layered-project-multi")
        store.save_graph(
            GraphState(
                assets=[
                    Asset(
                        asset_id="upload_counts_v1",
                        asset_type="count_matrix",
                        title="gene_count_matrix.annotated.txt",
                        status="candidate",
                        path="data/upload/gene_count_matrix.annotated.txt",
                        summary="用户上传的原始计数矩阵。",
                    )
                ]
            )
        )

        manager = ManagerService(self.project_service, planner=AnswerOnlyPlanner())
        with self.assertRaises(ManagerPlanningError) as ctx:
            manager.blueprint_tools.save_patch_proposal(
                "layered-project-multi",
                {
                    "title": "一次性新增完整 RNA-seq 流程",
                    "summary": "同时新增数据准备和差异表达模块。",
                    "impact_summary": "尝试把上下游层一次写入。",
                    "patch_type": "add_module",
                    "reason": "测试分层校验",
                    "ops": [
                        {
                            "op": "create_module",
                            "payload": {
                                "module_id": "module_data_prep",
                                "title": "数据准备与样本分组",
                                "status": "planned",
                                "summary": "读取上传计数矩阵并生成标准化矩阵和样本元数据。",
                                "depends_on_assets": ["upload_counts_v1"],
                                "expected_outputs": ["normalized_matrix", "sample_metadata"],
                                "linked_cards": ["card_data_prep"],
                            },
                        },
                        {
                            "op": "create_card",
                            "payload": {
                                "card_id": "card_data_prep",
                                "card_type": "module",
                                "title": "数据准备与样本分组",
                                "status": "planned",
                                "summary": "读取原始计数矩阵并生成标准化表达矩阵和样本元数据。",
                                "why": "这是整个流程的起点。",
                                "inputs": [{"label": "原始计数矩阵", "asset_id": "upload_counts_v1", "status": "existing"}],
                                "outputs": [
                                    {"label": "标准化表达矩阵", "status": "planned"},
                                    {"label": "样本元数据", "status": "planned"},
                                ],
                                "key_findings": [],
                                "manager_review": "先准备上游数据资产。",
                                "next_actions": ["接受提案"],
                                "linked_modules": ["module_data_prep"],
                                "linked_assets": ["upload_counts_v1"],
                            },
                        },
                        {
                            "op": "create_module",
                            "payload": {
                                "module_id": "module_de_analysis",
                                "title": "差异表达分析",
                                "status": "planned",
                                "summary": "使用标准化矩阵和样本元数据做差异表达分析。",
                                "linked_cards": ["card_de_analysis"],
                            },
                        },
                        {
                            "op": "create_card",
                            "payload": {
                                "card_id": "card_de_analysis",
                                "card_type": "module",
                                "title": "差异表达分析",
                                "status": "planned",
                                "summary": "基于标准化表达矩阵执行 DESeq2 分析。",
                                "why": "识别差异基因。",
                                "inputs": [
                                    {"label": "标准化表达矩阵", "status": "planned"},
                                    {"label": "样本元数据", "status": "planned"},
                                ],
                                "outputs": [{"label": "DEG 结果表", "status": "planned"}],
                                "key_findings": [],
                                "manager_review": "等待上游完成。",
                                "next_actions": ["接受提案"],
                                "linked_modules": ["module_de_analysis"],
                                "linked_assets": [],
                            },
                        },
                    ],
                },
            )
        self.assertIn("downstream layers", str(ctx.exception))

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
