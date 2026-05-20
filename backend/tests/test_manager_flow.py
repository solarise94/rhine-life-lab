from pathlib import Path
import shutil
import tempfile
import time
import unittest

from app.core.config import get_settings
from app.models.cards import Card
from app.models.chat import ChatRequest, ChatSessionMessage
from app.models.graph import Asset, GraphState
from app.models.patches import GraphPatch, ValidationResult
from app.services.chat_session_service import ChatSessionService
from app.services.flow_service import FlowService
from app.services.manager_planner import DeepSeekManagerPlanner, ManagerPlanningError
from app.services.manager_service import ManagerService
from app.services.manifest_service import ManifestService
from app.services.patch_apply import PatchApplyService
from app.services.patch_validator import PatchValidator
from app.services.project_file_service import ProjectFileService
from app.services.project_service import ProjectService
from app.services.result_asset_service import ResultAssetService
from app.services.runtime_approval_service import RuntimeApprovalService
from app.services.worker_service import WorkerService


class AnswerOnlyPlanner:
    def agent_turn(self, messages: list[dict], tools: list[dict]) -> dict:
        return {"content": [{"type": "text", "text": "这是 DeepSeek 普通聊天回复。"}]}


class FailingPlanner:
    def agent_turn(self, messages: list[dict], tools: list[dict]) -> dict:
        raise ManagerPlanningError("agent llm failed")


class StubGitService:
    def commit(self, _message: str) -> str:
        return "test-commit"

    def log(self, limit: int = 20) -> list[dict[str, str]]:
        return []

    def head(self) -> str:
        return "test-commit"


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
        self.project_service.git_service = lambda _project_id: StubGitService()
        self.manager = ManagerService(self.project_service, planner=AnswerOnlyPlanner())
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

    def test_plain_chat_calls_llm_answer_and_does_not_mutate_cards(self) -> None:
        before_cards = self.project_service.graph_store("test-project").load_cards()

        response = self.manager.chat("test-project", ChatRequest(message="你好，先帮我解释一下现在项目里有哪些分析模块"))

        after_cards = self.project_service.graph_store("test-project").load_cards()
        self.assertIsNone(response.proposal)
        self.assertEqual([card.model_dump() for card in before_cards], [card.model_dump() for card in after_cards])
        self.assertEqual("这是 DeepSeek 普通聊天回复。", response.message)

    def test_plain_chat_llm_error_is_not_hidden(self) -> None:
        manager = ManagerService(self.project_service, planner=FailingPlanner())
        with self.assertRaises(ManagerPlanningError):
            manager.chat("test-project", ChatRequest(message="hi"))

    def test_analysis_suggestion_question_stays_plain_chat(self) -> None:
        response = self.manager.chat("test-project", ChatRequest(message="帮我看看我现在的分析流程，有没有还可以补充的"))
        self.assertIsNone(response.proposal)
        self.assertEqual("这是 DeepSeek 普通聊天回复。", response.message)

    def test_get_project_context_exposes_cards_and_graph(self) -> None:
        context = self.manager.blueprint_tools.get_project_context("test-project")
        self.assertEqual("test-project", context["project"]["project_id"])
        self.assertTrue(any(card["card_id"] == "card_de_analysis" for card in context["cards"]))
        self.assertTrue(any(module["module_id"] == "module_de_analysis" for module in context["modules"]))
        self.assertTrue(any(asset["asset_id"] == "deg_table_v1" for asset in context["assets"]))

    def test_list_data_assets_exposes_materialized_and_planned_timeline(self) -> None:
        self.manager.blueprint_tools.create_card(
            "test-project",
            {
                "card_id": "card_go_bp",
                "title": "GO BP 富集分析",
                "summary": "基于 DEG 结果进行 GO BP 富集。",
                "inputs": [{"label": "DEG table", "asset_id": "deg_table_v1"}],
                "outputs": [{"label": "GO BP enrichment", "asset_id": "go_bp_result"}],
            },
        )

        listing = self.manager.blueprint_tools.list_data_assets("test-project")
        self.assertTrue(any(asset["asset_id"] == "deg_table_v1" for asset in listing["materialized_assets"]))
        self.assertTrue(any(asset["planned"] and asset["producer_card_id"] == "card_go_bp" for asset in listing["planned_assets"]))
        enrichment = next(card for card in listing["cards"] if card["card_id"] == "card_enrichment_group")
        self.assertEqual(2, enrichment["step"])
        self.assertEqual(["deg_table_v1", "ranked_gene_list_v1"], enrichment["required_asset_ids"])
        self.assertEqual({"audit_card_tools": False}, listing["tool_policy"])

    def test_create_card_assigns_step_and_output_asset_ids(self) -> None:
        result = self.manager.blueprint_tools.create_card(
            "test-project",
            {
                "card_id": "card_go_bp",
                "title": "GO BP 富集分析",
                "summary": "基于 DEG 结果进行 GO BP 富集。",
                "why": "解释差异基因的生物过程。",
                "inputs": [{"label": "DEG table", "asset_id": "deg_table_v1"}],
                "outputs": [{"label": "GO BP enrichment"}],
                "linked_modules": ["module_group_enrichment"],
                "linked_assets": ["deg_table_v1"],
            },
        )

        card = Card.model_validate(result["card"])
        self.assertEqual("card_go_bp", card.card_id)
        self.assertEqual(2, card.step)
        self.assertTrue(card.outputs[0].asset_id)
        snapshot = self.project_service.get_project_snapshot("test-project")
        self.assertTrue(any(item.card_id == "card_go_bp" for item in snapshot["cards"]))

    def test_create_card_rejects_missing_input_with_retryable_error(self) -> None:
        with self.assertRaisesRegex(ManagerPlanningError, "Input asset missing_deg is missing"):
            self.manager.blueprint_tools.create_card(
                "test-project",
                {
                    "card_id": "card_missing_input",
                    "title": "Missing Input",
                    "summary": "Should fail because input is unknown.",
                    "inputs": [{"label": "missing", "asset_id": "missing_deg"}],
                    "outputs": [{"label": "result", "asset_id": "missing_input_result"}],
                },
            )

    def test_create_card_rejects_step_earlier_than_input_asset_timeline(self) -> None:
        self.manager.blueprint_tools.create_card(
            "test-project",
            {
                "card_id": "card_qc",
                "title": "QC",
                "summary": "Produce filtered counts.",
                "inputs": [{"label": "counts", "asset_id": "count_matrix_v1"}],
                "outputs": [{"label": "filtered counts", "asset_id": "filtered_counts"}],
            },
        )

        with self.assertRaisesRegex(ManagerPlanningError, "Increase step to at least 2"):
            self.manager.blueprint_tools.create_card(
                "test-project",
                {
                    "card_id": "card_downstream_too_early",
                    "title": "Downstream",
                    "summary": "Consumes a planned upstream output.",
                    "step": 1,
                    "inputs": [{"label": "filtered counts", "asset_id": "filtered_counts"}],
                    "outputs": [{"label": "downstream result", "asset_id": "downstream_result"}],
                },
            )

    def test_update_card_changes_content_and_revalidates_step(self) -> None:
        self.manager.blueprint_tools.create_card(
            "test-project",
            {
                "card_id": "card_qc",
                "title": "QC",
                "summary": "Produce filtered counts.",
                "inputs": [{"label": "counts", "asset_id": "count_matrix_v1"}],
                "outputs": [{"label": "filtered counts", "asset_id": "filtered_counts"}],
            },
        )
        result = self.manager.blueprint_tools.update_card(
            "test-project",
            {
                "card_id": "card_qc",
                "title": "QC and filtering",
                "summary": "Filter low-quality samples and features.",
            },
        )
        self.assertEqual("QC and filtering", result["card"]["title"])

        with self.assertRaisesRegex(ManagerPlanningError, "Increase step to at least 2"):
            self.manager.blueprint_tools.update_card(
                "test-project",
                {
                    "card_id": "card_qc",
                    "inputs": [{"label": "own output", "asset_id": "filtered_counts"}],
                    "step": 1,
                },
            )

    def test_delete_card_marks_card_cancelled(self) -> None:
        result = self.manager.blueprint_tools.delete_card(
            "test-project",
            {"card_id": "card_immune_module", "reason": "用户暂时不做免疫浸润分析"},
        )
        self.assertEqual("cancelled", result["card"]["status"])
        self.assertIn("用户暂时不做免疫浸润分析", result["card"]["manager_review"])

        snapshot = self.project_service.get_project_snapshot("test-project")
        card = next(item for item in snapshot["cards"] if item.card_id == "card_immune_module")
        self.assertEqual("cancelled", card.status)

    def test_create_card_rejects_duplicate_card_id_and_output_asset(self) -> None:
        with self.assertRaisesRegex(ManagerPlanningError, "Duplicate card_id"):
            self.manager.blueprint_tools.create_card(
                "test-project",
                {
                    "card_id": "card_de_analysis",
                    "title": "Duplicate",
                    "summary": "Duplicate card id.",
                    "inputs": [{"label": "counts", "asset_id": "count_matrix_v1"}],
                    "outputs": [{"label": "duplicate", "asset_id": "duplicate_output"}],
                },
            )

        with self.assertRaisesRegex(ManagerPlanningError, "already exists as a materialized asset"):
            self.manager.blueprint_tools.create_card(
                "test-project",
                {
                    "card_id": "card_duplicate_deg",
                    "title": "Duplicate DEG",
                    "summary": "Duplicate a planned or materialized output.",
                    "inputs": [{"label": "counts", "asset_id": "count_matrix_v1"}],
                    "outputs": [{"label": "DEG", "asset_id": "deg_table_v1"}],
                },
            )

        self.manager.blueprint_tools.create_card(
            "test-project",
            {
                "card_id": "card_first_planned_output",
                "title": "First planned output",
                "summary": "Creates a planned output asset.",
                "inputs": [{"label": "counts", "asset_id": "count_matrix_v1"}],
                "outputs": [{"label": "planned result", "asset_id": "planned_duplicate_result"}],
            },
        )
        with self.assertRaisesRegex(ManagerPlanningError, "Duplicate planned output asset_id values"):
            self.manager.blueprint_tools.create_card(
                "test-project",
                {
                    "card_id": "card_second_planned_output",
                    "title": "Second planned output",
                    "summary": "Tries to create the same planned output asset.",
                    "inputs": [{"label": "counts", "asset_id": "count_matrix_v1"}],
                    "outputs": [{"label": "planned result", "asset_id": "planned_duplicate_result"}],
                },
            )

    def test_multi_card_timeline_and_dag_are_derived_from_card_outputs(self) -> None:
        self.project_service.create_project(
            project_id="timeline-project",
            name="Timeline Project",
            current_goal="Build a layered workflow",
            seed_demo=False,
        )
        store = self.project_service.graph_store("timeline-project")
        store.save_graph(
            GraphState(
                assets=[
                    Asset(
                        asset_id="raw_counts",
                        asset_type="count_matrix",
                        title="raw_counts.tsv",
                        status="candidate",
                        path="data/uploads/raw_counts.tsv",
                        summary="Uploaded raw count matrix.",
                    )
                ]
            )
        )
        manager = ManagerService(self.project_service, planner=AnswerOnlyPlanner())

        manager.blueprint_tools.create_card(
            "timeline-project",
            {
                "card_id": "card_qc",
                "title": "数据校验与过滤",
                "summary": "过滤低质量样本并输出 filtered counts。",
                "inputs": [{"label": "Raw counts", "asset_id": "raw_counts"}],
                "outputs": [{"label": "Filtered counts", "asset_id": "filtered_counts"}],
            },
        )
        manager.blueprint_tools.create_card(
            "timeline-project",
            {
                "card_id": "card_deseq2",
                "title": "DESeq2 差异分析",
                "summary": "基于 filtered counts 输出 DEG 结果。",
                "inputs": [{"label": "Filtered counts", "asset_id": "filtered_counts"}],
                "outputs": [{"label": "DEG results", "asset_id": "deseq2_results"}],
            },
        )
        manager.blueprint_tools.create_card(
            "timeline-project",
            {
                "card_id": "card_kegg",
                "title": "KEGG 通路富集",
                "summary": "基于 DEG results 做 KEGG 富集。",
                "inputs": [{"label": "DEG results", "asset_id": "deseq2_results"}],
                "outputs": [{"label": "KEGG results", "asset_id": "kegg_results"}],
            },
        )

        listing = manager.blueprint_tools.list_data_assets("timeline-project")
        steps = {card["card_id"]: card["step"] for card in listing["cards"]}
        self.assertEqual({"card_qc": 1, "card_deseq2": 2, "card_kegg": 3}, steps)

        flow = FlowService(self.project_service).get_asset_flow("timeline-project")
        self.assertTrue(
            any(
                edge["source_card_id"] == "card_qc"
                and edge["target_card_id"] == "card_deseq2"
                and edge["asset_id"] == "filtered_counts"
                for edge in flow["card_edges"]
            )
        )
        self.assertTrue(
            any(
                edge["source_card_id"] == "card_deseq2"
                and edge["target_card_id"] == "card_kegg"
                and edge["asset_id"] == "deseq2_results"
                for edge in flow["card_edges"]
            )
        )

        work_order = FlowService(self.project_service).get_work_order("timeline-project")
        deseq2 = next(item for item in work_order["work_items"] if item["card_id"] == "card_deseq2")
        kegg = next(item for item in work_order["work_items"] if item["card_id"] == "card_kegg")
        self.assertEqual(["card_qc"], deseq2["depends_on_card_ids"])
        self.assertEqual(["card_deseq2"], kegg["depends_on_card_ids"])
        self.assertFalse(deseq2["can_start"])
        self.assertIn("upstream_cards_not_accepted", deseq2["block_reasons"])

    def test_read_result_asset_tool_returns_preview(self) -> None:
        detail = self.manager.blueprint_tools.read_result_asset("test-project", "deg_table_v1")
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

    def test_tool_policy_can_enable_card_tool_audit(self) -> None:
        policy = self.manager.blueprint_tools.set_tool_policy("test-project", {"audit_card_tools": True})
        self.assertEqual({"audit_card_tools": True}, policy["tool_policy"])

        self.manager.blueprint_tools.create_card(
            "test-project",
            {
                "card_id": "card_audited",
                "title": "Audited Card",
                "summary": "Audit should be recorded when enabled.",
                "inputs": [{"label": "counts", "asset_id": "count_matrix_v1"}],
                "outputs": [{"label": "result", "asset_id": "audited_result"}],
            },
        )

        graph = self.project_service.graph_store("test-project").load_graph()
        audit_log = graph.metadata.get("card_tool_audit")
        self.assertEqual("create_card", audit_log[-1]["action"])
        self.assertEqual("card_audited", audit_log[-1]["card_id"])

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

    def test_apply_patch_keeps_mutation_when_git_commit_fails(self) -> None:
        patch = GraphPatch.model_validate(
            {
                "patch_id": "patch_create_direct_card",
                "patch_type": "add_module",
                "source": "manager_ai",
                "reason": "test git failure",
                "ops": [
                    {
                        "op": "create_card",
                        "payload": {
                            "card_id": "card_direct_patch",
                            "card_type": "module",
                            "title": "Direct Patch Card",
                            "status": "planned",
                            "summary": "Card created by patch apply.",
                            "inputs": [],
                            "outputs": [],
                        },
                    }
                ],
            }
        )

        class BrokenGitService:
            def commit(self, _message: str) -> str:
                raise RuntimeError("git is unavailable")

        original_git_service = self.project_service.git_service
        self.project_service.git_service = lambda _project_id: BrokenGitService()
        try:
            result = self.apply.apply_patch("test-project", patch)
        finally:
            self.project_service.git_service = original_git_service

        snapshot = self.project_service.get_project_snapshot("test-project")
        self.assertIsNone(result.commit_hash)
        self.assertTrue(any("git commit failed" in warning for warning in result.warnings))
        self.assertTrue(any(card.card_id == "card_direct_patch" for card in snapshot["cards"]))

    def test_go_spec_does_not_match_good_or_going(self) -> None:
        self.assertIsNone(self.manager.tool_layer._resolve_spec("please create a good module"))
        self.assertIsNone(self.manager.tool_layer._resolve_spec("we are going to discuss the plan"))

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
